#!/usr/bin/env python3
"""Build frozen, blinded DLP manual-labeling packages."""
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
    build_validation_manifest,
    load_scene_review_data,
    select_reviewer_subset,
    write_reviewer_package,
)


def _read_events(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _reviewer_copy(items: list[dict], reviewer: str) -> list[dict]:
    return [
        dict(item, reviewer=reviewer, review_order=order)
        for order, item in enumerate(items, start=1)
    ]


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_package(data_dir: Path, event_path: Path, output_dir: Path, *, seed: int) -> dict:
    events = _read_events(event_path)
    scene_ids = sorted(path.name.removesuffix("_agents.json") for path in data_dir.glob("DJI_*_agents.json"))
    agents_by_scene = {
        scene_id: json.loads((data_dir / f"{scene_id}_agents.json").read_text(encoding="utf-8"))
        for scene_id in scene_ids
    }
    random_tracks = build_random_track_catalog(agents_by_scene, events)
    manifest = build_validation_manifest(events, random_tracks, seed=seed)
    hermes_subset = _reviewer_copy(manifest, "hermes")
    hugh_subset = select_reviewer_subset(manifest, reviewer="hugh", seed=seed)

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
            hermes_item = hermes_by_id[item["item_id"]]
            hermes_payloads[item["item_id"]] = build_review_payload(
                hermes_item, agents[token], trajectories[token], fps=fps, stalls=stalls
            )
            if item["item_id"] in hugh_by_id:
                hugh_item = hugh_by_id[item["item_id"]]
                hugh_payloads[item["item_id"]] = build_review_payload(
                    hugh_item, agents[token], trajectories[token], fps=fps, stalls=stalls
                )

    write_reviewer_package(output_dir, "hermes", hermes_subset, hermes_payloads, seed=seed)
    write_reviewer_package(output_dir, "hugh", hugh_subset, hugh_payloads, seed=seed)

    source_counts = Counter(item["source_kind"] for item in manifest)
    boundary_counts = Counter(
        item["detector_censoring"] if item["detector_censoring"] != "none"
        else (
            "unclear_method"
            if item["detector_method"] not in {"forward", "reverse"}
            else "non_primary_vehicle"
        )
        for item in manifest
        if item["source_kind"] == "boundary"
    )
    record = {
        "protocol_version": "1.0",
        "seed": seed,
        "manifest_sha256": manifest_sha256,
        "manifest_items": len(manifest),
        "hugh_items": len(hugh_subset),
        "source_counts": dict(sorted(source_counts.items())),
        "boundary_counts": dict(sorted(boundary_counts.items())),
    }
    (internal_dir / "sample_record.json").write_text(
        json.dumps(record, indent=2, sort_keys=True), encoding="utf-8"
    )
    return record


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=root / "data")
    parser.add_argument("--events", type=Path, default=root / "results" / "candidate_events.jsonl")
    parser.add_argument("--output", type=Path, default=root / "results" / "validation")
    parser.add_argument("--seed", type=int, default=20260723)
    args = parser.parse_args()
    record = build_package(args.data, args.events, args.output, seed=args.seed)
    print(json.dumps(record, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
