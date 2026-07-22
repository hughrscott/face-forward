#!/usr/bin/env python3
"""Project Parkway 2D parking kinematics and Monte Carlo simulator.

The public data and function contract is documented in PARK_SIM_CONTRACT.md.
The module intentionally depends only on the Python standard library.
"""

from __future__ import annotations

from dataclasses import dataclass
import argparse
import csv
import json
import math
from pathlib import Path
import random

WHEELBASE_M = 2.8
DRIVER_EYE_FORWARD_M = 1.2
SUV_WIDTH_M = 2.0
SUV_LENGTH_M = 5.2
SUV_HEIGHT_M = 1.9
GEAR_SHIFT_DELAY_S = 1.0
REACTION_MEAN_S = 0.75
REACTION_STD_S = 0.15
SCAN_SWEEP_S = 0.5
CREEP_SPEED_MPS = 0.5
PROXIMITY_THRESHOLD_M = 1.5
COLLISION_THRESHOLD_M = 0.25
CONFLICT_BRAKING_MPS2 = 3.0


@dataclass(frozen=True)
class OBB:
    """Oriented rectangle; length follows heading and width is lateral."""

    cx: float
    cy: float
    width: float
    length: float
    theta: float

    def corners(self) -> tuple[tuple[float, float], ...]:
        forward = (math.cos(self.theta), math.sin(self.theta))
        lateral = (-math.sin(self.theta), math.cos(self.theta))
        half_l, half_w = self.length / 2.0, self.width / 2.0
        return tuple(
            (
                self.cx + sx * half_l * forward[0] + sy * half_w * lateral[0],
                self.cy + sx * half_l * forward[1] + sy * half_w * lateral[1],
            )
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        )


@dataclass(frozen=True)
class VehicleState:
    """Rear-axle-centred kinematic state in SI units."""

    x: float
    y: float
    theta: float
    v: float


@dataclass(frozen=True)
class Control:
    """Longitudinal acceleration and front-wheel steering angle."""

    a: float
    delta: float


def ackermann_step(
    state: VehicleState,
    control: Control,
    dt: float,
    wheelbase: float = WHEELBASE_M,
) -> VehicleState:
    """Advance the kinematic bicycle model by one explicit Euler step."""
    if dt <= 0.0:
        raise ValueError("dt must be positive")
    if wheelbase <= 0.0:
        raise ValueError("wheelbase must be positive")
    return VehicleState(
        x=state.x + state.v * math.cos(state.theta) * dt,
        y=state.y + state.v * math.sin(state.theta) * dt,
        theta=state.theta + (state.v / wheelbase) * math.tan(control.delta) * dt,
        v=state.v + control.a * dt,
    )


def driver_eye_position(state: VehicleState) -> tuple[float, float]:
    return (
        state.x + DRIVER_EYE_FORWARD_M * math.cos(state.theta),
        state.y + DRIVER_EYE_FORWARD_M * math.sin(state.theta),
    )


def _axes(box: OBB) -> tuple[tuple[float, float], tuple[float, float]]:
    return (
        (math.cos(box.theta), math.sin(box.theta)),
        (-math.sin(box.theta), math.cos(box.theta)),
    )


def broad_phase_overlap(a: OBB, b: OBB) -> bool:
    """Conservative bounding-circle collision test."""
    ra = math.hypot(a.length, a.width) / 2.0
    rb = math.hypot(b.length, b.width) / 2.0
    return math.hypot(a.cx - b.cx, a.cy - b.cy) <= ra + rb


def obb_collision(a: OBB, b: OBB) -> bool:
    """Narrow-phase separating-axis test, including edge contact."""
    if not broad_phase_overlap(a, b):
        return False
    ca, cb = a.corners(), b.corners()
    for axis in _axes(a) + _axes(b):
        pa = [x * axis[0] + y * axis[1] for x, y in ca]
        pb = [x * axis[0] + y * axis[1] for x, y in cb]
        if max(pa) < min(pb) or max(pb) < min(pa):
            return False
    return True


def _cross(a: tuple[float, float], b: tuple[float, float], c: tuple[float, float]) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def _segment_intersects(
    a: tuple[float, float],
    b: tuple[float, float],
    c: tuple[float, float],
    d: tuple[float, float],
) -> bool:
    eps = 1e-12
    c1, c2, c3, c4 = _cross(a, b, c), _cross(a, b, d), _cross(c, d, a), _cross(c, d, b)
    if ((c1 > eps and c2 < -eps) or (c1 < -eps and c2 > eps)) and (
        (c3 > eps and c4 < -eps) or (c3 < -eps and c4 > eps)
    ):
        return True
    for value, p, q, r in ((c1, a, b, c), (c2, a, b, d), (c3, c, d, a), (c4, c, d, b)):
        if abs(value) <= eps and min(p[0], q[0]) - eps <= r[0] <= max(p[0], q[0]) + eps and min(p[1], q[1]) - eps <= r[1] <= max(p[1], q[1]) + eps:
            return True
    return False


def line_of_sight_blocked(
    eye: tuple[float, float],
    target: tuple[float, float],
    obstacles: list[OBB] | tuple[OBB, ...],
) -> bool:
    """Return whether the finite eye-to-target ray crosses an obstacle."""
    for obstacle in obstacles:
        corners = obstacle.corners()
        if any(
            _segment_intersects(eye, target, corners[i], corners[(i + 1) % 4])
            for i in range(4)
        ):
            return True
    return False


@dataclass(frozen=True)
class ManeuverTrace:
    strategy: str
    phases: tuple[str, ...]
    total_time_s: float
    reaction_time_s: float
    gear_shifts: int
    line_of_sight_blocked: bool
    creep_activated: bool
    max_blind_exit_speed_mps: float
    proximity_warning: bool
    critical_conflict: bool
    collision: bool
    min_pedestrian_distance_m: float | None
    trajectory: tuple[VehicleState, ...]


def sample_reaction_time(rng: random.Random) -> float:
    """Draw a positive reaction latency from the specified Gaussian."""
    return max(0.1, rng.gauss(REACTION_MEAN_S, REACTION_STD_S))


def _kinematic_trajectory(strategy: str, aisle_width: float, points: int = 31) -> tuple[VehicleState, ...]:
    """Integrate a quarter-turn Ackermann path and its parking-space leg."""
    radius = aisle_width / 2.0
    arc_steps = max(40, points)
    speed = 1.0
    state = VehicleState(-radius, 0.0, 0.0, speed)
    entry: list[VehicleState] = [state]
    arc_dt = (math.pi * radius / 2.0) / (arc_steps * speed)
    steering = math.atan(WHEELBASE_M / radius)
    for _ in range(arc_steps):
        state = ackermann_step(state, Control(0.0, steering), arc_dt)
        entry.append(state)
    remaining = max(0.0, 4.4 - state.y)
    straight_steps = max(10, math.ceil(remaining / 0.1))
    if remaining:
        straight_dt = remaining / (straight_steps * speed)
        for _ in range(straight_steps):
            state = ackermann_step(state, Control(0.0, 0.0), straight_dt)
            entry.append(state)

    if strategy == "reverse":
        entry = [
            VehicleState(s.x, s.y, (s.theta + math.pi) % (2 * math.pi), -speed)
            for s in entry
        ]
    parked = VehicleState(entry[-1].x, entry[-1].y, entry[-1].theta, 0.0)
    exit_states = [VehicleState(s.x, s.y, s.theta, -s.v) for s in reversed(entry)]
    if strategy == "reverse":
        initial_shift = VehicleState(entry[0].x, entry[0].y, entry[0].theta, 0.0)
        return tuple([initial_shift] + entry + [parked] + exit_states[1:])
    return tuple(entry + [parked] + exit_states[1:])


def simulate_maneuver(
    strategy: str,
    rng: random.Random,
    stall_width: float,
    aisle_width: float,
    suv_present: bool,
    pedestrian_present: bool,
) -> ManeuverTrace:
    """Execute one full park-and-exit state machine.

    Forward parking reverses from the stall and therefore applies LOS scanning and
    creep under SUV occlusion. Reverse parking pays an extra initial gear change,
    then exits nose-first with an unobstructed driver view.
    """
    if strategy not in {"forward", "reverse"}:
        raise ValueError("strategy must be 'forward' or 'reverse'")
    if stall_width <= 0.0 or aisle_width <= 0.0:
        raise ValueError("stall_width and aisle_width must be positive")

    if strategy == "forward":
        phases = ("approach", "enter_forward", "parked", "shift_to_reverse", "scan", "exit_reverse", "complete")
        gear_shifts = 1
    else:
        phases = ("approach", "shift_to_reverse", "enter_reverse", "parked", "shift_to_drive", "exit_forward", "complete")
        gear_shifts = 2

    eye = (0.0, DRIVER_EYE_FORWARD_M)
    sight_target = (aisle_width / 2.0, -0.5)
    adjacent_suv = OBB(stall_width, 1.6, SUV_WIDTH_M, SUV_LENGTH_M, math.pi / 2.0)
    blocked = strategy == "forward" and suv_present and line_of_sight_blocked(eye, sight_target, [adjacent_suv])
    creep = blocked
    exit_speed = CREEP_SPEED_MPS if creep else 1.2
    reaction_time = sample_reaction_time(rng)

    min_distance: float | None = None
    proximity = critical = collision = False
    if pedestrian_present:
        visibility_penalty = 0.9 if blocked else 0.0
        min_distance = max(0.0, rng.gauss(2.1 - visibility_penalty, 0.65))
        proximity = min_distance < PROXIMITY_THRESHOLD_M
        stopping_distance = max(min_distance, 0.05)
        required_deceleration = exit_speed**2 / (2.0 * stopping_distance)
        critical = required_deceleration > CONFLICT_BRAKING_MPS2
        collision = min_distance < COLLISION_THRESHOLD_M

    path = _kinematic_trajectory(strategy, aisle_width)
    if creep:
        path = tuple(
            VehicleState(s.x, s.y, s.theta, -CREEP_SPEED_MPS)
            if s.v < -CREEP_SPEED_MPS
            else s
            for s in path
        )
    path_length = sum(math.hypot(b.x - a.x, b.y - a.y) for a, b in zip(path, path[1:]))
    mean_speed = 0.8 if strategy == "reverse" else 0.95
    travel_time = path_length / mean_speed
    scan_time = SCAN_SWEEP_S if strategy == "forward" else 0.0
    if blocked:
        travel_time += 2.0
    total_time = travel_time + gear_shifts * GEAR_SHIFT_DELAY_S + reaction_time + scan_time

    return ManeuverTrace(
        strategy=strategy,
        phases=phases,
        total_time_s=total_time,
        reaction_time_s=reaction_time,
        gear_shifts=gear_shifts,
        line_of_sight_blocked=blocked,
        creep_activated=creep,
        max_blind_exit_speed_mps=exit_speed,
        proximity_warning=proximity,
        critical_conflict=critical,
        collision=collision,
        min_pedestrian_distance_m=min_distance,
        trajectory=path,
    )


@dataclass(frozen=True)
class SimulationConfig:
    seed: int = 42
    runs: int = 10_000
    stall_width: float = 2.7
    aisle_width: float | None = None
    ped_density: float | None = None
    suv_prob: float | None = None

    def __post_init__(self) -> None:
        if self.runs <= 0:
            raise ValueError("runs must be positive")
        if self.stall_width <= 0.0:
            raise ValueError("stall_width must be positive")
        if self.aisle_width is not None and not 5.5 <= self.aisle_width <= 7.2:
            raise ValueError("aisle_width must be in [5.5, 7.2]")
        if self.ped_density is not None and not 0.0 <= self.ped_density <= 0.3:
            raise ValueError("ped_density must be in [0.0, 0.3]")
        if self.suv_prob is not None and not 0.0 <= self.suv_prob <= 0.8:
            raise ValueError("suv_prob must be in [0.0, 0.8]")


RESULT_FIELDS = (
    "run_id",
    "strategy",
    "seed",
    "stall_width_m",
    "aisle_width_m",
    "ped_density_per_m",
    "suv_probability",
    "suv_present",
    "pedestrian_count",
    "pedestrian_speed_mps",
    "total_cycle_time_s",
    "reaction_time_s",
    "gear_shifts",
    "los_blocked",
    "creep_activated",
    "max_blind_exit_speed_mps",
    "min_pedestrian_distance_m",
    "proximity_warning",
    "required_braking_mps2",
    "critical_conflict",
    "collision",
)


def _sample_poisson(mean: float, rng: random.Random) -> int:
    if mean <= 0.0:
        return 0
    threshold = math.exp(-mean)
    product = 1.0
    count = 0
    while product > threshold:
        count += 1
        product *= rng.random()
    return count - 1


def run_monte_carlo(config: SimulationConfig) -> list[dict[str, object]]:
    """Run seeded trials; unset environmental parameters are sampled per trial."""
    master = random.Random(config.seed)
    rows: list[dict[str, object]] = []
    for run_id in range(config.runs):
        strategy = "forward" if run_id % 2 == 0 else "reverse"
        aisle_width = config.aisle_width if config.aisle_width is not None else master.uniform(5.5, 7.2)
        ped_density = config.ped_density if config.ped_density is not None else master.uniform(0.05, 0.3)
        suv_probability = config.suv_prob if config.suv_prob is not None else master.uniform(0.0, 0.8)
        suv_present = master.random() < suv_probability
        pedestrian_count = _sample_poisson(ped_density * aisle_width, master)
        pedestrian_speed = max(0.2, master.gauss(1.4, 0.2)) if pedestrian_count else None
        trace = simulate_maneuver(
            strategy,
            master,
            config.stall_width,
            aisle_width,
            suv_present,
            pedestrian_count > 0,
        )

        required_braking = (
            0.0
            if trace.min_pedestrian_distance_m is None
            else trace.max_blind_exit_speed_mps**2
            / (2.0 * max(trace.min_pedestrian_distance_m, 0.05))
        )

        rows.append(
            {
                "run_id": run_id,
                "strategy": strategy,
                "seed": config.seed,
                "stall_width_m": round(config.stall_width, 4),
                "aisle_width_m": round(aisle_width, 4),
                "ped_density_per_m": round(ped_density, 5),
                "suv_probability": round(suv_probability, 5),
                "suv_present": suv_present,
                "pedestrian_count": pedestrian_count,
                "pedestrian_speed_mps": None if pedestrian_speed is None else round(pedestrian_speed, 4),
                "total_cycle_time_s": round(trace.total_time_s, 4),
                "reaction_time_s": round(trace.reaction_time_s, 4),
                "gear_shifts": trace.gear_shifts,
                "los_blocked": trace.line_of_sight_blocked,
                "creep_activated": trace.creep_activated,
                "max_blind_exit_speed_mps": round(trace.max_blind_exit_speed_mps, 4),
                "min_pedestrian_distance_m": None if trace.min_pedestrian_distance_m is None else round(trace.min_pedestrian_distance_m, 4),
                "proximity_warning": trace.proximity_warning,
                "required_braking_mps2": round(required_braking, 4),
                "critical_conflict": trace.critical_conflict,
                "collision": trace.collision,
            }
        )
    return rows


def export_results_csv(rows: list[dict[str, object]], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_FIELDS, extrasaction="raise")
        writer.writeheader()
        writer.writerows(rows)


def canonical_paths() -> list[dict[str, object]]:
    paths: list[dict[str, object]] = []
    for aisle_width in (5.5, 5.9, 6.35, 6.8, 7.2):
        for strategy in ("forward", "reverse"):
            states = _kinematic_trajectory(strategy, aisle_width)
            paths.append(
                {
                    "id": f"{strategy}-aisle-{aisle_width:.2f}",
                    "strategy": strategy,
                    "parameters": {"aisle_width_m": aisle_width, "stall_width_m": 2.7},
                    "points": [
                        {"x": round(s.x, 5), "y": round(s.y, 5), "theta": round(s.theta, 6), "v": s.v}
                        for s in states
                    ],
                }
            )
    return paths


def export_canonical_paths(path: str | Path) -> list[dict[str, object]]:
    paths = canonical_paths()
    payload = {
        "schema_version": "1.0",
        "coordinate_frame": "rear_axle_centroid",
        "units": {"position": "m", "heading": "rad", "velocity": "m/s"},
        "paths": paths,
    }
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Project Parkway parking Monte Carlo simulator")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--runs", type=int, default=10_000)
    parser.add_argument("--stall-width", type=float, default=2.7)
    parser.add_argument("--aisle-width", type=float, default=None, help="fixed width; default samples 5.5-7.2 m")
    parser.add_argument("--ped-density", type=float, default=None, help="fixed density; default samples 0.05-0.30 peds/m")
    parser.add_argument("--suv-prob", type=float, default=None, help="fixed probability; default samples 0.0-0.8")
    parser.add_argument("--export-csv", type=Path, default=None)
    parser.add_argument("--export-json", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = SimulationConfig(
        seed=args.seed,
        runs=args.runs,
        stall_width=args.stall_width,
        aisle_width=args.aisle_width,
        ped_density=args.ped_density,
        suv_prob=args.suv_prob,
    )
    rows = run_monte_carlo(config)
    if args.export_csv:
        export_results_csv(rows, args.export_csv)
    if args.export_json:
        export_canonical_paths(args.export_json)
    conflicts = sum(bool(row["critical_conflict"]) for row in rows)
    collisions = sum(bool(row["collision"]) for row in rows)
    print(json.dumps({"runs": len(rows), "critical_conflicts": conflicts, "collisions": collisions}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
