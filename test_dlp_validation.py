from collections import Counter
import json
from pathlib import Path

from dlp.validation import (
    build_blind_index,
    build_random_track_catalog,
    build_review_payload,
    build_validation_manifest,
    load_scene_review_data,
    select_reviewer_subset,
    write_reviewer_package,
)
from dlp.pipeline import Stall


def _event(index, event_type, method, *, complete=True, censoring="none", agent_type="Car"):
    return {
        "event_id": f"event-{index}",
        "scene_id": f"DJI_{index % 30 + 1:04d}",
        "scene_token": f"scene-token-{index}",
        "agent_token": f"agent-token-{index}",
        "agent_type": agent_type,
        "event_type": event_type,
        "method": method,
        "complete": complete,
        "censoring": censoring,
        "start_index": 10,
        "end_index": 20,
        "stall_id": "A-R1-C01",
    }


def _synthetic_inputs():
    events = []
    index = 0
    for event_type in ("parking", "unparking"):
        for method in ("forward", "reverse"):
            for _ in range(30):
                events.append(_event(index, event_type, method))
                index += 1

    for _ in range(12):
        events.append(_event(index, "parking", "unclear", complete=False, censoring="left"))
        index += 1
    for _ in range(12):
        events.append(_event(index, "unparking", "unclear", complete=False, censoring="right"))
        index += 1
    for _ in range(6):
        events.append(_event(index, "parking", "unclear"))
        index += 1
    for _ in range(6):
        events.append(_event(index, "unparking", "forward", agent_type="Motorcycle"))
        index += 1

    random_tracks = [
        {"scene_id": f"DJI_{i % 30 + 1:04d}", "agent_token": f"random-agent-{i}"}
        for i in range(25)
    ]
    return events, random_tracks


def test_build_validation_manifest_freezes_balanced_150_item_sample():
    events, random_tracks = _synthetic_inputs()

    first = build_validation_manifest(events, random_tracks, seed=20260723)
    second = build_validation_manifest(events, random_tracks, seed=20260723)

    assert first == second
    assert len(first) == 150
    assert [item["item_id"] for item in first] == [f"VAL-{i:03d}" for i in range(1, 151)]
    assert Counter(item["source_kind"] for item in first) == {
        "detector_positive": 100,
        "boundary": 30,
        "random_track": 20,
    }
    positive_cells = Counter(
        (item["detector_event_type"], item["detector_method"])
        for item in first
        if item["source_kind"] == "detector_positive"
    )
    assert positive_cells == {
        ("parking", "forward"): 25,
        ("parking", "reverse"): 25,
        ("unparking", "forward"): 25,
        ("unparking", "reverse"): 25,
    }
    assert len({(item["scene_id"], item["agent_token"]) for item in first if item["source_kind"] == "random_track"}) == 20


def test_hugh_subset_is_stratified_and_blind_to_detector_predictions():
    events, random_tracks = _synthetic_inputs()
    manifest = build_validation_manifest(events, random_tracks, seed=20260723)

    subset = select_reviewer_subset(manifest, reviewer="hugh", seed=20260723)
    blind = build_blind_index(subset)

    assert len(subset) == 50
    assert Counter(item["source_kind"] for item in subset) == {
        "detector_positive": 32,
        "boundary": 10,
        "random_track": 8,
    }
    assert Counter(
        (item["detector_event_type"], item["detector_method"])
        for item in subset
        if item["source_kind"] == "detector_positive"
    ) == {
        ("parking", "forward"): 8,
        ("parking", "reverse"): 8,
        ("unparking", "forward"): 8,
        ("unparking", "reverse"): 8,
    }
    forbidden = {
        "source_kind",
        "detector_event_type",
        "detector_method",
        "detector_complete",
        "detector_censoring",
        "detector_start_index",
        "detector_end_index",
        "detector_stall_id",
    }
    assert all(not forbidden.intersection(item) for item in blind)
    assert [item["review_order"] for item in blind] == list(range(1, 51))


def test_review_payload_adds_context_without_exposing_detector_boundaries():
    rows = [
        {
            "coords": [float(index), 1.0],
            "heading": 0.0,
            "speed": 1.0,
            "timestamp": float(index),
        }
        for index in range(30)
    ]
    item = {
        "item_id": "VAL-007",
        "review_order": 1,
        "source_kind": "detector_positive",
        "scene_id": "DJI_0001",
        "agent_token": "agent-1",
        "detector_start_index": 10,
        "detector_end_index": 20,
        "detector_crossing_index": 14,
    }
    agent = {"type": "Car", "size": [4.5, 2.0]}
    stalls = (
        Stall("X-R1-C01", "X", 1, 1, 0.0, 4.0, 0.0, 2.0),
        Stall("X-R1-C02", "X", 1, 2, 40.0, 44.0, 0.0, 2.0),
    )

    payload = build_review_payload(item, agent, rows, fps=1.0, stalls=stalls)

    assert payload["item_id"] == "VAL-007"
    assert payload["review_anchor_index"] == 14
    assert [point["frame_index"] for point in payload["trajectory"]] == list(range(5, 26))
    assert payload["stalls"] == [
        {"stall_id": "X-R1-C01", "xmin": 0.0, "xmax": 4.0, "ymin": 0.0, "ymax": 2.0}
    ]
    assert not any("detector" in key or key == "source_kind" for key in payload)

    random_item = dict(item, source_kind="random_track", detector_crossing_index=None)
    random_payload = build_review_payload(random_item, agent, rows, fps=1.0, stalls=stalls)
    assert random_payload["review_anchor_index"] == 14
    assert [point["frame_index"] for point in random_payload["trajectory"]] == list(range(30))


def test_random_track_catalog_excludes_event_agents_and_non_primary_vehicles():
    agents_by_scene = {
        "DJI_0001": {
            "event-agent": {"agent_token": "event-agent", "scene_token": "scene-1", "type": "Car"},
            "eligible-car": {"agent_token": "eligible-car", "scene_token": "scene-1", "type": "Car"},
            "eligible-medium": {"agent_token": "eligible-medium", "scene_token": "scene-1", "type": "Medium Vehicle"},
            "motorcycle": {"agent_token": "motorcycle", "scene_token": "scene-1", "type": "Motorcycle"},
        }
    }
    events = [{"scene_id": "DJI_0001", "agent_token": "event-agent"}]

    catalog = build_random_track_catalog(agents_by_scene, events)

    assert catalog == [
        {
            "scene_id": "DJI_0001",
            "scene_token": "scene-1",
            "agent_token": "eligible-car",
            "agent_type": "Car",
        },
        {
            "scene_id": "DJI_0001",
            "scene_token": "scene-1",
            "agent_token": "eligible-medium",
            "agent_type": "Medium Vehicle",
        },
    ]


def test_load_scene_review_data_keeps_only_requested_sorted_trajectories(tmp_path: Path):
    agents = {
        "wanted": {"agent_token": "wanted", "scene_token": "scene-1", "type": "Car", "size": [4.5, 2.0]},
        "other": {"agent_token": "other", "scene_token": "scene-1", "type": "Car", "size": [4.4, 1.9]},
    }
    frames = {
        "frame-0": {"timestamp": 0.0},
        "frame-1": {"timestamp": 0.5},
        "frame-2": {"timestamp": 1.0},
    }
    instances = {
        "late": {"agent_token": "wanted", "frame_token": "frame-2", "coords": [2.0, 0.0], "heading": 0.0, "speed": 1.0},
        "early": {"agent_token": "wanted", "frame_token": "frame-0", "coords": [0.0, 0.0], "heading": 0.0, "speed": 1.0},
        "ignored": {"agent_token": "other", "frame_token": "frame-1", "coords": [1.0, 0.0], "heading": 0.0, "speed": 1.0},
    }
    for suffix, payload in (("agents", agents), ("frames", frames), ("instances", instances)):
        (tmp_path / f"DJI_0001_{suffix}.json").write_text(json.dumps(payload))

    loaded_agents, trajectories, fps = load_scene_review_data(
        tmp_path, "DJI_0001", {"wanted"}
    )

    assert loaded_agents == {"wanted": agents["wanted"]}
    assert fps == 2.0
    assert [row["timestamp"] for row in trajectories["wanted"]] == [0.0, 1.0]
    assert "other" not in trajectories


def test_write_reviewer_package_serves_only_blind_index_and_payloads(tmp_path: Path):
    subset = [{
        "item_id": "VAL-001",
        "review_order": 1,
        "reviewer": "hugh",
        "scene_id": "DJI_0001",
        "agent_token": "agent-1",
        "source_kind": "detector_positive",
        "detector_method": "reverse",
    }]
    payloads = {"VAL-001": {"item_id": "VAL-001", "trajectory": [{"frame_index": 1}]}}

    write_reviewer_package(tmp_path, "hugh", subset, payloads, seed=20260723)

    index = json.loads((tmp_path / "hugh" / "index.json").read_text())
    saved_payload = json.loads((tmp_path / "hugh" / "items" / "VAL-001.json").read_text())
    assert index["reviewer"] == "hugh"
    assert index["seed"] == 20260723
    assert index["items"] == [{
        "item_id": "VAL-001",
        "review_order": 1,
        "scene_id": "DJI_0001",
        "agent_token": "agent-1",
        "payload_url": "items/VAL-001.json",
    }]
    assert saved_payload == payloads["VAL-001"]
    assert "detector_method" not in (tmp_path / "hugh" / "index.json").read_text()
