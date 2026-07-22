"""YOLOv8n + Supervision ByteTrack adapter for empirical parking footage."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Mapping, Sequence

from empirical_pipeline import (
    KinematicPoint,
    analyze_observation_frames,
    compute_validation_metrics,
    export_maneuvers_csv,
    generate_comparison_figure,
    pair_ttc,
    point_in_polygon,
    project_point,
    segment_intersects_bbox,
)


@dataclass(frozen=True)
class LotConfig:
    source_id: str
    homography: tuple[tuple[float, float, float], ...]
    aisle_polygon_m: tuple[tuple[float, float], ...]
    stall_polygon_m: tuple[tuple[float, float], ...]
    stall_inward_vector: tuple[float, float]
    aisle_center_m: tuple[float, float]
    confidence: float = 0.25
    frame_stride: int = 1
    adjacent_distance_m: float = 5.5

    @classmethod
    def from_json(cls, path: str | Path) -> "LotConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(
            source_id=str(raw["source_id"]),
            homography=tuple(tuple(float(v) for v in row) for row in raw["homography"]),
            aisle_polygon_m=_polygon(raw["aisle_polygon_m"]),
            stall_polygon_m=_polygon(raw["stall_polygon_m"]),
            stall_inward_vector=_point(raw["stall_inward_vector"]),
            aisle_center_m=_point(raw["aisle_center_m"]),
            confidence=float(raw.get("confidence", 0.25)),
            frame_stride=int(raw.get("frame_stride", 1)),
            adjacent_distance_m=float(raw.get("adjacent_distance_m", 5.5)),
        )

    def __post_init__(self) -> None:
        if len(self.homography) != 3 or any(len(row) != 3 for row in self.homography):
            raise ValueError("homography must be 3x3")
        if self.frame_stride < 1:
            raise ValueError("frame_stride must be at least 1")
        if not 0 < self.confidence <= 1:
            raise ValueError("confidence must be in (0, 1]")


def _point(values: Sequence[object]) -> tuple[float, float]:
    if len(values) != 2:
        raise ValueError("point must have two coordinates")
    return float(values[0]), float(values[1])


def _polygon(values: Sequence[Sequence[object]]) -> tuple[tuple[float, float], ...]:
    polygon = tuple(_point(value) for value in values)
    if len(polygon) < 3:
        raise ValueError("polygon must have at least three points")
    return polygon


def _velocity(
    history: deque[tuple[float, tuple[float, float]]],
) -> tuple[tuple[float, float], float]:
    if len(history) < 2:
        return (0.0, 0.0), 0.0
    (first_time, first), (last_time, last) = history[0], history[-1]
    elapsed = last_time - first_time
    if elapsed <= 1e-6:
        return (0.0, 0.0), 0.0
    vector = ((last[0] - first[0]) / elapsed, (last[1] - first[1]) / elapsed)
    speed = (vector[0] ** 2 + vector[1] ** 2) ** 0.5
    return vector, speed


def _ground_bbox(
    xyxy: Sequence[float], homography: Sequence[Sequence[float]]
) -> tuple[float, float, float, float]:
    x1, y1, x2, y2 = (float(value) for value in xyxy)
    points = [
        project_point((x1, y1), homography),
        project_point((x2, y1), homography),
        project_point((x1, y2), homography),
        project_point((x2, y2), homography),
    ]
    return (
        min(point[0] for point in points),
        min(point[1] for point in points),
        max(point[0] for point in points),
        max(point[1] for point in points),
    )


def infer_observation_frames(
    source: str,
    config: LotConfig,
    model_path: str = "yolov8n.pt",
    max_frames: int | None = None,
) -> Iterator[dict[str, object]]:
    """Yield normalized, calibrated observations without retaining video frames."""
    try:
        import cv2
        import numpy as np
        import supervision as sv
        from ultralytics import YOLO
    except ImportError as exc:  # pragma: no cover - installation-specific
        raise RuntimeError("install dependencies from requirements-empirical.txt") from exc

    capture = cv2.VideoCapture(source)
    if not capture.isOpened():
        raise RuntimeError(f"unable to open video/RTSP source: {source}")
    fps = float(capture.get(cv2.CAP_PROP_FPS)) or 15.0
    tracker = sv.ByteTrack(frame_rate=round(fps))
    model = YOLO(model_path)
    history: dict[int, deque[tuple[float, tuple[float, float]]]] = {}
    last_heading: dict[int, tuple[float, float]] = {}
    frame_index = 0
    emitted = 0
    started_at = datetime.now(timezone.utc)
    try:
        while True:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % config.frame_stride:
                frame_index += 1
                continue
            timestamp_s = frame_index / fps
            expired = [
                key for key, samples in history.items()
                if samples and timestamp_s - samples[-1][0] > 300.0
            ]
            for key in expired:
                history.pop(key, None)
                last_heading.pop(key, None)
            result = model.predict(frame, conf=config.confidence, verbose=False)[0]
            detections = sv.Detections.from_ultralytics(result)
            if len(detections):
                relevant = np.isin(detections.class_id, [0, 2, 5, 7])
                detections = detections[relevant]
                detections = tracker.update_with_detections(detections)
            tracked: list[dict[str, object]] = []
            for index in range(len(detections)):
                tracker_id = detections.tracker_id[index]
                if tracker_id is None:
                    continue
                xyxy = detections.xyxy[index]
                class_id = int(detections.class_id[index])
                image_ground = ((float(xyxy[0]) + float(xyxy[2])) / 2.0, float(xyxy[3]))
                position = project_point(image_ground, config.homography)
                key = int(tracker_id)
                samples = history.setdefault(key, deque(maxlen=5))
                samples.append((timestamp_s, position))
                velocity, speed = _velocity(samples)
                if speed > 0.05:
                    heading = (velocity[0] / speed, velocity[1] / speed)
                    last_heading[key] = heading
                else:
                    heading = last_heading.get(key, config.stall_inward_vector)
                tracked.append(
                    {
                        "track_id": key,
                        "class_id": class_id,
                        "position": position,
                        "velocity": velocity,
                        "speed_mps": speed,
                        "heading": heading,
                        "bbox_m": _ground_bbox(xyxy, config.homography),
                    }
                )

            pedestrians = [item for item in tracked if item["class_id"] == 0]
            vehicles = [item for item in tracked if item["class_id"] in {2, 5, 7}]
            observations: list[dict[str, object]] = []
            for vehicle in vehicles:
                position = vehicle["position"]
                in_stall = point_in_polygon(position, config.stall_polygon_m)
                in_aisle = point_in_polygon(position, config.aisle_polygon_m)
                pedestrian_ttcs: dict[int, float | None] = {}
                for pedestrian in pedestrians:
                    pedestrian_ttcs[int(pedestrian["track_id"])] = pair_ttc(
                        KinematicPoint(position, vehicle["velocity"]),
                        KinematicPoint(pedestrian["position"], pedestrian["velocity"]),
                    )
                adjacent = []
                for other in vehicles:
                    if other["track_id"] == vehicle["track_id"]:
                        continue
                    distance = (
                        (other["position"][0] - position[0]) ** 2
                        + (other["position"][1] - position[1]) ** 2
                    ) ** 0.5
                    if distance <= config.adjacent_distance_m:
                        adjacent.append(other)
                los_blocked = any(
                    segment_intersects_bbox(position, config.aisle_center_m, other["bbox_m"])
                    for other in adjacent
                )
                suv_adjacent = any(
                    other["class_id"] in {5, 7}
                    or max(
                        other["bbox_m"][2] - other["bbox_m"][0],
                        other["bbox_m"][3] - other["bbox_m"][1],
                    )
                    >= 4.8
                    for other in adjacent
                )
                observations.append(
                    {
                        "track_id": vehicle["track_id"],
                        "speed_mps": round(float(vehicle["speed_mps"]), 4),
                        "in_stall": in_stall,
                        "in_aisle": in_aisle,
                        "heading": vehicle["heading"],
                        "los_blocked": los_blocked,
                        "suv_adjacent": suv_adjacent,
                        "pedestrian_ttcs": pedestrian_ttcs,
                    }
                )
            yield {
                "timestamp_s": timestamp_s,
                "timestamp": (started_at + timedelta(seconds=timestamp_s)).isoformat(),
                "vehicles": observations,
            }
            emitted += 1
            frame_index += 1
            if max_frames is not None and emitted >= max_frames:
                break
    finally:
        capture.release()


def _tap_jsonl(
    frames: Iterator[Mapping[str, object]], path: str | Path
) -> Iterator[Mapping[str, object]]:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8") as handle:
        for frame in frames:
            handle.write(json.dumps(frame) + "\n")
            yield frame


def _read_jsonl(path: str | Path) -> Iterator[Mapping[str, object]]:
    with Path(path).open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, 1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"invalid JSON on line {line_number}") from exc


def _read_csv(path: str | Path) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def probe_source(source: str) -> dict[str, object]:
    command = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=codec_name,width,height,avg_frame_rate:format=duration",
        "-of", "json", source,
    ]
    completed = subprocess.run(command, check=False, capture_output=True, text=True, timeout=30)
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "ffprobe failed")
    return json.loads(completed.stdout)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Empirical parking camera pipeline")
    subparsers = parser.add_subparsers(dest="command", required=True)

    infer = subparsers.add_parser("infer", help="run YOLOv8n + ByteTrack on MP4 or RTSP")
    infer.add_argument("--source", required=True)
    infer.add_argument("--config", type=Path, required=True)
    infer.add_argument("--output", type=Path, required=True)
    infer.add_argument("--observations", type=Path)
    infer.add_argument("--model", default="yolov8n.pt")
    infer.add_argument("--max-frames", type=int)

    normalize = subparsers.add_parser("normalize", help="convert normalized JSONL to maneuvers CSV")
    normalize.add_argument("--input", type=Path, required=True)
    normalize.add_argument("--config", type=Path, required=True)
    normalize.add_argument("--output", type=Path, required=True)

    validate = subparsers.add_parser("validate", help="score auto-labels against hand labels")
    validate.add_argument("--truth", type=Path, required=True)
    validate.add_argument("--predicted", type=Path, required=True)

    figure = subparsers.add_parser("figure", help="generate empirical/simulation comparison PDF")
    figure.add_argument("--empirical", type=Path, required=True)
    figure.add_argument("--simulation", type=Path, required=True)
    figure.add_argument("--output", type=Path, required=True)

    probe = subparsers.add_parser("probe", help="probe MP4/RTSP metadata with ffprobe")
    probe.add_argument("--source", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "probe":
        print(json.dumps(probe_source(args.source), indent=2))
        return 0
    if args.command == "validate":
        print(json.dumps(compute_validation_metrics(_read_csv(args.truth), _read_csv(args.predicted)), indent=2))
        return 0
    if args.command == "figure":
        print(json.dumps(generate_comparison_figure(args.empirical, args.simulation, args.output)))
        return 0

    config = LotConfig.from_json(args.config)
    if args.command == "normalize":
        records = analyze_observation_frames(_read_jsonl(args.input), config.stall_inward_vector)
    else:
        frames = infer_observation_frames(args.source, config, args.model, args.max_frames)
        if args.observations:
            frames = _tap_jsonl(frames, args.observations)
        records = analyze_observation_frames(frames, config.stall_inward_vector)
    export_maneuvers_csv(records, args.output)
    print(json.dumps({"maneuvers": len(records), "output": str(args.output)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
