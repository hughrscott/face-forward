#!/usr/bin/env python3
"""Inspect one DLP scene and inventory obvious parking/unparking candidates.

This is an exploratory census, not the final event detector. It deliberately uses
simple, auditable thresholds so we can see whether the raw trajectories support
Face Forward's intended analysis before building the production pipeline.
"""
from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from statistics import median

DATA = Path(__file__).resolve().parent / "data"
PREFIX = DATA / "DJI_0001"

PARKING_AREAS = {
    "A": (28.53, 138.42, 68.51, 73.73),
    "B": (7.71, 76.54, 50.40, 61.40),
    "C": (83.82, 138.42, 50.40, 61.40),
    "D": (7.71, 76.54, 31.93, 43.24),
    "E": (83.82, 138.42, 31.93, 43.24),
    "F": (7.71, 76.54, 13.51, 24.68),
    "G": (83.82, 138.42, 13.51, 24.68),
    "H": (7.71, 76.54, 0.95, 6.48),
    "I": (83.82, 138.42, 0.95, 6.48),
}
VEHICLE_TYPES = {"Car", "Bus", "Truck", "Medium Vehicle", "Motorcycle"}
STATIC_SPEED = 0.05
MOVING_SPEED = 0.10
STATIC_SECONDS = 2.0
APPROACH_RADIUS_M = 8.0


def load(suffix: str):
    with open(f"{PREFIX}_{suffix}.json", encoding="utf-8") as handle:
        return json.load(handle)


def parking_area(coords: list[float]) -> str | None:
    x, y = coords
    for name, (xmin, xmax, ymin, ymax) in PARKING_AREAS.items():
        if xmin <= x <= xmax and ymin <= y <= ymax:
            return name
    return None


def signed_motion(instances: list[dict]) -> list[float]:
    """Return heading-aligned displacement signs, scaled by stored speed."""
    result = []
    for i, inst in enumerate(instances):
        lo = max(0, i - 1)
        hi = min(len(instances) - 1, i + 1)
        dx = instances[hi]["coords"][0] - instances[lo]["coords"][0]
        dy = instances[hi]["coords"][1] - instances[lo]["coords"][1]
        heading = inst["heading"]
        dot = math.cos(heading) * dx + math.sin(heading) * dy
        result.append(inst["speed"] if dot >= 0 else -inst["speed"])
    return result


def sustained_static_edge(instances: list[dict], edge: str, fps: float) -> bool:
    n = max(1, round(STATIC_SECONDS * fps))
    sample = instances[:n] if edge == "start" else instances[-n:]
    return len(sample) == n and all(row["speed"] < STATIC_SPEED for row in sample)


def classify_direction(instances: list[dict], anchor: list[float]) -> tuple[str, float, int]:
    signed = signed_motion(instances)
    sample = []
    for inst, value in zip(instances, signed):
        distance = math.dist(inst["coords"], anchor)
        if distance <= APPROACH_RADIUS_M and abs(value) >= MOVING_SPEED:
            sample.append(value)
    if not sample:
        return "unclear", 0.0, 0
    forward_share = sum(value > 0 for value in sample) / len(sample)
    reverse_share = sum(value < 0 for value in sample) / len(sample)
    if forward_share >= 0.70:
        return "forward", forward_share, len(sample)
    if reverse_share >= 0.70:
        return "reverse", reverse_share, len(sample)
    return "mixed", max(forward_share, reverse_share), len(sample)


def parking_segment(rows: list[dict], fps: float) -> tuple[int, int] | None:
    """Return final entry into the 8 m stall envelope through terminal stop."""
    end = len(rows) - 1
    while end > 0 and rows[end - 1]["speed"] < STATIC_SPEED:
        end -= 1
    if len(rows) - end < round(STATIC_SECONDS * fps):
        return None
    anchor = rows[-1]["coords"]
    start = end
    while start > 0 and math.dist(rows[start - 1]["coords"], anchor) <= APPROACH_RADIUS_M:
        start -= 1
    return None if start == 0 else (start, end)


def unparking_segment(rows: list[dict], fps: float) -> tuple[int, int] | None:
    """Return end of initial stop through exit from the 8 m stall envelope."""
    start = 0
    while start < len(rows) - 1 and rows[start]["speed"] < STATIC_SPEED:
        start += 1
    if start < round(STATIC_SECONDS * fps):
        return None
    anchor = rows[0]["coords"]
    end = start
    while end < len(rows) - 1 and math.dist(rows[end]["coords"], anchor) <= APPROACH_RADIUS_M:
        end += 1
    if end == len(rows) - 1 and math.dist(rows[end]["coords"], anchor) <= APPROACH_RADIUS_M:
        return None
    return start, end


def main() -> None:
    scene = load("scene")
    agents = load("agents")
    frames = load("frames")
    instances = load("instances")
    obstacles = load("obstacles")

    timestamps = {token: row["timestamp"] for token, row in frames.items()}
    ordered_frame_times = sorted(timestamps.values())
    deltas = [b - a for a, b in zip(ordered_frame_times, ordered_frame_times[1:]) if b > a]
    frame_dt = median(deltas)
    fps = 1.0 / frame_dt

    trajectories: dict[str, list[dict]] = {token: [] for token in agents}
    for inst in instances.values():
        trajectories[inst["agent_token"]].append(inst)
    for rows in trajectories.values():
        rows.sort(key=lambda row: timestamps[row["frame_token"]])

    candidates = []
    for token, agent in agents.items():
        if agent["type"] not in VEHICLE_TYPES:
            continue
        rows = trajectories[token]
        if len(rows) < round(STATIC_SECONDS * fps) + 2:
            continue
        first_area = parking_area(rows[0]["coords"])
        last_area = parking_area(rows[-1]["coords"])
        ever_moving = any(row["speed"] >= MOVING_SPEED for row in rows)
        if not ever_moving:
            continue

        start_static = first_area is not None and sustained_static_edge(rows, "start", fps)
        end_static = last_area is not None and sustained_static_edge(rows, "end", fps)
        if end_static and not start_static:
            segment = parking_segment(rows, fps)
            if segment is None:
                continue
            event_start, event_end = segment
            event_rows = rows[event_start:event_end + 1]
            direction, confidence, n = classify_direction(event_rows, rows[-1]["coords"])
            duration = timestamps[rows[event_end]["frame_token"]] - timestamps[rows[event_start]["frame_token"]]
            candidates.append({
                "event": "parking", "agent": token[:10], "type": agent["type"],
                "area": last_area, "direction": direction, "share": confidence,
                "samples": n, "maneuver_seconds": round(duration, 2),
                "start": [round(v, 2) for v in rows[0]["coords"]],
                "end": [round(v, 2) for v in rows[-1]["coords"]],
            })
        if start_static and not end_static:
            segment = unparking_segment(rows, fps)
            if segment is None:
                continue
            event_start, event_end = segment
            event_rows = rows[event_start:event_end + 1]
            direction, confidence, n = classify_direction(event_rows, rows[0]["coords"])
            duration = timestamps[rows[event_end]["frame_token"]] - timestamps[rows[event_start]["frame_token"]]
            candidates.append({
                "event": "unparking", "agent": token[:10], "type": agent["type"],
                "area": first_area, "direction": direction, "share": confidence,
                "samples": n, "maneuver_seconds": round(duration, 2),
                "start": [round(v, 2) for v in rows[0]["coords"]],
                "end": [round(v, 2) for v in rows[-1]["coords"]],
            })

    output = {
        "scene": scene["filename"],
        "scene_timestamp": scene["timestamp"],
        "duration_seconds": round(ordered_frame_times[-1] - ordered_frame_times[0], 2),
        "frames": len(frames),
        "estimated_fps": round(fps, 4),
        "agents": len(agents),
        "agent_types": dict(Counter(row["type"] for row in agents.values())),
        "instances": len(instances),
        "obstacles": len(obstacles),
        "candidate_count": len(candidates),
        "candidate_summary": {
            f"{event}:{direction}": count
            for (event, direction), count in Counter(
                (row["event"], row["direction"]) for row in candidates
            ).items()
        },
        "candidates": candidates,
    }
    print(json.dumps(output, indent=2))


if __name__ == "__main__":
    main()
