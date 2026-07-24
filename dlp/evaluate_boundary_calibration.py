#!/usr/bin/env python3
"""Compare legacy and semantic DLP boundaries against sealed development labels."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from statistics import mean, median

from dlp.pipeline import (
    _semantic_parking_end,
    _semantic_parking_start,
    _semantic_unparking_end,
    _semantic_unparking_start,
    generate_stalls,
)

BOUNDARY_KEYS = (
    "parking_start",
    "parking_end",
    "unparking_start",
    "unparking_end",
)

EXCLUDED_BOUNDARIES = {
    ("VAL-012", "parking_end"): "acknowledged non-sustained parked endpoint",
    ("VAL-044", "parking_end"): "acknowledged non-sustained parked endpoint",
    ("VAL-096", "parking_end"): "acknowledged non-sustained parked endpoint",
    ("VAL-099", "parking_end"): "acknowledged non-sustained parked endpoint",
    ("VAL-106", "parking_end"): "acknowledged non-sustained parked endpoint",
    ("VAL-124", "parking_end"): "acknowledged non-sustained parked endpoint",
    ("VAL-123", "unparking_start"): "annotation-target mismatch",
    ("VAL-123", "unparking_end"): "annotation-target mismatch",
    ("VAL-018", "unparking_end"): "acknowledged non-aisle endpoint",
}


def summarize_boundary_errors(
    errors: dict[str, list[tuple[str, float]]],
) -> dict[str, dict]:
    """Summarize signed errors expressed in seconds."""
    report: dict[str, dict] = {}
    all_absolute: list[float] = []
    for boundary, rows in errors.items():
        absolute = [abs(error) for _, error in rows]
        if not absolute:
            continue
        all_absolute.extend(absolute)
        report[boundary] = {
            "n": len(absolute),
            "median_abs_s": round(median(absolute), 3),
            "mean_abs_s": round(mean(absolute), 3),
            "max_abs_s": round(max(absolute), 3),
            "over_2s": sum(value > 2.0 for value in absolute),
            "worst": [
                {"item_id": item_id, "signed_s": round(error, 3)}
                for item_id, error in sorted(
                    rows, key=lambda row: abs(row[1]), reverse=True
                )[:5]
            ],
        }
    report["overall"] = {
        "n": len(all_absolute),
        "median_abs_s": round(median(all_absolute), 3),
        "mean_abs_s": round(mean(all_absolute), 3),
        "max_abs_s": round(max(all_absolute), 3),
        "over_2s": sum(value > 2.0 for value in all_absolute),
    }
    return report


def evaluate_boundary_calibration(package_root: Path) -> dict:
    """Run production boundary helpers against the sealed calibration package."""
    manifest_path = package_root / "internal" / "calibration_manifest.json"
    labels_path = package_root / "internal" / "hugh_boundary_labels_complete.json"
    manifest = {
        row["item_id"]: row for row in json.loads(manifest_path.read_text())
    }
    labels = json.loads(labels_path.read_text())
    stalls = {stall.stall_id: stall for stall in generate_stalls()}
    legacy = {key: [] for key in BOUNDARY_KEYS}
    semantic = {key: [] for key in BOUNDARY_KEYS}

    for item_id, label in labels.items():
        event_type = label["event_type"]
        if event_type not in {"parking", "unparking"}:
            continue
        manifest_row = manifest[item_id]
        payload = json.loads(
            (package_root / "hugh" / "items" / f"{item_id}.json").read_text()
        )
        raw_trajectory = payload["trajectory"]
        rows = [
            {
                "coords": [row["x"], row["y"]],
                "heading": row["heading"],
                "speed": row["speed"],
                "timestamp": row["timestamp"],
            }
            for row in raw_trajectory
        ]
        frame_positions = {
            row["frame_index"]: index
            for index, row in enumerate(raw_trajectory)
        }
        fps = float(payload["fps"])
        stall = stalls[manifest_row["detector_stall_id"]]
        legacy_start = frame_positions[manifest_row["detector_start_index"]]
        legacy_end = frame_positions[manifest_row["detector_end_index"]]
        crossing = frame_positions.get(
            manifest_row.get("detector_crossing_index"), legacy_end
        )

        if event_type == "parking":
            episode_end = legacy_end
            for index in range(legacy_end, len(rows)):
                if not stall.contains(rows[index]["coords"]):
                    break
                episode_end = index
            predictions = {
                "start": _semantic_parking_start(
                    rows,
                    stall,
                    legacy_start=legacy_start,
                    crossing=crossing,
                    fps=fps,
                ),
                "end": _semantic_parking_end(
                    rows,
                    stall,
                    episode_start=legacy_end,
                    episode_end=episode_end,
                    minimum_static_frames=max(1, round(2.0 * fps)),
                    fps=fps,
                ),
            }
        else:
            predictions = {
                "start": _semantic_unparking_start(
                    rows,
                    legacy_start=legacy_start,
                    stop=legacy_end + 1,
                    fps=fps,
                ),
                "end": _semantic_unparking_end(
                    rows,
                    stall,
                    crossing=crossing,
                    legacy_end=legacy_end,
                    fps=fps,
                ),
            }

        for boundary_name in ("start", "end"):
            target_frame = label[f"{boundary_name}_index"]
            component = f"{event_type}_{boundary_name}"
            if target_frame is None or (item_id, component) in EXCLUDED_BOUNDARIES:
                continue
            human_position = frame_positions[target_frame]
            legacy_position = (
                legacy_start if boundary_name == "start" else legacy_end
            )
            legacy[component].append(
                (item_id, (legacy_position - human_position) / fps)
            )
            semantic[component].append(
                (item_id, (predictions[boundary_name] - human_position) / fps)
            )

    return {
        "label_snapshot_sha256": hashlib.sha256(labels_path.read_bytes()).hexdigest(),
        "excluded_boundaries": [
            {
                "item_id": item_id,
                "component": component,
                "reason": reason,
            }
            for (item_id, component), reason in sorted(EXCLUDED_BOUNDARIES.items())
        ],
        "legacy": summarize_boundary_errors(legacy),
        "semantic": summarize_boundary_errors(semantic),
    }


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--package",
        type=Path,
        default=root / "results" / "v2-boundary-calibration",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    report = evaluate_boundary_calibration(args.package)
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")


if __name__ == "__main__":
    main()
