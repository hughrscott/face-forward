import json
from pathlib import Path

import pytest

from dlp.pipeline import (
    Stall,
    classify_stall_crossing,
    detect_trajectory_events,
    discover_scene_prefixes,
    generate_stalls,
    inventory_scene,
    obstacle_stall_coverage,
    run_inventory,
    stall_for_point,
    write_inventory_outputs,
)


def test_discover_scene_prefixes_requires_complete_five_file_bundles(tmp_path: Path):
    suffixes = ("agents", "frames", "instances", "obstacles", "scene")
    for scene in ("DJI_0002", "DJI_0001"):
        for suffix in suffixes:
            (tmp_path / f"{scene}_{suffix}.json").touch()

    assert discover_scene_prefixes(tmp_path) == ["DJI_0001", "DJI_0002"]

    (tmp_path / "DJI_0002_frames.json").unlink()
    with pytest.raises(ValueError, match="DJI_0002.*frames"):
        discover_scene_prefixes(tmp_path)


def test_generate_stalls_matches_published_layout_and_resolves_points():
    stalls = generate_stalls()

    assert len(stalls) == 364
    assert len({stall.stall_id for stall in stalls}) == 364
    assert sum(stall.area == "B" for stall in stalls) == 50

    first_b = next(stall for stall in stalls if stall.stall_id == "B-R1-C01")
    assert first_b.xmin == pytest.approx(7.71)
    assert first_b.xmax == pytest.approx(7.71 + (76.54 - 7.71) / 25)
    assert first_b.ymin == pytest.approx(50.40)
    assert first_b.ymax == pytest.approx(50.40 + (61.40 - 50.40) / 2)
    assert stall_for_point(first_b.center, stalls) == first_b
    assert stall_for_point((0.0, 0.0), stalls) is None


def test_classify_stall_crossing_uses_final_crossing_not_forward_setup():
    stall = Stall("X-R1-C01", "X", 1, 1, 0.0, 2.0, 0.0, 2.0)
    rows = [
        {"coords": [12.0, 1.0], "heading": 0.0, "speed": 1.0},
        {"coords": [14.0, 1.0], "heading": 0.0, "speed": 1.0},  # forward setup
        {"coords": [10.0, 1.0], "heading": 0.0, "speed": 2.0},
        {"coords": [4.0, 1.0], "heading": 0.0, "speed": 2.0},
        {"coords": [1.0, 1.0], "heading": 0.0, "speed": 1.0},  # reverse crossing
        {"coords": [1.0, 1.0], "heading": 0.0, "speed": 0.0},
    ]

    method, confidence, crossing = classify_stall_crossing(rows, stall, "parking")

    assert method == "reverse"
    assert confidence == pytest.approx(1.0)
    assert crossing == 4


def test_classify_stall_crossing_uses_nearest_moving_evidence_when_crossing_is_slow():
    stall = Stall("X-R1-C01", "X", 1, 1, 0.0, 2.0, 0.0, 2.0)
    rows = [
        {"coords": [10.0, 1.0], "heading": 0.0, "speed": 1.0},
        {"coords": [8.0, 1.0], "heading": 0.0, "speed": 1.0},
        {"coords": [6.0, 1.0], "heading": 0.0, "speed": 1.0},
        {"coords": [4.0, 1.0], "heading": 0.0, "speed": 0.02},
        {"coords": [2.5, 1.0], "heading": 0.0, "speed": 0.02},
        {"coords": [1.9, 1.0], "heading": 0.0, "speed": 0.02},
        {"coords": [1.5, 1.0], "heading": 0.0, "speed": 0.02},
    ]

    method, confidence, crossing = classify_stall_crossing(
        rows,
        stall,
        "parking",
        evidence_frames=5,
        minimum_motion_samples=3,
    )

    assert method == "reverse"
    assert confidence == pytest.approx(1.0)
    assert crossing == 5


def test_detect_trajectory_events_preserves_provenance_and_boundaries():
    stall = Stall("X-R1-C01", "X", 1, 1, 0.0, 2.0, 0.0, 2.0)
    coords_and_speeds = [
        ([12.0, 1.0], 1.0),
        ([14.0, 1.0], 1.0),  # forward setup
        ([10.0, 1.0], 2.0),
        ([4.0, 1.0], 2.0),   # first frame inside 8 m envelope
        ([1.0, 1.0], 1.0),   # reverse stall crossing
        ([1.0, 1.0], 0.0),
        ([1.0, 1.0], 0.0),
        ([1.0, 1.0], 0.0),
    ]
    rows = [
        {
            "instance_token": f"instance-{index}",
            "coords": coords,
            "heading": 0.0,
            "speed": speed,
            "timestamp": float(index),
        }
        for index, (coords, speed) in enumerate(coords_and_speeds)
    ]
    agent = {"agent_token": "full-agent-token", "type": "Car", "size": [4.5, 2.0]}

    events = detect_trajectory_events(
        "DJI_0099",
        "full-scene-token",
        agent,
        rows,
        (stall,),
        fps=1.0,
    )

    assert len(events) == 1
    event = events[0]
    assert event.scene_id == "DJI_0099"
    assert event.scene_token == "full-scene-token"
    assert event.agent_token == "full-agent-token"
    assert event.event_type == "parking"
    assert event.stall_id == stall.stall_id
    assert event.method == "reverse"
    assert event.start_index == 3
    assert event.end_index == 5
    assert event.duration_seconds == pytest.approx(2.0)
    assert event.censoring == "none"
    assert event.complete is True


def test_detect_unparking_classifies_initial_stall_exit():
    stall = Stall("X-R1-C01", "X", 1, 1, 0.0, 2.0, 0.0, 2.0)
    coords_and_speeds = [
        ([1.0, 1.0], 0.0),
        ([1.0, 1.0], 0.0),
        ([1.0, 1.0], 0.0),
        ([2.5, 1.0], 1.0),  # forward stall exit
        ([4.0, 1.0], 1.0),
        ([10.0, 1.0], 1.0), # first frame outside 8 m envelope
    ]
    rows = [
        {
            "instance_token": f"instance-{index}",
            "coords": coords,
            "heading": 0.0,
            "speed": speed,
            "timestamp": float(index),
        }
        for index, (coords, speed) in enumerate(coords_and_speeds)
    ]
    agent = {"agent_token": "full-agent-token", "type": "Car", "size": [4.5, 2.0]}

    events = detect_trajectory_events(
        "DJI_0099", "full-scene-token", agent, rows, (stall,), fps=1.0
    )

    assert len(events) == 1
    event = events[0]
    assert event.event_type == "unparking"
    assert event.method == "forward"
    assert event.crossing_index == 3
    assert event.start_index == 3
    assert event.end_index == 5
    assert event.duration_seconds == pytest.approx(2.0)
    assert event.complete is True


def test_same_stall_repositioning_forms_one_parked_episode():
    stall = Stall("X-R1-C01", "X", 1, 1, 0.0, 2.0, 0.0, 2.0)
    coords_and_speeds = [
        ([1.0, 1.0], 0.0),
        ([1.0, 1.0], 0.0),
        ([1.0, 1.0], 0.0),
        ([1.5, 1.0], 1.0),  # brief repositioning entirely inside the stall
        ([1.5, 1.0], 0.0),
        ([1.5, 1.0], 0.0),
        ([1.5, 1.0], 0.0),
        ([3.0, 1.0], 1.0),  # actual departure
        ([5.0, 1.0], 1.0),
        ([10.0, 1.0], 1.0),
    ]
    rows = [
        {
            "instance_token": f"instance-{index}",
            "coords": coords,
            "heading": 0.0,
            "speed": speed,
            "timestamp": float(index),
        }
        for index, (coords, speed) in enumerate(coords_and_speeds)
    ]
    agent = {"agent_token": "full-agent-token", "type": "Car", "size": [4.5, 2.0]}

    events = detect_trajectory_events(
        "DJI_0099", "full-scene-token", agent, rows, (stall,), fps=1.0
    )

    assert len(events) == 1
    assert events[0].event_type == "unparking"
    assert events[0].start_index == 7


def test_inventory_scene_reads_bundle_and_returns_events_without_aggregation(tmp_path: Path):
    prefix = "DJI_0099"
    scene_token = "full-scene-token"
    agent_token = "full-agent-token"
    coords_and_speeds = [
        ([12.0, 1.0], 1.0),
        ([14.0, 1.0], 1.0),
        ([10.0, 1.0], 2.0),
        ([4.0, 1.0], 2.0),
        ([1.0, 1.0], 1.0),
        ([1.0, 1.0], 0.0),
        ([1.0, 1.0], 0.0),
        ([1.0, 1.0], 0.0),
    ]
    frames = {
        f"frame-{index}": {
            "frame_token": f"frame-{index}",
            "scene_token": scene_token,
            "timestamp": float(index),
            "instances": [f"instance-{index}"],
            "prev": "",
            "next": "",
        }
        for index in range(len(coords_and_speeds))
    }
    instances = {
        f"instance-{index}": {
            "instance_token": f"instance-{index}",
            "agent_token": agent_token,
            "frame_token": f"frame-{index}",
            "coords": coords,
            "heading": 0.0,
            "speed": speed,
            "acceleration": [0.0, 0.0],
            "mode": "",
            "prev": "",
            "next": "",
        }
        for index, (coords, speed) in enumerate(coords_and_speeds)
    }
    payloads = {
        "scene": {"scene_token": scene_token, "filename": prefix},
        "agents": {
            agent_token: {
                "agent_token": agent_token,
                "scene_token": scene_token,
                "type": "Car",
                "size": [4.5, 2.0],
            }
        },
        "frames": frames,
        "instances": instances,
        "obstacles": {},
    }
    for suffix, payload in payloads.items():
        (tmp_path / f"{prefix}_{suffix}.json").write_text(json.dumps(payload))

    stall = Stall("X-R1-C01", "X", 1, 1, 0.0, 2.0, 0.0, 2.0)
    inventory = inventory_scene(tmp_path, prefix, (stall,))

    assert inventory.scene_id == prefix
    assert inventory.scene_token == scene_token
    assert inventory.fps == pytest.approx(1.0)
    assert inventory.agent_count == 1
    assert inventory.vehicle_agent_count == 1
    assert len(inventory.events) == 1
    assert inventory.events[0].agent_token == agent_token

    output_dir = tmp_path / "results"
    write_inventory_outputs([inventory], output_dir)
    event_rows = [json.loads(line) for line in (output_dir / "candidate_events.jsonl").read_text().splitlines()]
    scene_rows = json.loads((output_dir / "scene_inventory.json").read_text())
    assert event_rows[0]["agent_token"] == agent_token
    assert scene_rows == [
        {
            "scene_id": prefix,
            "scene_token": scene_token,
            "fps": pytest.approx(1.0),
            "agent_count": 1,
            "vehicle_agent_count": 1,
            "instance_count": len(instances),
            "obstacle_count": 0,
            "event_count": 1,
        }
    ]
    assert "mean_duration" not in scene_rows[0]

    rerun = run_inventory(tmp_path, tmp_path / "run-results")
    assert len(rerun) == 1
    assert rerun[0].scene_id == prefix
    assert (tmp_path / "run-results" / "candidate_events.jsonl").exists()


def test_obstacle_stall_coverage_counts_centroids_inside_generated_stalls():
    stall = Stall("X-R1-C01", "X", 1, 1, 0.0, 2.0, 0.0, 2.0)
    obstacles = {
        "inside": {"coords": [1.0, 1.0]},
        "outside": {"coords": [10.0, 10.0]},
    }

    covered, total, fraction = obstacle_stall_coverage(obstacles, (stall,))

    assert covered == 1
    assert total == 2
    assert fraction == pytest.approx(0.5)


def test_fragmented_static_runs_do_not_duplicate_one_unparking_event():
    stall = Stall("X-R1-C01", "X", 1, 1, 0.0, 2.0, 0.0, 2.0)
    rows = [
        {"instance_token": "i0", "coords": [1.0, 1.0], "heading": 0.0, "speed": 0.0, "timestamp": 0.0},
        {"instance_token": "i1", "coords": [1.0, 1.0], "heading": 0.0, "speed": 0.0, "timestamp": 1.0},
        {"instance_token": "i2", "coords": [1.0, 1.0], "heading": 0.0, "speed": 0.06, "timestamp": 2.0},
        {"instance_token": "i3", "coords": [1.0, 1.0], "heading": 0.0, "speed": 0.0, "timestamp": 3.0},
        {"instance_token": "i4", "coords": [1.0, 1.0], "heading": 0.0, "speed": 0.0, "timestamp": 4.0},
        {"instance_token": "i5", "coords": [10.0, 1.0], "heading": 0.0, "speed": 1.0, "timestamp": 5.0},
    ]
    agent = {"agent_token": "agent", "type": "Car", "size": [4.5, 2.0]}

    events = detect_trajectory_events("DJI_0099", "scene", agent, rows, (stall,), fps=1.0)
    unparking = [event for event in events if event.event_type == "unparking"]

    assert len(unparking) == 1
    assert len({event.event_id for event in events}) == len(events)


def test_static_run_does_not_claim_departure_after_a_later_parked_run():
    stall = Stall("X-R1-C01", "X", 1, 1, 0.0, 2.0, 0.0, 2.0)
    speeds = [0.0, 0.0, 0.06, 0.06, 0.0, 0.0, 1.0]
    rows = [
        {
            "instance_token": f"i{index}",
            "coords": [10.0, 1.0] if index == 6 else [1.0, 1.0],
            "heading": 0.0,
            "speed": speed,
            "timestamp": float(index),
        }
        for index, speed in enumerate(speeds)
    ]
    agent = {"agent_token": "agent", "type": "Car", "size": [4.5, 2.0]}

    events = detect_trajectory_events("DJI_0099", "scene", agent, rows, (stall,), fps=1.0)
    unparking = [event for event in events if event.event_type == "unparking"]

    assert len(unparking) == 1
    assert unparking[0].start_index == 6
