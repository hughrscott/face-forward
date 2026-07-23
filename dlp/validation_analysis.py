"""Mechanical comparison of frozen detector predictions and blinded review labels."""

from __future__ import annotations

from collections import Counter
from statistics import median
from typing import Any, Mapping, Sequence

_EVENT_TYPES = ("parking", "unparking", "not_event")
_METHODS = ("forward", "reverse", "mixed")
_CLASSIFIABLE_METHODS = frozenset({"forward", "reverse"})
_PRIMARY_VEHICLE_TYPES = frozenset({"Car", "Medium Vehicle"})


def _safe_div(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _gate(value: float | None, threshold: float, *, strictly_below: bool = False) -> bool:
    if value is None:
        return False
    return value < threshold if strictly_below else value >= threshold


def cohens_kappa(left: Sequence[str], right: Sequence[str], categories: Sequence[str]) -> float | None:
    """Return unweighted Cohen's kappa for paired categorical labels."""
    if len(left) != len(right):
        raise ValueError("Kappa inputs must have equal length")
    if not left:
        return None
    allowed = set(categories)
    if not set(left).issubset(allowed) or not set(right).issubset(allowed):
        raise ValueError("Kappa input contains an unknown category")

    n = len(left)
    observed = sum(a == b for a, b in zip(left, right, strict=True)) / n
    left_counts = Counter(left)
    right_counts = Counter(right)
    expected = sum((left_counts[c] / n) * (right_counts[c] / n) for c in categories)
    if expected == 1.0:
        return 1.0 if observed == 1.0 else 0.0
    return (observed - expected) / (1.0 - expected)


def compare_validation_labels(
    hugh_labels: Mapping[str, Mapping[str, Any]],
    hermes_labels: Mapping[str, Mapping[str, Any]],
    manifest: Sequence[Mapping[str, Any]],
    fps_by_item: Mapping[str, float],
) -> dict[str, Any]:
    """Compare detector outputs with Hugh labels and reviewer agreement.

    Candidate-detection metrics treat both strict positives and boundary/rejected
    candidates as detections. Strict-analyzable metrics count only complete,
    classifiable detector positives as usable timing events. Hugh is the human
    reference; Hermes supplies an independent categorical-agreement check.
    """
    manifest_by_id = {row["item_id"]: row for row in manifest}
    missing = set(hugh_labels) - set(manifest_by_id)
    if missing:
        raise ValueError(f"Hugh labels absent from manifest: {sorted(missing)}")

    counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    candidate_counts = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    event_type_matches = 0
    event_type_denominator = 0
    method_matches = 0
    method_denominator = 0
    start_errors: list[float] = []
    end_errors: list[float] = []
    detector_disagreements: list[str] = []
    method_disagreements: list[str] = []

    for item_id, human in hugh_labels.items():
        row = manifest_by_id[item_id]
        detector_positive = row.get("source_kind") == "detector_positive"
        candidate_positive = row.get("source_kind") != "random_track"
        human_positive = human.get("event_type") != "not_event"
        human_eligible = (
            human_positive
            and human.get("method") in _CLASSIFIABLE_METHODS
            and human.get("censoring") == "complete"
            and human.get("start_index") is not None
            and human.get("end_index") is not None
            and row.get("agent_type") in _PRIMARY_VEHICLE_TYPES
        )

        if candidate_positive and human_positive:
            candidate_counts["tp"] += 1
        elif candidate_positive:
            candidate_counts["fp"] += 1
        elif human_positive:
            candidate_counts["fn"] += 1
        else:
            candidate_counts["tn"] += 1

        if detector_positive and human_eligible:
            counts["tp"] += 1
        elif detector_positive:
            counts["fp"] += 1
        elif human_eligible:
            counts["fn"] += 1
        else:
            counts["tn"] += 1

        predicted_event = row.get("detector_event_type") if candidate_positive else "not_event"
        if predicted_event == human.get("event_type"):
            event_type_matches += 1
        else:
            detector_disagreements.append(item_id)
        event_type_denominator += 1

        human_method = human.get("method")
        detector_method = row.get("detector_method")
        if (
            candidate_positive
            and predicted_event == human.get("event_type")
            and human_method in _METHODS
            and detector_method in _METHODS
        ):
            method_denominator += 1
            if human_method == detector_method:
                method_matches += 1
            else:
                method_disagreements.append(item_id)

        if candidate_positive and human_positive:
            fps = float(fps_by_item[item_id])
            if fps <= 0:
                raise ValueError(f"Non-positive FPS for {item_id}")
            if human.get("start_index") is not None and row.get("detector_start_index") is not None:
                start_errors.append(abs(row["detector_start_index"] - human["start_index"]) / fps)
            if human.get("end_index") is not None and row.get("detector_end_index") is not None:
                end_errors.append(abs(row["detector_end_index"] - human["end_index"]) / fps)

    precision = _safe_div(counts["tp"], counts["tp"] + counts["fp"])
    recall = _safe_div(counts["tp"], counts["tp"] + counts["fn"])
    f1 = None if precision is None or recall is None or precision + recall == 0 else 2 * precision * recall / (precision + recall)
    candidate_precision = _safe_div(candidate_counts["tp"], candidate_counts["tp"] + candidate_counts["fp"])
    candidate_recall = _safe_div(candidate_counts["tp"], candidate_counts["tp"] + candidate_counts["fn"])
    candidate_f1 = (
        None
        if candidate_precision is None or candidate_recall is None or candidate_precision + candidate_recall == 0
        else 2 * candidate_precision * candidate_recall / (candidate_precision + candidate_recall)
    )
    method_accuracy = _safe_div(method_matches, method_denominator)
    all_boundary_errors = start_errors + end_errors
    boundary_median = median(all_boundary_errors) if all_boundary_errors else None

    overlap = sorted(set(hugh_labels) & set(hermes_labels))
    hugh_events = [str(hugh_labels[item]["event_type"]) for item in overlap]
    hermes_events = [str(hermes_labels[item]["event_type"]) for item in overlap]
    kappa = cohens_kappa(hugh_events, hermes_events, _EVENT_TYPES)
    reviewer_disagreements = [item for item in overlap if hugh_labels[item]["event_type"] != hermes_labels[item]["event_type"]]

    gates = {
        "event_precision": _gate(candidate_precision, 0.95),
        "event_recall": _gate(candidate_recall, 0.90),
        "method_accuracy": _gate(method_accuracy, 0.95),
        "timing_boundary_error": _gate(boundary_median, 0.5, strictly_below=True),
        "categorical_agreement": _gate(kappa, 0.90),
    }

    return {
        "reviewed_items": len(hugh_labels),
        "hermes_overlap_items": len(overlap),
        "event_counts": candidate_counts,
        "event_precision": candidate_precision,
        "event_recall": candidate_recall,
        "event_f1": candidate_f1,
        "strict_analyzable_event_counts": counts,
        "strict_analyzable_event_precision": precision,
        "strict_analyzable_event_recall": recall,
        "strict_analyzable_event_f1": f1,
        "candidate_detection_counts": candidate_counts,
        "candidate_detection_precision": candidate_precision,
        "candidate_detection_recall": candidate_recall,
        "candidate_detection_f1": candidate_f1,
        "event_type_accuracy": _safe_div(event_type_matches, event_type_denominator),
        "method_accuracy": method_accuracy,
        "method_denominator": method_denominator,
        "timing_boundary_median_absolute_error_seconds": boundary_median,
        "timing_start_median_absolute_error_seconds": median(start_errors) if start_errors else None,
        "timing_end_median_absolute_error_seconds": median(end_errors) if end_errors else None,
        "timing_start_boundaries": len(start_errors),
        "timing_end_boundaries": len(end_errors),
        "hugh_hermes_event_kappa": kappa,
        "hugh_hermes_event_disagreements": reviewer_disagreements,
        "detector_event_disagreements": sorted(detector_disagreements),
        "detector_method_disagreements": sorted(method_disagreements),
        "gates": gates,
        "all_gates_pass": all(gates.values()),
    }
