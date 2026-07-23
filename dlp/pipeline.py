#!/usr/bin/env python3
"""Scene-independent, outcome-blind DLP maneuver inventory pipeline."""
from __future__ import annotations

import re
import math
import json
import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Callable, Sequence

SCENE_FILE = re.compile(
    r"^(DJI_\d{4})_(agents|frames|instances|obstacles|scene)\.json$"
)
REQUIRED_SUFFIXES = frozenset({"agents", "frames", "instances", "obstacles", "scene"})
VEHICLE_TYPES = frozenset({"Car", "Bus", "Truck", "Medium Vehicle", "Motorcycle"})

PARKING_LAYOUT = {
    "A": (28.53, 138.42, 68.51, 73.73, 1, 42),
    "B": (7.71, 76.54, 50.40, 61.40, 2, 25),
    "C": (83.82, 138.42, 50.40, 61.40, 2, 21),
    "D": (7.71, 76.54, 31.93, 43.24, 2, 25),
    "E": (83.82, 138.42, 31.93, 43.24, 2, 21),
    "F": (7.71, 76.54, 13.51, 24.68, 2, 25),
    "G": (83.82, 138.42, 13.51, 24.68, 2, 21),
    "H": (7.71, 76.54, 0.95, 6.48, 1, 25),
    "I": (83.82, 138.42, 0.95, 6.48, 1, 21),
}


@dataclass(frozen=True)
class Stall:
    stall_id: str
    area: str
    row: int
    column: int
    xmin: float
    xmax: float
    ymin: float
    ymax: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2)

    def contains(self, point: tuple[float, float] | list[float]) -> bool:
        x, y = point
        return self.xmin <= x <= self.xmax and self.ymin <= y <= self.ymax


@dataclass(frozen=True)
class EventCandidate:
    event_id: str
    scene_id: str
    scene_token: str
    agent_token: str
    agent_type: str
    vehicle_length: float
    vehicle_width: float
    event_type: str
    stall_id: str
    parking_area: str
    method: str
    method_confidence: float
    crossing_index: int | None
    start_index: int
    end_index: int
    start_timestamp: float
    end_timestamp: float
    duration_seconds: float
    censoring: str
    complete: bool


@dataclass(frozen=True)
class SceneInventory:
    scene_id: str
    scene_token: str
    fps: float
    agent_count: int
    vehicle_agent_count: int
    instance_count: int
    obstacle_count: int
    events: tuple[EventCandidate, ...]


def generate_stalls() -> tuple[Stall, ...]:
    """Divide the published parking-area bounds into individual stall polygons."""
    stalls: list[Stall] = []
    for area, (xmin, xmax, ymin, ymax, rows, columns) in PARKING_LAYOUT.items():
        width = (xmax - xmin) / columns
        height = (ymax - ymin) / rows
        for row in range(1, rows + 1):
            for column in range(1, columns + 1):
                stalls.append(
                    Stall(
                        stall_id=f"{area}-R{row}-C{column:02d}",
                        area=area,
                        row=row,
                        column=column,
                        xmin=xmin + (column - 1) * width,
                        xmax=xmin + column * width,
                        ymin=ymin + (row - 1) * height,
                        ymax=ymin + row * height,
                    )
                )
    return tuple(stalls)


def stall_for_point(
    point: tuple[float, float] | list[float], stalls: tuple[Stall, ...]
) -> Stall | None:
    return next((stall for stall in stalls if stall.contains(point)), None)


def obstacle_stall_coverage(
    obstacles: dict, stalls: tuple[Stall, ...]
) -> tuple[int, int, float]:
    """Count static-obstacle centroids that fall inside generated stalls."""
    total = len(obstacles)
    covered = sum(
        stall_for_point(obstacle["coords"], stalls) is not None
        for obstacle in obstacles.values()
    )
    return covered, total, covered / total if total else 0.0


def _motion_sign(rows: list[dict], index: int) -> int:
    lo = max(0, index - 1)
    hi = min(len(rows) - 1, index + 1)
    dx = rows[hi]["coords"][0] - rows[lo]["coords"][0]
    dy = rows[hi]["coords"][1] - rows[lo]["coords"][1]
    heading = rows[index]["heading"]
    dot = math.cos(heading) * dx + math.sin(heading) * dy
    if math.isclose(dot, 0.0, abs_tol=1e-9):
        return 0
    return 1 if dot > 0 else -1


def classify_stall_crossing(
    rows: list[dict],
    stall: Stall,
    event_type: str,
    *,
    moving_speed: float = 0.10,
    evidence_frames: int = 1,
    minimum_motion_samples: int = 1,
) -> tuple[str, float, int | None]:
    """Classify motion at the stall boundary, separate from the timing window."""
    inside = [stall.contains(row["coords"]) for row in rows]
    if event_type == "parking":
        crossings = [i for i in range(1, len(rows)) if inside[i] and not inside[i - 1]]
        crossing = crossings[-1] if crossings else None
    elif event_type == "unparking":
        crossings = [i for i in range(1, len(rows)) if not inside[i] and inside[i - 1]]
        crossing = crossings[0] if crossings else None
    else:
        raise ValueError(f"Unsupported event type: {event_type}")

    if crossing is None:
        return "unclear", 0.0, None

    signs = [
        _motion_sign(rows, i)
        for i in range(
            max(0, crossing - evidence_frames),
            min(len(rows), crossing + evidence_frames + 1),
        )
        if rows[i]["speed"] >= moving_speed
    ]
    signs = [sign for sign in signs if sign]
    if len(signs) < minimum_motion_samples:
        return "unclear", 0.0, crossing
    forward_share = sum(sign > 0 for sign in signs) / len(signs)
    reverse_share = sum(sign < 0 for sign in signs) / len(signs)
    if forward_share >= 0.70:
        return "forward", forward_share, crossing
    if reverse_share >= 0.70:
        return "reverse", reverse_share, crossing
    return "mixed", max(forward_share, reverse_share), crossing


def _static_runs(
    rows: list[dict], stalls: tuple[Stall, ...], minimum_frames: int
) -> list[tuple[int, int, Stall]]:
    runs: list[tuple[int, int, Stall]] = []
    start: int | None = None
    current: Stall | None = None

    for index, row in enumerate(rows + [None]):
        stall = None
        if row is not None and row["speed"] < 0.05:
            stall = stall_for_point(row["coords"], stalls)
        if current is not None and (stall is None or stall.stall_id != current.stall_id):
            assert start is not None
            if index - start >= minimum_frames:
                runs.append((start, index - 1, current))
            start = None
            current = None
        if stall is not None and current is None:
            start = index
            current = stall
    return runs


def _merge_fragmented_static_runs(
    runs: list[tuple[int, int, Stall]],
    rows: list[dict],
    maximum_gap_frames: int,
) -> list[tuple[int, int, Stall]]:
    """Join qualified same-stall runs separated only by brief speed noise."""
    merged: list[tuple[int, int, Stall]] = []
    for start, end, stall in runs:
        if merged:
            previous_start, previous_end, previous_stall = merged[-1]
            gap = rows[previous_end + 1:start]
            gap_is_speed_noise = (
                len(gap) <= maximum_gap_frames
                and all(row["speed"] < 0.10 for row in gap)
            )
            gap_is_in_stall_repositioning = (
                bool(gap)
                and all(stall.contains(row["coords"]) for row in gap)
            )
            if (
                stall.stall_id == previous_stall.stall_id
                and (gap_is_speed_noise or gap_is_in_stall_repositioning)
            ):
                merged[-1] = (previous_start, end, previous_stall)
                continue
        merged.append((start, end, stall))
    return merged


def _first_sustained_movement(
    rows: list[dict],
    start: int,
    minimum_frames: int,
    stop: int | None = None,
) -> int | None:
    stop = len(rows) if stop is None else min(stop, len(rows))
    for index in range(start, stop - minimum_frames + 1):
        if all(row["speed"] >= 0.10 for row in rows[index:index + minimum_frames]):
            return index
    return None


def detect_trajectory_events(
    scene_id: str,
    scene_token: str,
    agent: dict,
    rows: list[dict],
    stalls: tuple[Stall, ...],
    *,
    fps: float,
    static_seconds: float = 2.0,
    moving_seconds: float = 0.5,
    envelope_metres: float = 8.0,
) -> list[EventCandidate]:
    """Find broad parking/unparking candidates without aggregating outcomes."""
    static_frames = max(1, round(static_seconds * fps))
    moving_frames = max(1, round(moving_seconds * fps))
    events: list[EventCandidate] = []
    size: Sequence[float] = agent.get("size", (0.0, 0.0))

    static_runs = _static_runs(rows, stalls, static_frames)
    static_runs = _merge_fragmented_static_runs(
        static_runs,
        rows,
        maximum_gap_frames=max(1, round(fps)),
    )
    for run_index, (run_start, run_end, stall) in enumerate(static_runs):
        if run_start > 0:
            start = run_start
            while start > 0 and math.dist(rows[start - 1]["coords"], stall.center) <= envelope_metres:
                start -= 1
            left_censored = start == 0 and math.dist(rows[0]["coords"], stall.center) <= envelope_metres
            segment = rows[start:run_start + 1]
            method, confidence, crossing = classify_stall_crossing(
                segment,
                stall,
                "parking",
                evidence_frames=max(1, round(0.5 * fps)),
                minimum_motion_samples=min(3, max(1, round(0.12 * fps))),
            )
            crossing_index = None if crossing is None else start + crossing
            censoring = "left" if left_censored else "none"
            events.append(
                EventCandidate(
                    event_id=f"{scene_id}:{agent['agent_token']}:parking:{run_start}",
                    scene_id=scene_id,
                    scene_token=scene_token,
                    agent_token=agent["agent_token"],
                    agent_type=agent["type"],
                    vehicle_length=float(size[0]),
                    vehicle_width=float(size[1]),
                    event_type="parking",
                    stall_id=stall.stall_id,
                    parking_area=stall.area,
                    method=method,
                    method_confidence=confidence,
                    crossing_index=crossing_index,
                    start_index=start,
                    end_index=run_start,
                    start_timestamp=float(rows[start]["timestamp"]),
                    end_timestamp=float(rows[run_start]["timestamp"]),
                    duration_seconds=float(rows[run_start]["timestamp"] - rows[start]["timestamp"]),
                    censoring=censoring,
                    complete=not left_censored,
                )
            )

        next_run_start = (
            static_runs[run_index + 1][0]
            if run_index + 1 < len(static_runs)
            else None
        )
        movement_start = _first_sustained_movement(
            rows,
            run_end + 1,
            moving_frames,
            stop=next_run_start,
        )
        if movement_start is not None:
            end = movement_start
            while end < len(rows) - 1 and math.dist(rows[end]["coords"], stall.center) <= envelope_metres:
                end += 1
            right_censored = end == len(rows) - 1 and math.dist(rows[end]["coords"], stall.center) <= envelope_metres
            classification_start = min(run_end, movement_start)
            segment = rows[classification_start:end + 1]
            method, confidence, crossing = classify_stall_crossing(
                segment,
                stall,
                "unparking",
                evidence_frames=max(1, round(0.5 * fps)),
                minimum_motion_samples=min(3, max(1, round(0.12 * fps))),
            )
            crossing_index = None if crossing is None else classification_start + crossing
            censoring = "right" if right_censored else "none"
            events.append(
                EventCandidate(
                    event_id=f"{scene_id}:{agent['agent_token']}:unparking:{movement_start}",
                    scene_id=scene_id,
                    scene_token=scene_token,
                    agent_token=agent["agent_token"],
                    agent_type=agent["type"],
                    vehicle_length=float(size[0]),
                    vehicle_width=float(size[1]),
                    event_type="unparking",
                    stall_id=stall.stall_id,
                    parking_area=stall.area,
                    method=method,
                    method_confidence=confidence,
                    crossing_index=crossing_index,
                    start_index=movement_start,
                    end_index=end,
                    start_timestamp=float(rows[movement_start]["timestamp"]),
                    end_timestamp=float(rows[end]["timestamp"]),
                    duration_seconds=float(rows[end]["timestamp"] - rows[movement_start]["timestamp"]),
                    censoring=censoring,
                    complete=not right_censored,
                )
            )
    return events


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def inventory_scene(
    data_dir: Path, scene_id: str, stalls: tuple[Stall, ...]
) -> SceneInventory:
    """Load one five-file scene bundle and return its unaggregated event ledger."""
    scene = _load_json(data_dir / f"{scene_id}_scene.json")
    agents = _load_json(data_dir / f"{scene_id}_agents.json")
    frames = _load_json(data_dir / f"{scene_id}_frames.json")
    instances = _load_json(data_dir / f"{scene_id}_instances.json")
    obstacles = _load_json(data_dir / f"{scene_id}_obstacles.json")

    timestamps = {token: float(frame["timestamp"]) for token, frame in frames.items()}
    ordered_times = sorted(timestamps.values())
    deltas = [b - a for a, b in zip(ordered_times, ordered_times[1:]) if b > a]
    if not deltas:
        raise ValueError(f"Scene {scene_id} has no positive frame intervals")
    fps = 1.0 / median(deltas)

    trajectories: dict[str, list[dict]] = {token: [] for token in agents}
    for instance in instances.values():
        enriched = dict(instance)
        enriched["timestamp"] = timestamps[instance["frame_token"]]
        trajectories[instance["agent_token"]].append(enriched)
    for rows in trajectories.values():
        rows.sort(key=lambda row: row["timestamp"])

    events: list[EventCandidate] = []
    vehicle_count = 0
    for token, agent in agents.items():
        if agent["type"] not in VEHICLE_TYPES:
            continue
        vehicle_count += 1
        events.extend(
            detect_trajectory_events(
                scene_id,
                scene["scene_token"],
                agent,
                trajectories[token],
                stalls,
                fps=fps,
            )
        )

    return SceneInventory(
        scene_id=scene_id,
        scene_token=scene["scene_token"],
        fps=fps,
        agent_count=len(agents),
        vehicle_agent_count=vehicle_count,
        instance_count=len(instances),
        obstacle_count=len(obstacles),
        events=tuple(events),
    )


def write_inventory_outputs(
    inventories: Sequence[SceneInventory], output_dir: Path
) -> tuple[Path, Path]:
    """Write event-level candidates and an outcome-blind per-scene inventory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    event_path = output_dir / "candidate_events.jsonl"
    scene_path = output_dir / "scene_inventory.json"

    with event_path.open("w", encoding="utf-8") as handle:
        for inventory in sorted(inventories, key=lambda item: item.scene_id):
            for event in inventory.events:
                handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")

    scene_rows = [
        {
            "scene_id": inventory.scene_id,
            "scene_token": inventory.scene_token,
            "fps": inventory.fps,
            "agent_count": inventory.agent_count,
            "vehicle_agent_count": inventory.vehicle_agent_count,
            "instance_count": inventory.instance_count,
            "obstacle_count": inventory.obstacle_count,
            "event_count": len(inventory.events),
        }
        for inventory in sorted(inventories, key=lambda item: item.scene_id)
    ]
    scene_path.write_text(json.dumps(scene_rows, indent=2), encoding="utf-8")
    return event_path, scene_path


def run_inventory(
    data_dir: Path,
    output_dir: Path,
    *,
    progress: Callable[[str], object] | None = None,
) -> tuple[SceneInventory, ...]:
    """Inventory every complete scene sequentially and write blinded outputs."""
    stalls = generate_stalls()
    scenes = discover_scene_prefixes(data_dir)
    inventories: list[SceneInventory] = []
    for index, scene_id in enumerate(scenes, start=1):
        if progress:
            progress(f"[{index:02d}/{len(scenes):02d}] {scene_id}")
        inventories.append(inventory_scene(data_dir, scene_id, stalls))
    write_inventory_outputs(inventories, output_dir)
    return tuple(inventories)


def discover_scene_prefixes(data_dir: Path) -> list[str]:
    """Return complete scene prefixes; reject any partial scene bundle."""
    found: dict[str, set[str]] = {}
    for path in data_dir.glob("DJI_*.json"):
        match = SCENE_FILE.fullmatch(path.name)
        if match:
            found.setdefault(match.group(1), set()).add(match.group(2))

    for scene, suffixes in sorted(found.items()):
        missing = sorted(REQUIRED_SUFFIXES - suffixes)
        if missing:
            raise ValueError(f"Incomplete scene {scene}; missing: {', '.join(missing)}")
    return sorted(found)


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=root / "data")
    parser.add_argument("--output", type=Path, default=root / "results")
    args = parser.parse_args()

    inventories = run_inventory(args.data, args.output, progress=lambda line: print(line, flush=True))
    print(
        f"COMPLETE: {len(inventories)} scenes inventoried; "
        f"outputs written to {args.output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
