# Project Parkway Simulator Contract

Version: 1.1
Producer: `park_sim.py`
Consumers: frontend trajectory visualizer and analysis pipeline

## Coordinate and unit conventions

- World frame: right-handed 2D Cartesian plane.
- Vehicle position `(x, y)`: centroid of the rear axle, in meters.
- Heading `theta`: radians, counter-clockwise from the positive x-axis.
- Velocity `v`: meters/second along the heading; negative means reverse.
- Acceleration `a`: meters/second squared.
- Front-wheel steering `delta`: radians, positive for a left turn.
- Wheelbase: 2.8 m.

## Python API

### Data model

- `VehicleState(x, y, theta, v)` — immutable kinematic state.
- `Control(a, delta)` — immutable longitudinal/steering control.
- `OBB(cx, cy, width, length, theta)` — immutable oriented bounding box; `length` follows heading.
- `SimulationConfig(seed=42, runs=10000, stall_width=2.7, aisle_width=None, ped_density=None, suv_prob=None)`.
- `ManeuverTrace` — completed state-machine trace and safety metrics.

### Functions

- `ackermann_step(state, control, dt, wheelbase=2.8) -> VehicleState`
- `driver_eye_position(state) -> (x, y)`
- `line_of_sight_blocked(eye, target, obstacles) -> bool`
- `broad_phase_overlap(a, b) -> bool`
- `obb_collision(a, b) -> bool`
- `simulate_maneuver(strategy, rng, stall_width, aisle_width, suv_present, pedestrian_present, pedestrian_speed_mps=1.4) -> ManeuverTrace`
- `run_monte_carlo(config) -> list[dict]`
- `export_results_csv(rows, path) -> None`
- `canonical_paths() -> list[dict]`
- `export_canonical_paths(path) -> list[dict]`

`strategy` is exactly `"forward"` (nose-in) or `"reverse"` (back-in). Invalid strategies and out-of-range fixed simulation inputs raise `ValueError`.

If aisle width, pedestrian density, or SUV probability is `None`, each trial samples the SPEC ranges: 5.5–7.2 m, 0.05–0.30 pedestrians/m, and 0.0–0.8 respectively.

## CLI

```bash
python3 park_sim.py \
  --seed 42 \
  --runs 10000 \
  --stall-width 2.7 \
  --export-csv simulation_results.csv \
  --export-json canonical_paths.json
```

Optional `--aisle-width`, `--ped-density`, and `--suv-prob` pin those variables rather than sampling their ranges. The process prints one JSON summary to stdout and exits 0 on success.

## `simulation_results.csv`

One row per trial, UTF-8, comma-delimited, with this exact ordered header:

```text
run_id,strategy,seed,stall_width_m,aisle_width_m,ped_density_per_m,suv_probability,suv_present,pedestrian_count,pedestrian_speed_mps,total_cycle_time_s,entry_time_s,park_time_s,exit_time_s,reaction_time_s,gear_shifts,los_blocked,creep_activated,max_blind_exit_speed_mps,pedestrian_reaction_probability,pedestrian_reacted,min_pedestrian_distance_m,proximity_warning,required_braking_mps2,critical_conflict,collision
```

- Empty `pedestrian_speed_mps` / `min_pedestrian_distance_m` means no pedestrian was generated.
- Boolean fields serialize as `True` or `False`.
- `total_cycle_time_s = entry_time_s + park_time_s + exit_time_s` (subject only to four-decimal CSV rounding).
- `pedestrian_reaction_probability` uses `0.7 * (1 - visibility_factor) * (1 - vehicle_speed / 1.2)`; a reacting pedestrian gains 0.75 m clearance scaled by pedestrian speed relative to 1.4 m/s.
- `required_braking_mps2` is `v²/(2d)`, using exit speed and a 0.05 m numerical floor on `d`.
- `critical_conflict=True` means required braking is greater than 3.0 m/s².
- `proximity_warning=True` means modeled pedestrian proximity is below 1.5 m.
- `collision=True` means modeled pedestrian proximity is below 0.25 m.

## `canonical_paths.json`

Top-level schema:

```json
{
  "schema_version": "1.0",
  "coordinate_frame": "rear_axle_centroid",
  "units": {
    "position": "m",
    "heading": "rad",
    "velocity": "m/s"
  },
  "paths": []
}
```

There are exactly 10 paths: forward and reverse variants at aisle widths 5.50, 5.90, 6.35, 6.80, and 7.20 m. Each path is:

```json
{
  "id": "forward-aisle-5.50",
  "strategy": "forward",
  "parameters": {
    "aisle_width_m": 5.5,
    "stall_width_m": 2.7
  },
  "points": [
    {"x": -2.75, "y": 0.0, "theta": 0.0, "v": 1.0}
  ]
}
```

Frontend rules:

1. Treat points as ordered samples; interpolate between adjacent points for animation.
2. Do not reinterpret the coordinates as vehicle centroids; they are rear-axle centroids.
3. A negative `v` means reverse. Do not infer gear from point order.
4. Use `theta` directly in radians. Canvas renderers with a downward-positive y-axis must invert y or the rotation sign consistently.
5. Select paths by `id` or by exact `strategy` plus nearest `aisle_width_m`; array order is not an API guarantee.
