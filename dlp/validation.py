#!/usr/bin/env python3
"""Blinded validation-sample construction for the DLP event detector."""
from __future__ import annotations

import json
import random
from collections import defaultdict
from pathlib import Path
from statistics import median
from typing import Iterable, Sequence

from dlp.pipeline import Stall

PRIMARY_VEHICLE_TYPES = frozenset({"Car", "Medium Vehicle"})
CLASSIFIABLE_METHODS = frozenset({"forward", "reverse"})


def _is_detector_positive(event: dict) -> bool:
    return (
        event["complete"]
        and event["censoring"] == "none"
        and event["method"] in CLASSIFIABLE_METHODS
        and event["agent_type"] in PRIMARY_VEHICLE_TYPES
    )


def _balanced_sample(rows: Iterable[dict], count: int, rng: random.Random) -> list[dict]:
    by_scene: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_scene[row["scene_id"]].append(row)
    for group in by_scene.values():
        rng.shuffle(group)
    scenes = list(by_scene)
    rng.shuffle(scenes)

    selected: list[dict] = []
    while scenes and len(selected) < count:
        next_scenes: list[str] = []
        for scene in scenes:
            if by_scene[scene] and len(selected) < count:
                selected.append(by_scene[scene].pop())
            if by_scene[scene]:
                next_scenes.append(scene)
        scenes = next_scenes
    if len(selected) != count:
        raise ValueError(f"Requested {count} rows but only {len(selected)} were available")
    return selected


def _event_manifest_row(event: dict, source_kind: str) -> dict:
    return {
        "source_kind": source_kind,
        "scene_id": event["scene_id"],
        "scene_token": event["scene_token"],
        "agent_token": event["agent_token"],
        "agent_type": event["agent_type"],
        "event_id": event["event_id"],
        "detector_event_type": event["event_type"],
        "detector_method": event["method"],
        "detector_complete": event["complete"],
        "detector_censoring": event["censoring"],
        "detector_start_index": event["start_index"],
        "detector_end_index": event["end_index"],
        "detector_stall_id": event["stall_id"],
    }


def _boundary_category(event: dict) -> str:
    if event["censoring"] == "left":
        return "left_censored"
    if event["censoring"] == "right":
        return "right_censored"
    if event["method"] not in CLASSIFIABLE_METHODS:
        return "unclear_method"
    if event["agent_type"] not in PRIMARY_VEHICLE_TYPES:
        return "non_primary_vehicle"
    return "other"


def build_random_track_catalog(
    agents_by_scene: dict[str, dict[str, dict]], events: list[dict]
) -> list[dict]:
    """List primary vehicle tracks that have no detector candidate."""
    event_agents = {(event["scene_id"], event["agent_token"]) for event in events}
    catalog: list[dict] = []
    for scene_id, agents in sorted(agents_by_scene.items()):
        for agent_token, agent in sorted(agents.items()):
            if agent["type"] not in PRIMARY_VEHICLE_TYPES:
                continue
            if (scene_id, agent_token) in event_agents:
                continue
            catalog.append(
                {
                    "scene_id": scene_id,
                    "scene_token": agent["scene_token"],
                    "agent_token": agent_token,
                    "agent_type": agent["type"],
                }
            )
    return catalog


def load_scene_review_data(
    data_dir: Path, scene_id: str, wanted_tokens: set[str]
) -> tuple[dict[str, dict], dict[str, list[dict]], float]:
    """Load selected trajectories from one scene and enrich them with timestamps."""
    agents = json.loads((data_dir / f"{scene_id}_agents.json").read_text(encoding="utf-8"))
    frames = json.loads((data_dir / f"{scene_id}_frames.json").read_text(encoding="utf-8"))
    instances = json.loads(
        (data_dir / f"{scene_id}_instances.json").read_text(encoding="utf-8")
    )
    selected_agents = {token: agents[token] for token in wanted_tokens}
    timestamps = {token: float(frame["timestamp"]) for token, frame in frames.items()}
    ordered_times = sorted(timestamps.values())
    deltas = [later - earlier for earlier, later in zip(ordered_times, ordered_times[1:]) if later > earlier]
    if not deltas:
        raise ValueError(f"Scene {scene_id} has no positive frame intervals")
    fps = 1.0 / median(deltas)

    trajectories: dict[str, list[dict]] = {token: [] for token in wanted_tokens}
    for instance in instances.values():
        token = instance["agent_token"]
        if token not in wanted_tokens:
            continue
        row = dict(instance)
        row["timestamp"] = timestamps[instance["frame_token"]]
        trajectories[token].append(row)
    for rows in trajectories.values():
        rows.sort(key=lambda row: row["timestamp"])
    return selected_agents, trajectories, fps


def build_validation_manifest(
    events: list[dict], random_tracks: list[dict], *, seed: int
) -> list[dict]:
    """Build the frozen 100-positive/30-boundary/20-random manifest."""
    rng = random.Random(seed)
    selected: list[dict] = []

    for event_type in ("parking", "unparking"):
        for method in ("forward", "reverse"):
            pool = [
                event
                for event in events
                if _is_detector_positive(event)
                and event["event_type"] == event_type
                and event["method"] == method
            ]
            selected.extend(
                _event_manifest_row(event, "detector_positive")
                for event in _balanced_sample(pool, 25, rng)
            )

    boundary = [event for event in events if not _is_detector_positive(event)]
    by_category: dict[str, list[dict]] = defaultdict(list)
    for event in boundary:
        by_category[_boundary_category(event)].append(event)
    boundary_selected: list[dict] = []
    used: set[str] = set()
    for category, quota in (
        ("left_censored", 10),
        ("right_censored", 10),
        ("unclear_method", 5),
        ("non_primary_vehicle", 5),
    ):
        take = min(quota, len(by_category[category]))
        for event in _balanced_sample(by_category[category], take, rng) if take else []:
            boundary_selected.append(event)
            used.add(event["event_id"])
    if len(boundary_selected) < 30:
        remainder = [event for event in boundary if event["event_id"] not in used]
        boundary_selected.extend(_balanced_sample(remainder, 30 - len(boundary_selected), rng))
    selected.extend(_event_manifest_row(event, "boundary") for event in boundary_selected)

    event_agents = {(event["scene_id"], event["agent_token"]) for event in events}
    eligible_tracks = [
        track
        for track in random_tracks
        if (track["scene_id"], track["agent_token"]) not in event_agents
    ]
    for track in _balanced_sample(eligible_tracks, 20, rng):
        selected.append(
            {
                "source_kind": "random_track",
                "scene_id": track["scene_id"],
                "scene_token": track.get("scene_token"),
                "agent_token": track["agent_token"],
                "agent_type": track.get("agent_type"),
                "event_id": None,
                "detector_event_type": None,
                "detector_method": None,
                "detector_complete": None,
                "detector_censoring": None,
                "detector_start_index": None,
                "detector_end_index": None,
                "detector_stall_id": None,
            }
        )

    rng.shuffle(selected)
    for index, item in enumerate(selected, start=1):
        item["item_id"] = f"VAL-{index:03d}"
    return selected


def _manifest_boundary_category(item: dict) -> str:
    if item["detector_censoring"] == "left":
        return "left_censored"
    if item["detector_censoring"] == "right":
        return "right_censored"
    if item["detector_method"] not in CLASSIFIABLE_METHODS:
        return "unclear_method"
    if item["agent_type"] not in PRIMARY_VEHICLE_TYPES:
        return "non_primary_vehicle"
    return "other"


def select_reviewer_subset(
    manifest: list[dict], *, reviewer: str, seed: int
) -> list[dict]:
    """Select the frozen 32-positive/10-boundary/8-random reviewer set."""
    rng = random.Random(f"{seed}:{reviewer}")
    selected: list[dict] = []

    positives = [item for item in manifest if item["source_kind"] == "detector_positive"]
    for event_type in ("parking", "unparking"):
        for method in ("forward", "reverse"):
            pool = [
                item
                for item in positives
                if item["detector_event_type"] == event_type
                and item["detector_method"] == method
            ]
            selected.extend(_balanced_sample(pool, 8, rng))

    boundaries = [item for item in manifest if item["source_kind"] == "boundary"]
    by_category: dict[str, list[dict]] = defaultdict(list)
    for item in boundaries:
        by_category[_manifest_boundary_category(item)].append(item)
    boundary_selected: list[dict] = []
    used: set[str] = set()
    for category, quota in (
        ("left_censored", 3),
        ("right_censored", 3),
        ("unclear_method", 2),
        ("non_primary_vehicle", 2),
    ):
        take = min(quota, len(by_category[category]))
        for item in _balanced_sample(by_category[category], take, rng) if take else []:
            boundary_selected.append(item)
            used.add(item["item_id"])
    if len(boundary_selected) < 10:
        remainder = [item for item in boundaries if item["item_id"] not in used]
        boundary_selected.extend(_balanced_sample(remainder, 10 - len(boundary_selected), rng))
    selected.extend(boundary_selected)

    random_tracks = [item for item in manifest if item["source_kind"] == "random_track"]
    selected.extend(_balanced_sample(random_tracks, 8, rng))

    selected = [dict(item) for item in selected]
    rng.shuffle(selected)
    for order, item in enumerate(selected, start=1):
        item["reviewer"] = reviewer
        item["review_order"] = order
    return selected


def build_blind_index(subset: list[dict]) -> list[dict]:
    """Serialize only fields that cannot reveal detector predictions."""
    allowed = ("item_id", "review_order", "scene_id", "agent_token")
    return [
        {key: item[key] for key in allowed}
        for item in sorted(subset, key=lambda row: row["review_order"])
    ]


def build_review_payload(
    item: dict,
    agent: dict,
    rows: list[dict],
    *,
    fps: float,
    stalls: Sequence[Stall],
    context_seconds: float = 5.0,
) -> dict:
    """Create one blind trajectory payload for browser-based labeling."""
    if not rows:
        raise ValueError(f"No trajectory rows for {item['item_id']}")
    if item["source_kind"] == "random_track":
        start, end = 0, len(rows) - 1
    else:
        context = max(1, round(context_seconds * fps))
        start = max(0, int(item["detector_start_index"]) - context)
        end = min(len(rows) - 1, int(item["detector_end_index"]) + context)
    selected_rows = rows[start:end + 1]

    xmin = min(float(row["coords"][0]) for row in selected_rows) - 8.0
    xmax = max(float(row["coords"][0]) for row in selected_rows) + 8.0
    ymin = min(float(row["coords"][1]) for row in selected_rows) - 8.0
    ymax = max(float(row["coords"][1]) for row in selected_rows) + 8.0
    nearby_stalls = [
        stall
        for stall in stalls
        if stall.xmax >= xmin
        and stall.xmin <= xmax
        and stall.ymax >= ymin
        and stall.ymin <= ymax
    ]

    first_timestamp = float(selected_rows[0]["timestamp"])
    trajectory = [
        {
            "frame_index": frame_index,
            "timestamp": float(row["timestamp"]),
            "elapsed": float(row["timestamp"]) - first_timestamp,
            "x": float(row["coords"][0]),
            "y": float(row["coords"][1]),
            "heading": float(row["heading"]),
            "speed": float(row["speed"]),
        }
        for frame_index, row in enumerate(rows[start:end + 1], start=start)
    ]
    return {
        "item_id": item["item_id"],
        "review_order": item["review_order"],
        "scene_id": item["scene_id"],
        "agent_token": item["agent_token"],
        "vehicle_type": agent["type"],
        "vehicle_size": [float(value) for value in agent.get("size", (0.0, 0.0))],
        "fps": float(fps),
        "trajectory": trajectory,
        "stalls": [
            {
                "stall_id": stall.stall_id,
                "xmin": stall.xmin,
                "xmax": stall.xmax,
                "ymin": stall.ymin,
                "ymax": stall.ymax,
            }
            for stall in nearby_stalls
        ],
    }


def write_reviewer_package(
    output_dir: Path,
    reviewer: str,
    subset: list[dict],
    payloads: dict[str, dict],
    *,
    seed: int,
) -> Path:
    """Write the browser-visible index and blind payload files for one reviewer."""
    reviewer_dir = output_dir / reviewer
    items_dir = reviewer_dir / "items"
    items_dir.mkdir(parents=True, exist_ok=True)
    index_items = build_blind_index(subset)
    for item in index_items:
        item["payload_url"] = f"items/{item['item_id']}.json"
        payload = payloads[item["item_id"]]
        (items_dir / f"{item['item_id']}.json").write_text(
            json.dumps(payload, separators=(",", ":")), encoding="utf-8"
        )
    index = {
        "reviewer": reviewer,
        "seed": seed,
        "item_count": len(index_items),
        "items": index_items,
    }
    index_path = reviewer_dir / "index.json"
    index_path.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return index_path
