import csv
import math
from pathlib import Path

import pytest

from empirical_pipeline import (
    MANEUVER_COLUMNS,
    KinematicPoint,
    ManeuverAccumulator,
    ManeuverRecord,
    classify_strategy,
    analyze_observation_frames,
    compute_validation_metrics,
    export_maneuvers_csv,
    generate_comparison_figure,
    pair_ttc,
    point_in_polygon,
    project_point,
    rtsp_candidates,
    segment_intersects_bbox,
)


def test_classify_strategy_uses_parked_nose_direction():
    assert classify_strategy((0.0, 1.0), (0.0, 1.0)) == "forward"
    assert classify_strategy((0.0, -1.0), (0.0, 1.0)) == "reverse"
    with pytest.raises(ValueError, match="non-zero"):
        classify_strategy((0.0, 0.0), (0.0, 1.0))


def test_pair_ttc_reports_only_collision_course():
    vehicle = KinematicPoint(position=(0.0, 0.0), velocity=(1.0, 0.0))
    crossing_pedestrian = KinematicPoint(position=(2.0, -2.0), velocity=(0.0, 1.0))
    separating_pedestrian = KinematicPoint(position=(-2.0, 2.0), velocity=(0.0, 1.0))

    assert pair_ttc(vehicle, crossing_pedestrian, collision_radius_m=0.75) == pytest.approx(2.0)
    assert pair_ttc(vehicle, separating_pedestrian, collision_radius_m=0.75) is None


def test_calibrated_geometry_primitives():
    homography = ((0.1, 0.0, 0.0), (0.0, 0.1, 0.0), (0.0, 0.0, 1.0))
    assert project_point((20.0, 30.0), homography) == pytest.approx((2.0, 3.0))
    square = ((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0))
    assert point_in_polygon((2.0, 2.0), square) is True
    assert point_in_polygon((5.0, 2.0), square) is False
    assert segment_intersects_bbox((-1.0, 2.0), (5.0, 2.0), (1.0, 1.0, 3.0, 3.0)) is True
    assert segment_intersects_bbox((-1.0, 5.0), (5.0, 5.0), (1.0, 1.0, 3.0, 3.0)) is False


def test_accumulator_emits_one_complete_maneuver():
    accumulator = ManeuverAccumulator(
        track_id=7,
        timestamp_origin="2026-07-20T12:00:00Z",
        stall_inward_vector=(0.0, 1.0),
    )

    observations = [
        # time, speed, stall, aisle, heading, LOS, adjacent SUV, pedestrian TTCs
        (0.0, 1.0, False, True, (0.0, 1.0), False, False, {}),
        (5.0, 0.5, True, False, (0.0, 1.0), False, True, {}),
        (6.0, 0.1, True, False, (0.0, 1.0), False, True, {}),
        (20.0, 0.7, True, False, (0.0, -1.0), True, True, {3: 1.2}),
        (24.0, 1.0, False, True, (0.0, -1.0), True, True, {3: 0.9, 4: None}),
    ]

    record = None
    for values in observations:
        record = accumulator.observe(*values) or record

    assert record is not None
    assert record.strategy == "forward"
    assert record.entry_duration_s == pytest.approx(6.0)
    assert record.exit_duration_s == pytest.approx(4.0)
    assert record.total_cycle_s == pytest.approx(10.0)
    assert record.los_blocked is True
    assert record.suv_adjacent is True
    assert record.ped_count == 2
    assert record.near_miss_ttc_s == pytest.approx(0.9)
    assert record.conflict_flag is True


def test_observation_frames_are_converted_to_records():
    frames = [
        {
            "timestamp_s": time,
            "timestamp": "2026-07-20T12:00:00Z",
            "vehicles": [
                {
                    "track_id": 7,
                    "speed_mps": speed,
                    "in_stall": in_stall,
                    "in_aisle": in_aisle,
                    "heading": heading,
                    "los_blocked": blocked,
                    "suv_adjacent": adjacent,
                    "pedestrian_ttcs": ttcs,
                }
            ],
        }
        for time, speed, in_stall, in_aisle, heading, blocked, adjacent, ttcs in [
            (0.0, 1.0, False, True, [0.0, 1.0], False, False, {}),
            (5.0, 0.5, True, False, [0.0, 1.0], False, True, {}),
            (6.0, 0.1, True, False, [0.0, 1.0], False, True, {}),
            (20.0, 0.7, True, False, [0.0, -1.0], True, True, {"3": 1.2}),
            (24.0, 1.0, False, True, [0.0, -1.0], True, True, {"3": 0.9}),
        ]
    ]

    records = analyze_observation_frames(frames, stall_inward_vector=(0.0, 1.0))

    assert len(records) == 1
    assert records[0].strategy == "forward"
    assert records[0].conflict_flag is True


def test_csv_contract_has_exact_order_and_boolean_format(tmp_path: Path):
    output = tmp_path / "maneuvers.csv"
    record = ManeuverRecord(
        timestamp="2026-07-20T12:00:00Z",
        strategy="reverse",
        entry_duration_s=8.0,
        exit_duration_s=3.0,
        total_cycle_s=11.0,
        los_blocked=False,
        suv_adjacent=True,
        ped_count=0,
        near_miss_ttc_s=None,
        conflict_flag=False,
    )

    export_maneuvers_csv([record], output)
    with output.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))

    assert rows[0] == list(MANEUVER_COLUMNS)
    assert rows[1][1] == "reverse"
    assert rows[1][5:7] == ["False", "True"]
    assert rows[1][8] == ""


def test_comparison_figure_is_generated_from_real_csv_inputs(tmp_path: Path):
    empirical = tmp_path / "empirical.csv"
    simulation = tmp_path / "simulation.csv"
    output = tmp_path / "comparison.pdf"
    empirical.write_text(
        "strategy,entry_duration_s,conflict_flag\n"
        "forward,8.0,True\nforward,9.0,False\n"
        "reverse,10.0,False\nreverse,11.0,False\n",
        encoding="utf-8",
    )
    simulation.write_text(
        "strategy,entry_duration_s,critical_conflict\n"
        "forward,7.5,True\nforward,8.5,False\n"
        "reverse,9.5,False\nreverse,10.5,False\n",
        encoding="utf-8",
    )

    summary = generate_comparison_figure(empirical, simulation, output)

    assert output.exists()
    assert output.stat().st_size > 1_000
    assert summary["empirical_rows"] == 4
    assert summary["simulation_rows"] == 4


def test_validation_metrics_match_hand_labels_by_id():
    truth = [
        {"maneuver_id": "a", "strategy": "forward", "los_blocked": True},
        {"maneuver_id": "b", "strategy": "reverse", "los_blocked": False},
        {"maneuver_id": "c", "strategy": "forward", "los_blocked": True},
    ]
    predicted = [
        {"maneuver_id": "a", "strategy": "forward", "los_blocked": True},
        {"maneuver_id": "b", "strategy": "forward", "los_blocked": False},
        {"maneuver_id": "c", "strategy": "forward", "los_blocked": False},
    ]

    metrics = compute_validation_metrics(truth, predicted)

    assert metrics == {
        "matched": 3,
        "strategy_accuracy": pytest.approx(2 / 3),
        "los_blocked_accuracy": pytest.approx(2 / 3),
        "strategy_gate_passed": False,
        "los_blocked_gate_passed": False,
    }


def test_rtsp_candidates_are_vendor_specific_and_do_not_embed_credentials():
    hikvision = rtsp_candidates("hikvision", "100.64.0.10", channel=2)
    dahua = rtsp_candidates("dahua", "100.64.0.11", channel=2)

    assert hikvision == ["rtsp://100.64.0.10:554/Streaming/Channels/201"]
    assert dahua == [
        "rtsp://100.64.0.11:554/cam/realmonitor?channel=2&subtype=0"
    ]
    assert "@" not in hikvision[0]
    with pytest.raises(ValueError, match="supported"):
        rtsp_candidates("unknown", "100.64.0.12")
