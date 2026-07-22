"""Contract and regression tests for the Project Parkway simulator."""

import csv
import json
import math
from pathlib import Path
import random

import pytest

import park_sim


def test_ackermann_straight_line_and_turning_radius():
    state = park_sim.VehicleState(x=0.0, y=0.0, theta=0.0, v=2.0)
    straight = park_sim.ackermann_step(state, park_sim.Control(a=0.0, delta=0.0), dt=0.5)
    assert straight.x == pytest.approx(1.0)
    assert straight.y == pytest.approx(0.0)
    assert straight.theta == pytest.approx(0.0)

    delta = math.radians(30.0)
    turning = park_sim.ackermann_step(state, park_sim.Control(a=0.0, delta=delta), dt=0.1)
    measured_radius = state.v / ((turning.theta - state.theta) / 0.1)
    assert measured_radius == pytest.approx(park_sim.WHEELBASE_M / math.tan(delta))


def test_ackermann_rejects_invalid_timestep():
    with pytest.raises(ValueError, match="dt"):
        park_sim.ackermann_step(
            park_sim.VehicleState(0.0, 0.0, 0.0, 0.0),
            park_sim.Control(0.0, 0.0),
            dt=0.0,
        )


def test_line_of_sight_raycasting_detects_suv_shadow():
    eye = (0.0, 0.0)
    target = (8.0, 0.0)
    blocking_suv = park_sim.OBB(cx=4.0, cy=0.0, width=2.0, length=5.2, theta=0.0)
    clear_suv = park_sim.OBB(cx=4.0, cy=4.0, width=2.0, length=5.2, theta=0.0)
    assert park_sim.line_of_sight_blocked(eye, target, [blocking_suv])
    assert not park_sim.line_of_sight_blocked(eye, target, [clear_suv])


def test_driver_eye_is_1_2m_forward_of_rear_axle():
    state = park_sim.VehicleState(2.0, 3.0, math.pi / 2.0, 0.0)
    assert park_sim.driver_eye_position(state) == pytest.approx((2.0, 4.2))


def test_sat_collision_handles_overlap_separation_and_rotation():
    base = park_sim.OBB(0.0, 0.0, width=2.0, length=4.5, theta=0.0)
    overlap = park_sim.OBB(1.0, 0.0, width=2.0, length=4.5, theta=math.pi / 4.0)
    separated = park_sim.OBB(10.0, 0.0, width=2.0, length=4.5, theta=0.0)
    touching = park_sim.OBB(4.5, 0.0, width=2.0, length=4.5, theta=0.0)
    assert park_sim.obb_collision(base, overlap)
    assert not park_sim.obb_collision(base, separated)
    assert park_sim.obb_collision(base, touching)
    assert not park_sim.broad_phase_overlap(base, separated)


def test_latency_models_are_seeded_and_gear_delay_is_constant():
    first = park_sim.sample_reaction_time(random.Random(17))
    second = park_sim.sample_reaction_time(random.Random(17))
    assert first == second
    assert first > 0.0
    assert park_sim.GEAR_SHIFT_DELAY_S == 1.0


@pytest.mark.parametrize("strategy", ["forward", "reverse"])
def test_empty_lot_state_machines_complete_without_conflict(strategy):
    trace = park_sim.simulate_maneuver(
        strategy=strategy,
        rng=random.Random(4),
        stall_width=2.7,
        aisle_width=6.4,
        suv_present=False,
        pedestrian_present=False,
    )
    assert trace.phases[0] == "approach"
    assert trace.phases[-1] == "complete"
    assert trace.total_time_s > 0.0
    assert not trace.collision
    assert not trace.critical_conflict
    assert trace.min_pedestrian_distance_m is None


def test_forced_suv_obstruction_activates_reverse_exit_creep():
    forward = park_sim.simulate_maneuver(
        strategy="forward",
        rng=random.Random(9),
        stall_width=2.6,
        aisle_width=5.8,
        suv_present=True,
        pedestrian_present=True,
    )
    reverse = park_sim.simulate_maneuver(
        strategy="reverse",
        rng=random.Random(9),
        stall_width=2.6,
        aisle_width=5.8,
        suv_present=True,
        pedestrian_present=True,
    )
    assert forward.line_of_sight_blocked
    assert forward.creep_activated
    assert forward.max_blind_exit_speed_mps <= park_sim.CREEP_SPEED_MPS
    assert max(abs(state.v) for state in forward.trajectory if state.v < 0.0) <= park_sim.CREEP_SPEED_MPS
    assert 0.0 in {state.v for state in forward.trajectory}
    assert not reverse.creep_activated
    assert forward.gear_shifts == 1
    assert reverse.gear_shifts == 2


def _conflict_rate(rows):
    return sum(row["critical_conflict"] for row in rows) / len(rows)


def test_monte_carlo_seed_reproducibility():
    config = park_sim.SimulationConfig(seed=81, runs=120, ped_density=0.18, suv_prob=0.4)
    assert park_sim.run_monte_carlo(config) == park_sim.run_monte_carlo(config)


def test_monte_carlo_safety_events_are_derived_from_trace_distance_and_speed():
    rows = park_sim.run_monte_carlo(
        park_sim.SimulationConfig(seed=12, runs=500, ped_density=0.30, suv_prob=0.8)
    )
    pedestrian_rows = [row for row in rows if row["min_pedestrian_distance_m"] is not None]
    assert pedestrian_rows
    for row in pedestrian_rows:
        distance = row["min_pedestrian_distance_m"]
        expected_braking = row["max_blind_exit_speed_mps"] ** 2 / (2.0 * max(distance, 0.05))
        assert row["required_braking_mps2"] == pytest.approx(expected_braking, abs=2e-3)
        assert row["proximity_warning"] is (distance < park_sim.PROXIMITY_THRESHOLD_M)
        assert row["critical_conflict"] is (expected_braking > park_sim.CONFLICT_BRAKING_MPS2)
        assert row["collision"] is (distance < park_sim.COLLISION_THRESHOLD_M)


def test_conflict_probability_increases_with_pedestrian_density():
    low = park_sim.run_monte_carlo(
        park_sim.SimulationConfig(seed=22, runs=3000, ped_density=0.05, suv_prob=0.4)
    )
    high = park_sim.run_monte_carlo(
        park_sim.SimulationConfig(seed=22, runs=3000, ped_density=0.30, suv_prob=0.4)
    )
    assert _conflict_rate(high) > _conflict_rate(low)


def test_monte_carlo_conflict_rate_converges():
    short = park_sim.run_monte_carlo(
        park_sim.SimulationConfig(seed=31, runs=2000, ped_density=0.18, suv_prob=0.4)
    )
    long = park_sim.run_monte_carlo(
        park_sim.SimulationConfig(seed=31, runs=8000, ped_density=0.18, suv_prob=0.4)
    )
    assert abs(_conflict_rate(short) - _conflict_rate(long)) < 0.03


def test_artifact_schema_and_canonical_path_handoff(tmp_path):
    csv_path = tmp_path / "simulation_results.csv"
    json_path = tmp_path / "canonical_paths.json"
    rows = park_sim.run_monte_carlo(park_sim.SimulationConfig(seed=5, runs=20))
    park_sim.export_results_csv(rows, csv_path)
    paths = park_sim.export_canonical_paths(json_path)

    with csv_path.open(newline="", encoding="utf-8") as handle:
        parsed = list(csv.DictReader(handle))
    assert len(parsed) == 20
    assert set(park_sim.RESULT_FIELDS) == set(parsed[0])

    loaded = json.loads(json_path.read_text(encoding="utf-8"))
    assert loaded["schema_version"] == "1.0"
    assert loaded["units"] == {"position": "m", "heading": "rad", "velocity": "m/s"}
    assert len(paths) == len(loaded["paths"]) == 10
    assert {path["strategy"] for path in loaded["paths"]} == {"forward", "reverse"}
    assert all(len(path["points"]) >= 50 for path in loaded["paths"])
    assert set(loaded["paths"][0]["points"][0]) == {"x", "y", "theta", "v"}


def test_cli_exposes_required_flags():
    help_text = park_sim.build_parser().format_help()
    for flag in (
        "--seed",
        "--runs",
        "--stall-width",
        "--aisle-width",
        "--ped-density",
        "--suv-prob",
        "--export-csv",
        "--export-json",
    ):
        assert flag in help_text


def test_delivered_artifacts_have_required_cardinality():
    root = Path(__file__).resolve().parent
    with (root / "simulation_results.csv").open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    paths = json.loads((root / "canonical_paths.json").read_text(encoding="utf-8"))
    assert len(rows) == 10_000
    assert len(paths["paths"]) == 10
