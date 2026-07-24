#!/usr/bin/env python3
"""Build the frozen, agent-disjoint Protocol v2 held-out review package."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

from dlp.pipeline import generate_stalls
from dlp.validation import (
    build_random_track_catalog,
    build_review_payload,
    build_v2_validation_manifest,
    load_scene_review_data,
    select_reviewer_subset,
    write_reviewer_package,
)


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _reviewer_copy(items: list[dict], reviewer: str) -> list[dict]:
    return [
        dict(item, reviewer=reviewer, review_order=order)
        for order, item in enumerate(items, start=1)
    ]


def add_reviewer_weights(subset: list[dict], manifest: list[dict]) -> list[dict]:
    """Apply the second-stage reviewer sampling factor to manifest weights."""
    population_counts = Counter(item["sampling_stratum"] for item in manifest)
    sample_counts = Counter(item["sampling_stratum"] for item in subset)
    weighted: list[dict] = []
    for original in subset:
        item = dict(original)
        stratum = item["sampling_stratum"]
        population_count = population_counts[stratum]
        sample_count = sample_counts[stratum]
        reviewer_weight = population_count / sample_count
        item.update(
            review_population_count=population_count,
            review_sample_count=sample_count,
            review_sampling_weight=reviewer_weight,
            analysis_weight=float(item["sampling_weight"]) * reviewer_weight,
        )
        weighted.append(item)
    return weighted


def build_v2_package(
    data_dir: Path,
    event_path: Path,
    development_manifest_path: Path,
    output_dir: Path,
    *,
    seed: int,
    detector_commit: str,
) -> dict:
    """Create a fresh package and return its immutable freeze record."""
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"Held-out output must be absent or empty: {output_dir}")

    events = _read_events(event_path)
    development_manifest = json.loads(development_manifest_path.read_text(encoding="utf-8"))
    excluded_agents = {
        (row["scene_id"], row["agent_token"])
        for row in development_manifest
    }
    scene_ids = sorted(
        path.name.removesuffix("_agents.json")
        for path in data_dir.glob("DJI_*_agents.json")
    )
    agents_by_scene = {
        scene_id: json.loads(
            (data_dir / f"{scene_id}_agents.json").read_text(encoding="utf-8")
        )
        for scene_id in scene_ids
    }
    random_tracks = build_random_track_catalog(agents_by_scene, events)
    manifest = build_v2_validation_manifest(
        events,
        random_tracks,
        excluded_agents=excluded_agents,
        seed=seed,
    )

    sampled_agents = [(row["scene_id"], row["agent_token"]) for row in manifest]
    if len(set(sampled_agents)) != len(sampled_agents):
        raise ValueError("Protocol v2 manifest contains a duplicate agent")
    overlap = sorted(set(sampled_agents) & excluded_agents)
    if overlap:
        raise ValueError(f"Protocol v2 manifest reuses development agents: {overlap[:5]}")

    hermes_subset = add_reviewer_weights(_reviewer_copy(manifest, "hermes"), manifest)
    hugh_subset = add_reviewer_weights(
        select_reviewer_subset(manifest, reviewer="hugh", seed=seed),
        manifest,
    )
    internal_dir = output_dir / "internal"
    internal_dir.mkdir(parents=True, exist_ok=True)
    manifest_bytes = _canonical_bytes(manifest)
    manifest_sha256 = hashlib.sha256(manifest_bytes).hexdigest()
    (internal_dir / "manifest.json").write_bytes(manifest_bytes)
    (internal_dir / "hugh_subset.json").write_text(
        json.dumps(hugh_subset, indent=2, sort_keys=True), encoding="utf-8"
    )

    by_scene: dict[str, list[dict]] = defaultdict(list)
    for item in manifest:
        by_scene[item["scene_id"]].append(item)
    hermes_by_id = {item["item_id"]: item for item in hermes_subset}
    hugh_by_id = {item["item_id"]: item for item in hugh_subset}
    hermes_payloads: dict[str, dict] = {}
    hugh_payloads: dict[str, dict] = {}
    stalls = generate_stalls()

    for number, scene_id in enumerate(sorted(by_scene), start=1):
        items = by_scene[scene_id]
        wanted = {item["agent_token"] for item in items}
        print(f"[{number:02d}/{len(by_scene):02d}] {scene_id}: {len(items)} items", flush=True)
        agents, trajectories, fps = load_scene_review_data(data_dir, scene_id, wanted)
        for item in items:
            token = item["agent_token"]
            item_id = item["item_id"]
            hermes_payloads[item_id] = build_review_payload(
                hermes_by_id[item_id],
                agents[token],
                trajectories[token],
                fps=fps,
                stalls=stalls,
            )
            if item_id in hugh_by_id:
                hugh_payloads[item_id] = build_review_payload(
                    hugh_by_id[item_id],
                    agents[token],
                    trajectories[token],
                    fps=fps,
                    stalls=stalls,
                )

    write_reviewer_package(output_dir, "hermes", hermes_subset, hermes_payloads, seed=seed)
    write_reviewer_package(output_dir, "hugh", hugh_subset, hugh_payloads, seed=seed)

    strata: dict[str, dict] = {}
    for item in manifest:
        name = item["sampling_stratum"]
        strata.setdefault(
            name,
            {
                "population_count": item["population_count"],
                "sample_count": item["sample_count"],
                "sampling_weight": item["sampling_weight"],
            },
        )
    root = Path(__file__).resolve().parent
    source_counts = Counter(item["source_kind"] for item in manifest)
    hugh_review_strata = {
        name: {
            "manifest_count": next(
                item["review_population_count"]
                for item in hugh_subset
                if item["sampling_stratum"] == name
            ),
            "review_count": next(
                item["review_sample_count"]
                for item in hugh_subset
                if item["sampling_stratum"] == name
            ),
            "review_sampling_weight": next(
                item["review_sampling_weight"]
                for item in hugh_subset
                if item["sampling_stratum"] == name
            ),
        }
        for name in sorted({item["sampling_stratum"] for item in hugh_subset})
    }
    freeze_record = {
        "purpose": "untouched agent-disjoint detector-v2 promotion validation",
        "protocol_version": "2.0-heldout",
        "seed": seed,
        "detector_commit": detector_commit,
        "detector_pipeline_sha256": _sha256(root / "pipeline.py"),
        "detector_spec_sha256": _sha256(root / "DETECTOR_V2_SPEC.md"),
        "validation_selection_sha256": _sha256(root / "validation.py"),
        "package_builder_sha256": _sha256(root / "build_v2_validation_package.py"),
        "label_server_sha256": _sha256(root / "label_server.py"),
        "labeler_index_sha256": _sha256(root / "labeler" / "index.html"),
        "labeler_app_sha256": _sha256(root / "labeler" / "app.js"),
        "labeler_styles_sha256": _sha256(root / "labeler" / "styles.css"),
        "event_ledger_sha256": _sha256(event_path),
        "development_manifest_sha256": _sha256(development_manifest_path),
        "excluded_development_agents": len(excluded_agents),
        "agent_disjoint": True,
        "one_item_per_agent": True,
        "manifest_sha256": manifest_sha256,
        "manifest_items": len(manifest),
        "hugh_items": len(hugh_subset),
        "hermes_items": len(hermes_subset),
        "source_counts": dict(sorted(source_counts.items())),
        "sampling_strata": dict(sorted(strata.items())),
        "hugh_review_strata": hugh_review_strata,
        "labels_present_at_freeze": False,
    }
    (internal_dir / "freeze_record.json").write_text(
        json.dumps(freeze_record, indent=2, sort_keys=True), encoding="utf-8"
    )
    return freeze_record


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=root / "data")
    parser.add_argument(
        "--events",
        type=Path,
        default=root / "results" / "v2-semantic-development" / "candidate_events.jsonl",
    )
    parser.add_argument(
        "--development-manifest",
        type=Path,
        default=root / "results" / "validation" / "internal" / "manifest.json",
    )
    parser.add_argument(
        "--output", type=Path, default=root / "results" / "v2-heldout"
    )
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--detector-commit", required=True)
    args = parser.parse_args()
    record = build_v2_package(
        args.data,
        args.events,
        args.development_manifest,
        args.output,
        seed=args.seed,
        detector_commit=args.detector_commit,
    )
    print(json.dumps(record, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
