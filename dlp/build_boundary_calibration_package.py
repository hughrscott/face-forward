#!/usr/bin/env python3
"""Build a development-only package for corrected DLP boundary labels."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from dlp.pipeline import generate_stalls
from dlp.validation import build_review_payload, load_scene_review_data, write_reviewer_package

_EXCLUDED_ITEMS = {
    "VAL-132": "random-track endpoint was saved complete while moving at 2.64 m/s",
}


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def select_calibration_items(
    manifest: list[dict],
    labels: dict[str, dict],
    candidate_events: dict[str, dict],
    review_order: dict[str, int],
) -> list[dict]:
    """Select complete adjudicated development events and add neutral anchors."""
    selected = []
    for row in manifest:
        item_id = row["item_id"]
        label = labels.get(item_id)
        if (
            label is None
            or item_id in _EXCLUDED_ITEMS
            or label["event_type"] not in {"parking", "unparking"}
            or label["censoring"] != "complete"
        ):
            continue
        item = dict(row)
        event = candidate_events.get(item.get("event_id"), {})
        item["detector_crossing_index"] = event.get("crossing_index")
        item["reviewer"] = "hugh"
        item["original_review_order"] = review_order[item_id]
        selected.append(item)
    selected.sort(key=lambda item: item["original_review_order"])
    for order, item in enumerate(selected, start=1):
        item["review_order"] = order
    return selected


def build_calibration_package(
    data_dir: Path,
    source_package: Path,
    event_path: Path,
    output_dir: Path,
    *,
    context_seconds: float,
) -> dict:
    internal = source_package / "internal"
    manifest = json.loads((internal / "manifest.json").read_text(encoding="utf-8"))
    subset = json.loads((internal / "hugh_subset.json").read_text(encoding="utf-8"))
    label_path = internal / "hugh_labels_adjudicated.json"
    labels = json.loads(label_path.read_text(encoding="utf-8"))
    candidate_events = {event["event_id"]: event for event in _read_jsonl(event_path)}
    review_order = {item["item_id"]: item["review_order"] for item in subset}
    selected = select_calibration_items(manifest, labels, candidate_events, review_order)

    by_scene: dict[str, list[dict]] = defaultdict(list)
    for item in selected:
        by_scene[item["scene_id"]].append(item)

    stalls = generate_stalls()
    payloads: dict[str, dict] = {}
    for number, scene_id in enumerate(sorted(by_scene), start=1):
        items = by_scene[scene_id]
        wanted = {item["agent_token"] for item in items}
        print(f"[{number:02d}/{len(by_scene):02d}] {scene_id}: {len(items)} items", flush=True)
        agents, trajectories, fps = load_scene_review_data(data_dir, scene_id, wanted)
        for item in items:
            token = item["agent_token"]
            payloads[item["item_id"]] = build_review_payload(
                item,
                agents[token],
                trajectories[token],
                fps=fps,
                stalls=stalls,
                context_seconds=context_seconds,
            )

    write_reviewer_package(output_dir, "hugh", selected, payloads, seed=20260723)
    internal_output = output_dir / "internal"
    internal_output.mkdir(parents=True, exist_ok=True)
    (internal_output / "calibration_manifest.json").write_text(
        json.dumps(selected, indent=2, sort_keys=True), encoding="utf-8"
    )
    record = {
        "purpose": "detector-v2 boundary development only; not held-out validation",
        "protocol_version": "1.1-development",
        "context_seconds": context_seconds,
        "item_count": len(selected),
        "event_type_counts": dict(sorted(Counter(labels[item["item_id"]]["event_type"] for item in selected).items())),
        "source_label_sha256": _sha256(label_path),
        "source_event_sha256": _sha256(event_path),
        "excluded_items": _EXCLUDED_ITEMS,
        "labels_directory": str(output_dir / "labels"),
    }
    (internal_output / "calibration_record.json").write_text(
        json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
    )
    return record


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=root / "data")
    parser.add_argument("--source-package", type=Path, default=root / "results" / "validation")
    parser.add_argument("--events", type=Path, default=root / "results" / "candidate_events.jsonl")
    parser.add_argument("--output", type=Path, default=root / "results" / "v2-boundary-calibration")
    parser.add_argument("--context-seconds", type=float, default=15.0)
    args = parser.parse_args()
    record = build_calibration_package(
        args.data,
        args.source_package,
        args.events,
        args.output,
        context_seconds=args.context_seconds,
    )
    print(json.dumps(record, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
