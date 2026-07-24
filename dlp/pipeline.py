#!/usr/bin/env python3
"""Scene-independent, outcome-blind DLP maneuver inventory pipeline."""
from __future__ import annotations

import re
import math
import json
import argparse
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median
from typing import Callable, Sequence

SCENE_FILE = re.compile(
    r"^(DJI_\d{4})_(agents|frames|instances|obstacles|scene)\.json$"
)
REQUIRED_SUFFIXES = frozenset({"agents", "frames", "instances", "obstacles", "scene"})
VEHICLE_TYPES = frozenset({"Car", "Bus", "Truck", "Medium Vehicle", "Motorcycle"})

PARKING_LAYOUT = {
    "A": (28.53, 138.42, 68.51, 73.73, 1, 42),
    "B": (7.71, 76.54, 50.40, 61.40, 2, 25),
    "C": (83.82, 138.42, 50.40, 61.40, 2, 21),
    "D": (7.71, 76.54, 31.93, 43.24, 2, 25),
    "E": (83.82, 138.42, 31.93, 43.24, 2, 21),
    "F": (7.71, 76.54, 13.51, 24.68, 2, 25),
    "G": (83.82, 138.42, 13.51, 24.68, 2, 21),
    "H": (7.71, 76.54, 0.95, 6.48, 1, 25),
    "I": (83.82, 138.42, 0.95, 6.48, 1, 21),
}


@dataclass(frozen=True)
class Stall:
    stall_id: str
    area: str
    row: int
    column: int
    xmin: float
    xmax: float
    ymin: float
    ymax: float

    @property
    def center(self) -> tuple[float, float]:
        return ((self.xmin + self.xmax) / 2, (self.ymin + self.ymax) / 2)

    def contains(self, point: tuple[float, float] | list[float]) -> bool:
        x, y = point
        return self.xmin <= x <= self.xmax and self.ymin <= y <= self.ymax


@dataclass(frozen=True)
class EventCandidate:
    event_id: str
    scene_id: str
    scene_token: str
    agent_token: str
    agent_type: str
    vehicle_length: float
    vehicle_width: float
    event_type: str
    stall_id: str
    parking_area: str
    method: str
    method_confidence: float
    crossing_index: int | None
    legacy_start_index: int
    legacy_end_index: int
    start_index: int
    end_index: int
    start_timestamp: float
    end_timestamp: float
    duration_seconds: float
    censoring: str
    complete: bool


@dataclass(frozen=True)
class SceneInventory:
    scene_id: str
    scene_token: str
    fps: float
    agent_count: int
    vehicle_agent_count: int
    instance_count: int
    obstacle_count: int
    events: tuple[EventCandidate, ...]


def generate_stalls() -> tuple[Stall, ...]:
    """Divide the published parking-area bounds into individual stall polygons."""
    stalls: list[Stall] = []
    for area, (xmin, xmax, ymin, ymax, rows, columns) in PARKING_LAYOUT.items():
        width = (xmax - xmin) / columns
        height = (ymax - ymin) / rows
        for row in range(1, rows + 1):
            for column in range(1, columns + 1):
                stalls.append(
                    Stall(
                        stall_id=f"{area}-R{row}-C{column:02d}",
                        area=area,
                        row=row,
                        column=column,
                        xmin=xmin + (column - 1) * width,
                        xmax=xmin + column * width,
                        ymin=ymin + (row - 1) * height,
                        ymax=ymin + row * height,
                    )
                )
    return tuple(stalls)


def stall_for_point(
    point: tuple[float, float] | list[float], stalls: tuple[Stall, ...]
) -> Stall | None:
    return next((stall for stall in stalls if stall.contains(point)), None)


def obstacle_stall_coverage(
    obstacles: dict, stalls: tuple[Stall, ...]
) -> tuple[int, int, float]:
    """Count static-obstacle centroids that fall inside generated stalls."""
    total = len(obstacles)
    covered = sum(
        stall_for_point(obstacle["coords"], stalls) is not None
        for obstacle in obstacles.values()
    )
    return covered, total, covered / total if total else 0.0


def _motion_sign(rows: list[dict], index: int) -> int:
    lo = max(0, index - 1)
    hi = min(len(rows) - 1, index + 1)
    dx = rows[hi]["coords"][0] - rows[lo]["coords"][0]
    dy = rows[hi]["coords"][1] - rows[lo]["coords"][1]
    heading = rows[index]["heading"]
    dot = math.cos(heading) * dx + math.sin(heading) * dy
    if math.isclose(dot, 0.0, abs_tol=1e-9):
        return 0
    return 1 if dot > 0 else -1


def classify_stall_crossing(
    rows: list[dict],
    stall: Stall,
    event_type: str,
    *,
    moving_speed: float = 0.10,
    evidence_frames: int = 1,
    minimum_motion_samples: int = 1,
) -> tuple[str, float, int | None]:
    """Classify motion at the stall boundary, separate from the timing window."""
    inside = [stall.contains(row["coords"]) for row in rows]
    if event_type == "parking":
        crossings = [i for i in range(1, len(rows)) if inside[i] and not inside[i - 1]]
        crossing = crossings[-1] if crossings else None
    elif event_type == "unparking":
        crossings = [i for i in range(1, len(rows)) if not inside[i] and inside[i - 1]]
        crossing = crossings[0] if crossings else None
    else:
        raise ValueError(f"Unsupported event type: {event_type}")

    if crossing is None:
        return "unclear", 0.0, None

    signs = [
        _motion_sign(rows, i)
        for i in range(
            max(0, crossing - evidence_frames),
            min(len(rows), crossing + evidence_frames + 1),
        )
        if rows[i]["speed"] >= moving_speed
    ]
    signs = [sign for sign in signs if sign]
    if len(signs) < minimum_motion_samples:
        return "unclear", 0.0, crossing
    forward_share = sum(sign > 0 for sign in signs) / len(signs)
    reverse_share = sum(sign < 0 for sign in signs) / len(signs)
    if forward_share >= 0.70:
        return "forward", forward_share, crossing
    if reverse_share >= 0.70:
        return "reverse", reverse_share, crossing
    return "mixed", max(forward_share, reverse_share), crossing


def _static_runs(
    rows: list[dict], stalls: tuple[Stall, ...], minimum_frames: int
) -> list[tuple[int, int, Stall]]:
    runs: list[tuple[int, int, Stall]] = []
    start: int | None = None
    current: Stall | None = None

    for index, row in enumerate(rows + [None]):
        stall = None
        if row is not None and row["speed"] < 0.05:
            stall = stall_for_point(row["coords"], stalls)
        if current is not None and (stall is None or stall.stall_id != current.stall_id):
            assert start is not None
            if index - start >= minimum_frames:
                runs.append((start, index - 1, current))
            start = None
            current = None
        if stall is not None and current is None:
            start = index
            current = stall
    return runs


def _merge_fragmented_static_runs(
    runs: list[tuple[int, int, Stall]],
    rows: list[dict],
    maximum_gap_frames: int,
) -> list[tuple[int, int, Stall]]:
    """Join qualified same-stall runs separated only by brief speed noise."""
    merged: list[tuple[int, int, Stall]] = []
    for start, end, stall in runs:
        if merged:
            previous_start, previous_end, previous_stall = merged[-1]
            gap = rows[previous_end + 1:start]
            gap_is_speed_noise = (
                len(gap) <= maximum_gap_frames
                and all(row["speed"] < 0.10 for row in gap)
            )
            gap_is_in_stall_repositioning = (
                bool(gap)
                and all(stall.contains(row["coords"]) for row in gap)
            )
            if (
                stall.stall_id == previous_stall.stall_id
                and (gap_is_speed_noise or gap_is_in_stall_repositioning)
            ):
                merged[-1] = (previous_start, end, previous_stall)
                continue
        merged.append((start, end, stall))
    return merged


def _first_sustained_movement(
    rows: list[dict],
    start: int,
    minimum_frames: int,
    stop: int | None = None,
) -> int | None:
    stop = len(rows) if stop is None else min(stop, len(rows))
    for index in range(start, stop - minimum_frames + 1):
        if all(row["speed"] >= 0.10 for row in rows[index:index + minimum_frames]):
            return index
    return None


def _axis_difference(angle: float, axis: float) -> float:
    difference = abs((angle - axis) % math.pi)
    return min(difference, math.pi - difference)


def _stall_axes(stall: Stall) -> tuple[float, float]:
    """Return the stall's long axis and the perpendicular aisle axis."""
    long_axis = (
        math.pi / 2
        if stall.ymax - stall.ymin >= stall.xmax - stall.xmin
        else 0.0
    )
    return long_axis, (long_axis + math.pi / 2) % math.pi


def _path_axis(rows: list[dict], index: int, span_frames: int) -> float | None:
    lower = max(0, index - span_frames)
    upper = min(len(rows) - 1, index + span_frames)
    dx = rows[upper]["coords"][0] - rows[lower]["coords"][0]
    dy = rows[upper]["coords"][1] - rows[lower]["coords"][1]
    if math.hypot(dx, dy) < 0.05:
        return None
    return math.atan2(dy, dx) % math.pi


def _first_sustained_state(
    states: list[bool],
    start: int,
    stop: int,
    minimum_frames: int,
    *,
    minimum_share: float = 0.8,
) -> int | None:
    """Return the first mostly-true state window in ``[start, stop)``."""
    minimum_frames = max(1, minimum_frames)
    start = max(0, start)
    stop = min(stop, len(states))
    for index in range(start, stop - minimum_frames + 1):
        window = states[index:index + minimum_frames]
        if sum(window) / minimum_frames >= minimum_share:
            return index
    return None


def _semantic_parking_start(
    rows: list[dict],
    stall: Stall,
    *,
    legacy_start: int,
    crossing: int,
    fps: float,
) -> int:
    """Find the first committed maneuver after established aisle travel."""
    _, aisle_axis = _stall_axes(stall)
    path_span = max(1, round(0.2 * fps))
    path_deviation: list[float | None] = []
    heading_deviation: list[float] = []
    for index, row in enumerate(rows):
        path = _path_axis(rows, index, path_span)
        path_deviation.append(
            None if path is None else _axis_difference(path, aisle_axis)
        )
        heading_deviation.append(
            _axis_difference(float(row["heading"]) % math.pi, aisle_axis)
        )

    history_start = max(0, legacy_start - round(5.0 * fps))
    search_stop = min(len(rows), crossing + 1)
    established_stop = min(search_stop, legacy_start + round(2.0 * fps) + 1)
    established = [
        row["speed"] >= 0.50
        and path is not None
        and path <= math.radians(15)
        and heading <= math.radians(20)
        for row, path, heading in zip(rows, path_deviation, heading_deviation)
    ]
    established_start = _first_sustained_state(
        established,
        history_start,
        established_stop,
        round(0.5 * fps),
    )
    if established_start is None:
        return legacy_start

    baseline_stop = min(search_stop, established_start + round(1.5 * fps) + 1)
    baseline_speeds = [
        rows[index]["speed"]
        for index in range(established_start, baseline_stop)
        if established[index]
    ]
    if not baseline_speeds:
        return legacy_start
    baseline_speed = median(baseline_speeds)

    angle_change = [
        row["speed"] <= 2.0
        and (
            (path is not None and path >= math.radians(18))
            or heading >= math.radians(20)
        )
        for row, path, heading in zip(rows, path_deviation, heading_deviation)
    ]
    deceleration = [
        row["speed"] <= 0.40 * baseline_speed
        for row in rows
    ]
    sustain_frames = round(0.5 * fps)
    candidates = [
        candidate
        for candidate in (
            _first_sustained_state(
                angle_change,
                established_start,
                search_stop,
                sustain_frames,
            ),
            _first_sustained_state(
                deceleration,
                established_start,
                search_stop,
                sustain_frames,
            ),
        )
        if candidate is not None
    ]
    return min(candidates) if candidates else legacy_start


def _semantic_parking_end(
    rows: list[dict],
    stall: Stall,
    *,
    episode_start: int,
    episode_end: int,
    minimum_static_frames: int,
    fps: float,
) -> int:
    """Return the confirmed parked state in the final static run."""
    qualified_runs: list[tuple[int, int]] = []
    run_start: int | None = None
    for index in range(episode_start, episode_end + 2):
        is_static = (
            index <= episode_end
            and rows[index]["speed"] < 0.05
            and stall.contains(rows[index]["coords"])
        )
        if is_static and run_start is None:
            run_start = index
        elif not is_static and run_start is not None:
            if index - run_start >= minimum_static_frames:
                qualified_runs.append((run_start, index - 1))
            run_start = None
    if not qualified_runs:
        return episode_start
    final_start, final_end = qualified_runs[-1]
    return min(final_end, final_start + round(1.2 * fps))


def _semantic_unparking_start(
    rows: list[dict],
    *,
    legacy_start: int,
    stop: int,
    fps: float,
) -> int:
    """Ignore low-speed jitter and start at sustained committed movement."""
    committed_movement = [row["speed"] >= 0.25 for row in rows]
    refinement_stop = min(stop, legacy_start + round(2.0 * fps) + 1)
    start = _first_sustained_state(
        committed_movement,
        legacy_start,
        refinement_stop,
        round(0.6 * fps),
        minimum_share=1.0,
    )
    return legacy_start if start is None else start


def _semantic_unparking_end(
    rows: list[dict],
    stall: Stall,
    *,
    crossing: int,
    legacy_end: int,
    fps: float,
) -> int:
    """End at the first sustained outside-stall aisle-travel state."""
    _, aisle_axis = _stall_axes(stall)
    path_span = max(1, round(0.2 * fps))
    established_aisle: list[bool] = []
    for index, row in enumerate(rows):
        path = _path_axis(rows, index, path_span)
        established_aisle.append(
            not stall.contains(row["coords"])
            and row["speed"] >= 0.75
            and path is not None
            and _axis_difference(path, aisle_axis) <= math.radians(30)
            and _axis_difference(float(row["heading"]) % math.pi, aisle_axis)
            <= math.radians(15)
        )
    end = _first_sustained_state(
        established_aisle,
        crossing,
        min(len(rows), legacy_end + round(10.0 * fps) + 1),
        round(0.2 * fps),
        minimum_share=1.0,
    )
    return legacy_end if end is None else end


def detect_trajectory_events(
    scene_id: str,
    scene_token: str,
    agent: dict,
    rows: list[dict],
    stalls: tuple[Stall, ...],
    *,
    fps: float,
    static_seconds: float = 2.0,
    moving_seconds: float = 0.5,
    envelope_metres: float = 8.0,
) -> list[EventCandidate]:
    """Find broad parking/unparking candidates without aggregating outcomes."""
    static_frames = max(1, round(static_seconds * fps))
    moving_frames = max(1, round(moving_seconds * fps))
    events: list[EventCandidate] = []
    size: Sequence[float] = agent.get("size", (0.0, 0.0))

    static_runs = _static_runs(rows, stalls, static_frames)
    static_runs = _merge_fragmented_static_runs(
        static_runs,
        rows,
        maximum_gap_frames=max(1, round(fps)),
    )
    for run_index, (run_start, run_end, stall) in enumerate(static_runs):
        if run_start > 0:
            start = run_start
            while start > 0 and math.dist(rows[start - 1]["coords"], stall.center) <= envelope_metres:
                start -= 1
            left_censored = start == 0 and math.dist(rows[0]["coords"], stall.center) <= envelope_metres
            segment = rows[start:run_start + 1]
            method, confidence, crossing = classify_stall_crossing(
                segment,
                stall,
                "parking",
                evidence_frames=max(1, round(0.5 * fps)),
                minimum_motion_samples=min(3, max(1, round(0.12 * fps))),
            )
            crossing_index = None if crossing is None else start + crossing
            semantic_start = _semantic_parking_start(
                rows,
                stall,
                legacy_start=start,
                crossing=crossing_index if crossing_index is not None else run_start,
                fps=fps,
            )
            semantic_end = _semantic_parking_end(
                rows,
                stall,
                episode_start=run_start,
                episode_end=run_end,
                minimum_static_frames=static_frames,
                fps=fps,
            )
            censoring = "left" if left_censored else "none"
            events.append(
                EventCandidate(
                    event_id=f"{scene_id}:{agent['agent_token']}:parking:{run_start}",
                    scene_id=scene_id,
                    scene_token=scene_token,
                    agent_token=agent["agent_token"],
                    agent_type=agent["type"],
                    vehicle_length=float(size[0]),
                    vehicle_width=float(size[1]),
                    event_type="parking",
                    stall_id=stall.stall_id,
                    parking_area=stall.area,
                    method=method,
                    method_confidence=confidence,
                    crossing_index=crossing_index,
                    legacy_start_index=start,
                    legacy_end_index=run_start,
                    start_index=semantic_start,
                    end_index=semantic_end,
                    start_timestamp=float(rows[semantic_start]["timestamp"]),
                    end_timestamp=float(rows[semantic_end]["timestamp"]),
                    duration_seconds=float(
                        rows[semantic_end]["timestamp"]
                        - rows[semantic_start]["timestamp"]
                    ),
                    censoring=censoring,
                    complete=not left_censored,
                )
            )

        next_run_start = (
            static_runs[run_index + 1][0]
            if run_index + 1 < len(static_runs)
            else None
        )
        movement_start = _first_sustained_movement(
            rows,
            run_end + 1,
            moving_frames,
            stop=next_run_start,
        )
        if movement_start is not None:
            end = movement_start
            while end < len(rows) - 1 and math.dist(rows[end]["coords"], stall.center) <= envelope_metres:
                end += 1
            right_censored = end == len(rows) - 1 and math.dist(rows[end]["coords"], stall.center) <= envelope_metres
            classification_start = min(run_end, movement_start)
            segment = rows[classification_start:end + 1]
            method, confidence, crossing = classify_stall_crossing(
                segment,
                stall,
                "unparking",
                evidence_frames=max(1, round(0.5 * fps)),
                minimum_motion_samples=min(3, max(1, round(0.12 * fps))),
            )
            crossing_index = None if crossing is None else classification_start + crossing
            semantic_start = _semantic_unparking_start(
                rows,
                legacy_start=movement_start,
                stop=end + 1,
                fps=fps,
            )
            semantic_end = _semantic_unparking_end(
                rows,
                stall,
                crossing=(
                    crossing_index if crossing_index is not None else movement_start
                ),
                legacy_end=end,
                fps=fps,
            )
            censoring = "right" if right_censored else "none"
            events.append(
                EventCandidate(
                    event_id=f"{scene_id}:{agent['agent_token']}:unparking:{movement_start}",
                    scene_id=scene_id,
                    scene_token=scene_token,
                    agent_token=agent["agent_token"],
                    agent_type=agent["type"],
                    vehicle_length=float(size[0]),
                    vehicle_width=float(size[1]),
                    event_type="unparking",
                    stall_id=stall.stall_id,
                    parking_area=stall.area,
                    method=method,
                    method_confidence=confidence,
                    crossing_index=crossing_index,
                    legacy_start_index=movement_start,
                    legacy_end_index=end,
                    start_index=semantic_start,
                    end_index=semantic_end,
                    start_timestamp=float(rows[semantic_start]["timestamp"]),
                    end_timestamp=float(rows[semantic_end]["timestamp"]),
                    duration_seconds=float(
                        rows[semantic_end]["timestamp"]
                        - rows[semantic_start]["timestamp"]
                    ),
                    censoring=censoring,
                    complete=not right_censored,
                )
            )
    return events


def _load_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def inventory_scene(
    data_dir: Path, scene_id: str, stalls: tuple[Stall, ...]
) -> SceneInventory:
    """Load one five-file scene bundle and return its unaggregated event ledger."""
    scene = _load_json(data_dir / f"{scene_id}_scene.json")
    agents = _load_json(data_dir / f"{scene_id}_agents.json")
    frames = _load_json(data_dir / f"{scene_id}_frames.json")
    instances = _load_json(data_dir / f"{scene_id}_instances.json")
    obstacles = _load_json(data_dir / f"{scene_id}_obstacles.json")

    timestamps = {token: float(frame["timestamp"]) for token, frame in frames.items()}
    ordered_times = sorted(timestamps.values())
    deltas = [b - a for a, b in zip(ordered_times, ordered_times[1:]) if b > a]
    if not deltas:
        raise ValueError(f"Scene {scene_id} has no positive frame intervals")
    fps = 1.0 / median(deltas)

    trajectories: dict[str, list[dict]] = {token: [] for token in agents}
    for instance in instances.values():
        enriched = dict(instance)
        enriched["timestamp"] = timestamps[instance["frame_token"]]
        trajectories[instance["agent_token"]].append(enriched)
    for rows in trajectories.values():
        rows.sort(key=lambda row: row["timestamp"])

    events: list[EventCandidate] = []
    vehicle_count = 0
    for token, agent in agents.items():
        if agent["type"] not in VEHICLE_TYPES:
            continue
        vehicle_count += 1
        events.extend(
            detect_trajectory_events(
                scene_id,
                scene["scene_token"],
                agent,
                trajectories[token],
                stalls,
                fps=fps,
            )
        )

    return SceneInventory(
        scene_id=scene_id,
        scene_token=scene["scene_token"],
        fps=fps,
        agent_count=len(agents),
        vehicle_agent_count=vehicle_count,
        instance_count=len(instances),
        obstacle_count=len(obstacles),
        events=tuple(events),
    )


def write_inventory_outputs(
    inventories: Sequence[SceneInventory], output_dir: Path
) -> tuple[Path, Path]:
    """Write event-level candidates and an outcome-blind per-scene inventory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    event_path = output_dir / "candidate_events.jsonl"
    scene_path = output_dir / "scene_inventory.json"

    with event_path.open("w", encoding="utf-8") as handle:
        for inventory in sorted(inventories, key=lambda item: item.scene_id):
            for event in inventory.events:
                handle.write(json.dumps(asdict(event), sort_keys=True) + "\n")

    scene_rows = [
        {
            "scene_id": inventory.scene_id,
            "scene_token": inventory.scene_token,
            "fps": inventory.fps,
            "agent_count": inventory.agent_count,
            "vehicle_agent_count": inventory.vehicle_agent_count,
            "instance_count": inventory.instance_count,
            "obstacle_count": inventory.obstacle_count,
            "event_count": len(inventory.events),
        }
        for inventory in sorted(inventories, key=lambda item: item.scene_id)
    ]
    scene_path.write_text(json.dumps(scene_rows, indent=2), encoding="utf-8")
    return event_path, scene_path


def run_inventory(
    data_dir: Path,
    output_dir: Path,
    *,
    progress: Callable[[str], object] | None = None,
) -> tuple[SceneInventory, ...]:
    """Inventory every complete scene sequentially and write blinded outputs."""
    stalls = generate_stalls()
    scenes = discover_scene_prefixes(data_dir)
    inventories: list[SceneInventory] = []
    for index, scene_id in enumerate(scenes, start=1):
        if progress:
            progress(f"[{index:02d}/{len(scenes):02d}] {scene_id}")
        inventories.append(inventory_scene(data_dir, scene_id, stalls))
    write_inventory_outputs(inventories, output_dir)
    return tuple(inventories)


def discover_scene_prefixes(data_dir: Path) -> list[str]:
    """Return complete scene prefixes; reject any partial scene bundle."""
    found: dict[str, set[str]] = {}
    for path in data_dir.glob("DJI_*.json"):
        match = SCENE_FILE.fullmatch(path.name)
        if match:
            found.setdefault(match.group(1), set()).add(match.group(2))

    for scene, suffixes in sorted(found.items()):
        missing = sorted(REQUIRED_SUFFIXES - suffixes)
        if missing:
            raise ValueError(f"Incomplete scene {scene}; missing: {', '.join(missing)}")
    return sorted(found)


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=root / "data")
    parser.add_argument("--output", type=Path, default=root / "results")
    args = parser.parse_args()

    inventories = run_inventory(args.data, args.output, progress=lambda line: print(line, flush=True))
    print(
        f"COMPLETE: {len(inventories)} scenes inventoried; "
        f"outputs written to {args.output}",
        flush=True,
    )


if __name__ == "__main__":
    main()
