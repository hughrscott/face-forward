#!/usr/bin/env python3
"""Evaluate frozen detector v2 against the adjudicated v1 development evidence."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from dlp.evaluate_boundary_calibration import evaluate_boundary_calibration
from dlp.validation_analysis import compare_validation_labels

PRIMARY_VEHICLES = frozenset({"Car", "Medium Vehicle"})
CLASSIFIABLE_METHODS = frozenset({"forward", "reverse"})
TARGET_EXCLUSIONS = {
    "VAL-123": "v1 annotation-target mismatch: sampled parking candidate, labeled adjacent unparking event",
}
METHOD_EXCLUSIONS = {
    "VAL-081": "predeclared kinematic audit conflict: saved reverse label contradicts forward motion evidence",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text())


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def match_v2_event(
    manifest_row: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]
) -> Mapping[str, Any] | None:
    """Map a v1 sampled target to the corresponding v2 candidate deterministically."""
    if not candidates:
        return None
    old_event_id = manifest_row.get("event_id")
    for event in candidates:
        if old_event_id and event.get("event_id") == old_event_id:
            return event

    old_start = manifest_row.get("detector_start_index")
    old_end = manifest_row.get("detector_end_index")

    def cost(event: Mapping[str, Any]) -> tuple[int, int, str]:
        structural = 0
        if event.get("stall_id") != manifest_row.get("detector_stall_id"):
            structural += 100_000
        if event.get("event_type") != manifest_row.get("detector_event_type"):
            structural += 10_000
        boundary_distance = 0
        if old_start is not None:
            boundary_distance += abs(int(event["legacy_start_index"]) - int(old_start))
        if old_end is not None:
            boundary_distance += abs(int(event["legacy_end_index"]) - int(old_end))
        return structural, boundary_distance, str(event["event_id"])

    return min(candidates, key=cost)


def remap_manifest(
    manifest: Sequence[Mapping[str, Any]],
    selected_item_ids: set[str],
    v2_events: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Replace v1 prediction fields with predictions from the frozen v2 ledger."""
    events_by_agent: dict[tuple[str, str], list[Mapping[str, Any]]] = defaultdict(list)
    for event in v2_events:
        events_by_agent[(str(event["scene_id"]), str(event["agent_token"]))].append(event)

    remapped: list[dict[str, Any]] = []
    for original in manifest:
        if original["item_id"] not in selected_item_ids:
            continue
        row = dict(original)
        candidates = events_by_agent[(str(row["scene_id"]), str(row["agent_token"]))]
        event = match_v2_event(row, candidates)
        if event is None:
            row.update(
                source_kind="random_track",
                detector_event_type=None,
                detector_method=None,
                detector_start_index=None,
                detector_end_index=None,
                detector_censoring=None,
                detector_complete=False,
                detector_stall_id=None,
                v2_event_id=None,
            )
        else:
            strict_positive = (
                bool(event["complete"])
                and event["method"] in CLASSIFIABLE_METHODS
                and row.get("agent_type") in PRIMARY_VEHICLES
            )
            row.update(
                source_kind="detector_positive" if strict_positive else "boundary",
                detector_event_type=event["event_type"],
                detector_method=event["method"],
                detector_start_index=event["start_index"],
                detector_end_index=event["end_index"],
                detector_censoring=event["censoring"],
                detector_complete=event["complete"],
                detector_stall_id=event["stall_id"],
                v2_event_id=event["event_id"],
            )
        remapped.append(row)
    return remapped


def method_summary(
    labels: Mapping[str, Mapping[str, Any]],
    predictions: Sequence[Mapping[str, Any]],
    exclusions: Mapping[str, str],
) -> dict[str, Any]:
    """Score method only where detector and human agree on the targeted event type."""
    by_id = {row["item_id"]: row for row in predictions}
    matches = 0
    denominator = 0
    disagreements: list[str] = []
    excluded: list[dict[str, str]] = []
    for item_id, human in sorted(labels.items()):
        row = by_id[item_id]
        if (
            row.get("source_kind") == "random_track"
            or row.get("detector_event_type") != human.get("event_type")
            or human.get("method") not in CLASSIFIABLE_METHODS
            or row.get("detector_method") not in CLASSIFIABLE_METHODS
        ):
            continue
        if item_id in exclusions:
            excluded.append({"item_id": item_id, "reason": exclusions[item_id]})
            continue
        denominator += 1
        if row["detector_method"] == human["method"]:
            matches += 1
        else:
            disagreements.append(item_id)
    return {
        "accuracy": matches / denominator if denominator else None,
        "matches": matches,
        "n": denominator,
        "disagreements": disagreements,
        "excluded": excluded,
    }


def accepted_event_summary(
    labels: Mapping[str, Mapping[str, Any]], predictions: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Describe strict detector-positive status against human event existence."""
    by_id = {row["item_id"]: row for row in predictions}
    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    disagreements: list[str] = []
    for item_id, human in sorted(labels.items()):
        predicted = by_id[item_id].get("source_kind") == "detector_positive"
        observed = human.get("event_type") != "not_event"
        key = "tp" if predicted and observed else "fp" if predicted else "fn" if observed else "tn"
        counts[key] += 1
        if predicted != observed:
            disagreements.append(item_id)
    precision_denominator = counts["tp"] + counts["fp"]
    recall_denominator = counts["tp"] + counts["fn"]
    return {
        "counts": counts,
        "precision": counts["tp"] / precision_denominator if precision_denominator else None,
        "recall": counts["tp"] / recall_denominator if recall_denominator else None,
        "disagreements": disagreements,
    }


def evaluate_detector_v2_development(
    validation_root: Path,
    v2_ledger: Path,
    calibration_root: Path,
) -> dict[str, Any]:
    manifest_path = validation_root / "internal" / "manifest.json"
    labels_path = validation_root / "internal" / "hugh_labels_adjudicated.json"
    hermes_path = validation_root / "internal" / "hermes_labels_complete_v2.json"
    manifest = _load_json(manifest_path)
    labels = _load_json(labels_path)
    hermes_labels = _load_json(hermes_path)
    v2_events = _load_jsonl(v2_ledger)
    predictions = remap_manifest(manifest, set(labels), v2_events)

    fps_by_item = {
        item_id: float(_load_json(validation_root / "hugh" / "items" / f"{item_id}.json")["fps"])
        for item_id in labels
    }
    raw = compare_validation_labels(labels, hermes_labels, predictions, fps_by_item)
    clean_labels = {item: label for item, label in labels.items() if item not in TARGET_EXCLUSIONS}
    clean_predictions = [row for row in predictions if row["item_id"] in clean_labels]
    clean = compare_validation_labels(clean_labels, hermes_labels, clean_predictions, fps_by_item)
    methods = method_summary(clean_labels, clean_predictions, METHOD_EXCLUSIONS)
    calibration = evaluate_boundary_calibration(calibration_root)

    return {
        "status": "development evidence only; not held-out validation",
        "inputs": {
            "manifest_sha256": _sha256(manifest_path),
            "hugh_labels_sha256": _sha256(labels_path),
            "hermes_labels_sha256": _sha256(hermes_path),
            "v2_ledger_sha256": _sha256(v2_ledger),
            "calibration_labels_sha256": calibration["label_snapshot_sha256"],
        },
        "reviewed_items": len(labels),
        "target_exclusions": [
            {"item_id": item, "reason": reason} for item, reason in sorted(TARGET_EXCLUSIONS.items())
        ],
        "raw_50_item_comparison": raw,
        "clean_target_comparison": {
            "n": len(clean_labels),
            "broad_candidate_counts": clean["candidate_detection_counts"],
            "broad_candidate_precision": clean["candidate_detection_precision"],
            "broad_candidate_recall": clean["candidate_detection_recall"],
            "broad_candidate_f1": clean["candidate_detection_f1"],
            "accepted_event": accepted_event_summary(clean_labels, clean_predictions),
            "strict_analyzable_counts": clean["strict_analyzable_event_counts"],
            "strict_analyzable_precision": clean["strict_analyzable_event_precision"],
            "strict_analyzable_recall": clean["strict_analyzable_event_recall"],
            "event_type_accuracy": clean["event_type_accuracy"],
            "event_disagreements": clean["detector_event_disagreements"],
            "method": methods,
        },
        "corrected_boundary_comparison": calibration,
        "prediction_map": predictions,
        "promotion_note": (
            "Development metrics diagnose v2 but do not promote it. The fresh weighted, "
            "agent-disjoint held-out package supplies the promotion gates."
        ),
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--validation-root", type=Path, default=Path("dlp/results/validation"))
    parser.add_argument(
        "--v2-ledger",
        type=Path,
        default=Path("dlp/results/v2-semantic-development/candidate_events.jsonl"),
    )
    parser.add_argument(
        "--calibration-root", type=Path, default=Path("dlp/results/v2-boundary-calibration")
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    report = evaluate_detector_v2_development(
        args.validation_root, args.v2_ledger, args.calibration_root
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered)
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
