#!/usr/bin/env python3
"""Run the bounded detector-v2 exploratory timing and sensitivity analysis."""
from __future__ import annotations

import argparse
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Callable

GROUP_KEYS = (
    ("parking", "forward"),
    ("parking", "reverse"),
    ("unparking", "forward"),
    ("unparking", "reverse"),
)
PRIMARY_AGENT_TYPES = frozenset({"Car", "Medium Vehicle"})
CLASSIFIABLE_METHODS = frozenset({"forward", "reverse"})
EVENT_TYPES = frozenset({"parking", "unparking"})


def primary_events(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if row["complete"]
        and row["censoring"] == "none"
        and row["agent_type"] in PRIMARY_AGENT_TYPES
        and row["method"] in CLASSIFIABLE_METHODS
    ]


def group_values(rows: list[dict]) -> dict[tuple[str, str], list[float]]:
    return {
        key: [
            float(row["duration_seconds"])
            for row in rows
            if (row["event_type"], row["method"]) == key
        ]
        for key in GROUP_KEYS
    }


def lifecycle_contrast(
    groups: dict[tuple[str, str], list[float]],
    average: Callable[[list[float]], float] = statistics.mean,
) -> float:
    return (
        average(groups[("parking", "forward")])
        + average(groups[("unparking", "reverse")])
        - average(groups[("parking", "reverse")])
        - average(groups[("unparking", "forward")])
    )


def trimmed_mean(values: list[float], fraction: float = 0.10) -> float:
    ordered = sorted(values)
    cut = math.floor(len(ordered) * fraction)
    kept = ordered[cut : len(ordered) - cut] if cut else ordered
    return statistics.mean(kept)


def quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    fraction = position - lower
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def weighted_median(values: list[tuple[float, float]]) -> float:
    ordered = sorted(values)
    halfway = sum(weight for _, weight in ordered) / 2
    cumulative = 0.0
    for value, weight in ordered:
        cumulative += weight
        if cumulative >= halfway:
            return value
    raise ValueError("weighted median requires at least one value")


def adverse_primary_false_positive_contrast(
    groups: dict[tuple[str, str], list[float]],
    forward_parking_false_positive_rate: float,
) -> dict:
    adjusted = {key: sorted(values) for key, values in groups.items()}
    parking_forward = adjusted[("parking", "forward")]
    remove = math.ceil(len(parking_forward) * forward_parking_false_positive_rate)
    if remove >= len(parking_forward):
        raise ValueError("false-positive stress would remove the whole group")
    adjusted[("parking", "forward")] = parking_forward[remove:]
    return {
        "removed_forward_parking": remove,
        "contrast_seconds": lifecycle_contrast(adjusted),
    }


def scene_cluster_bootstrap(
    rows: list[dict], *, seed: int, draws: int
) -> list[float]:
    scenes = sorted({row["scene_id"] for row in rows})
    by_scene = {
        scene: [row for row in rows if row["scene_id"] == scene]
        for scene in scenes
    }
    rng = random.Random(seed)
    results: list[float] = []
    for _ in range(draws):
        sampled: list[dict] = []
        for scene in rng.choices(scenes, k=len(scenes)):
            sampled.extend(by_scene[scene])
        groups = group_values(sampled)
        if all(groups[key] for key in GROUP_KEYS):
            results.append(lifecycle_contrast(groups))
    return results


def validation_metrics(validation_root: Path) -> dict:
    labels = json.loads(
        (validation_root / "internal" / "hugh_labels_sealed.json").read_text()
    )
    rows = {
        row["item_id"]: row
        for row in json.loads(
            (validation_root / "internal" / "hugh_subset.json").read_text()
        )
    }
    confusion: dict[str, float] = defaultdict(float)
    primary_confusion: dict[str, float] = defaultdict(float)
    method_correct = 0.0
    method_total = 0.0
    boundary_errors: list[tuple[str, float, float]] = []

    for item_id, label in labels.items():
        row = rows[item_id]
        weight = float(row["analysis_weight"])
        human_event = label["event_type"] in EVENT_TYPES
        detector_candidate = row["source_kind"] != "random_track"
        key = (
            "tp" if human_event and detector_candidate
            else "fp" if detector_candidate
            else "fn" if human_event
            else "tn"
        )
        confusion[key] += weight
        if row["source_kind"] == "detector_positive":
            primary_confusion["tp" if human_event else "fp"] += weight

        eligible = label["exclusion_reason"] == "none"
        matched = (
            eligible
            and human_event
            and detector_candidate
            and label["event_type"] == row["detector_event_type"]
            and label["method"] in CLASSIFIABLE_METHODS
            and row["detector_method"] in CLASSIFIABLE_METHODS
        )
        if not matched:
            continue
        method_total += weight
        method_correct += weight * (label["method"] == row["detector_method"])
        payload = json.loads(
            (validation_root / "hugh" / "items" / f"{item_id}.json").read_text()
        )
        timestamps = {
            point["frame_index"]: point["timestamp"]
            for point in payload["trajectory"]
        }
        if (
            label["censoring"] != "left"
            and label["start_index"] is not None
            and label["start_index"] in timestamps
            and row["detector_start_index"] in timestamps
        ):
            boundary_errors.append(
                (
                    "start",
                    abs(
                        timestamps[label["start_index"]]
                        - timestamps[row["detector_start_index"]]
                    ),
                    weight,
                )
            )
        if (
            label["censoring"] != "right"
            and label["end_index"] is not None
            and label["end_index"] in timestamps
            and row["detector_end_index"] in timestamps
        ):
            boundary_errors.append(
                (
                    "end",
                    abs(
                        timestamps[label["end_index"]]
                        - timestamps[row["detector_end_index"]]
                    ),
                    weight,
                )
            )

    precision = confusion["tp"] / (confusion["tp"] + confusion["fp"])
    recall = confusion["tp"] / (confusion["tp"] + confusion["fn"])
    primary_precision = primary_confusion["tp"] / sum(primary_confusion.values())
    medians = {
        name: weighted_median(
            [(error, weight) for kind, error, weight in boundary_errors if kind == name]
        )
        for name in ("start", "end")
    }
    medians["overall"] = weighted_median(
        [(error, weight) for _, error, weight in boundary_errors]
    )
    return {
        "broad_weighted_precision": precision,
        "broad_weighted_recall": recall,
        "strict_primary_weighted_precision": primary_precision,
        "weighted_method_accuracy": method_correct / method_total,
        "weighted_boundary_median_seconds": medians,
        "timing_components": len(boundary_errors),
        "forward_parking_false_positive_rate": 0.125,
    }


def analyze(
    event_path: Path,
    validation_root: Path,
    *,
    seed: int,
    bootstrap_draws: int,
) -> dict:
    rows = [json.loads(line) for line in event_path.read_text().splitlines() if line]
    primary = primary_events(rows)
    groups = group_values(primary)
    validation = validation_metrics(validation_root)
    raw_contrast = lifecycle_contrast(groups)
    trimmed_contrast = lifecycle_contrast(
        groups, lambda values: trimmed_mean(values, 0.10)
    )
    bootstrap = scene_cluster_bootstrap(
        primary, seed=seed, draws=bootstrap_draws
    )
    adverse_fp = adverse_primary_false_positive_contrast(
        groups, validation["forward_parking_false_positive_rate"]
    )
    start_error = validation["weighted_boundary_median_seconds"]["start"]
    end_error = validation["weighted_boundary_median_seconds"]["end"]
    observed_boundary_shift = 4 * (start_error + end_error)
    group_summary = {
        f"{event_type}:{method}": {
            "n": len(groups[(event_type, method)]),
            "mean_seconds": statistics.mean(groups[(event_type, method)]),
            "median_seconds": statistics.median(groups[(event_type, method)]),
            "trimmed_10_mean_seconds": trimmed_mean(groups[(event_type, method)]),
        }
        for event_type, method in GROUP_KEYS
    }
    parking_forward = statistics.mean(groups[("parking", "forward")])
    parking_reverse = statistics.mean(groups[("parking", "reverse")])
    unparking_forward = statistics.mean(groups[("unparking", "forward")])
    unparking_reverse = statistics.mean(groups[("unparking", "reverse")])
    return {
        "status": "exploratory_not_formally_validated",
        "inputs": {
            "candidate_events": len(rows),
            "primary_events": len(primary),
            "seed": seed,
            "bootstrap_draws": bootstrap_draws,
        },
        "validation": validation,
        "groups": group_summary,
        "contrasts": {
            "entry_forward_minus_reverse_seconds": parking_forward - parking_reverse,
            "exit_forward_minus_reverse_seconds": unparking_forward - unparking_reverse,
            "nose_in_lifecycle_seconds": parking_forward + unparking_reverse,
            "nose_out_lifecycle_seconds": parking_reverse + unparking_forward,
            "nose_in_minus_nose_out_seconds": raw_contrast,
            "trimmed_10_nose_in_minus_nose_out_seconds": trimmed_contrast,
        },
        "scene_cluster_bootstrap": {
            "successful_draws": len(bootstrap),
            "ci95_seconds": [quantile(bootstrap, 0.025), quantile(bootstrap, 0.975)],
            "probability_nose_in_faster": sum(value < 0 for value in bootstrap) / len(bootstrap),
            "probability_beyond_minus_2_seconds": sum(value < -2 for value in bootstrap) / len(bootstrap),
        },
        "sensitivity": {
            "adverse_strict_primary_false_positive": adverse_fp,
            "adverse_fp_plus_observed_boundary_medians_seconds": adverse_fp["contrast_seconds"] + observed_boundary_shift,
            "raw_plus_observed_boundary_medians_seconds": raw_contrast + observed_boundary_shift,
            "adverse_fp_plus_one_second_each_boundary_seconds": adverse_fp["contrast_seconds"] + 8,
            "interpretation": "Negative favors nose-in; values below -2 s exceed the frozen practical margin.",
        },
    }


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--events",
        type=Path,
        default=root / "results" / "v2-semantic-development" / "candidate_events.jsonl",
    )
    parser.add_argument(
        "--validation",
        type=Path,
        default=root / "results" / "v2-heldout",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "results" / "v2-exploratory-analysis.json",
    )
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--bootstrap-draws", type=int, default=10000)
    args = parser.parse_args()
    result = analyze(
        args.events,
        args.validation,
        seed=args.seed,
        bootstrap_draws=args.bootstrap_draws,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
