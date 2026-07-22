#!/usr/bin/env python3
"""Plot the first-scene DLP maneuver candidates for visual QA."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from inspect_scene import (
    PARKING_AREAS,
    classify_direction,
    parking_segment,
    unparking_segment,
)

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = ROOT / "results"
OUT.mkdir(exist_ok=True)

CANDIDATES = {
    "5cd75d2991": "unparking",
    "04420fc6a7": "unparking",
    "d0198723b1": "parking",
    "e51d4eba8b": "parking",
    "fc6c383407": "parking",
    "360f3f08f1": "parking",
    "9c5ba92d3e": "parking",
    "008e628146": "parking",
}


def load(name: str):
    with open(DATA / f"DJI_0001_{name}.json", encoding="utf-8") as handle:
        return json.load(handle)


agents = load("agents")
frames = load("frames")
instances = load("instances")
timestamps = {token: row["timestamp"] for token, row in frames.items()}
trajectories = {token: [] for token in agents}
for inst in instances.values():
    trajectories[inst["agent_token"]].append(inst)
for rows in trajectories.values():
    rows.sort(key=lambda row: timestamps[row["frame_token"]])

fig, axes = plt.subplots(2, 4, figsize=(18, 9), constrained_layout=True)
for ax, (prefix, event) in zip(axes.flat, CANDIDATES.items()):
    token = next(token for token in agents if token.startswith(prefix))
    rows = trajectories[token]
    segment = parking_segment(rows, 25.0) if event == "parking" else unparking_segment(rows, 25.0)
    if segment is None:
        continue
    start, end = segment
    event_rows = rows[start:end + 1]
    anchor = rows[-1]["coords"] if event == "parking" else rows[0]["coords"]
    direction, share, _ = classify_direction(event_rows, anchor)

    ax.plot([r["coords"][0] for r in rows], [r["coords"][1] for r in rows], color="#b8b8b8", lw=1)
    color = {"forward": "#17823b", "reverse": "#b3261e", "mixed": "#d89b00", "unclear": "#777"}[direction]
    ax.plot([r["coords"][0] for r in event_rows], [r["coords"][1] for r in event_rows], color=color, lw=3)
    ax.scatter(*event_rows[0]["coords"], marker="o", color="#2364aa", s=40, label="segment start")
    ax.scatter(*event_rows[-1]["coords"], marker="x", color="#111", s=55, label="segment end")

    for name, (xmin, xmax, ymin, ymax) in PARKING_AREAS.items():
        ax.add_patch(Rectangle((xmin, ymin), xmax - xmin, ymax - ymin, fill=False, lw=0.4, ec="#ddd"))

    duration = timestamps[event_rows[-1]["frame_token"]] - timestamps[event_rows[0]["frame_token"]]
    ax.set_title(f"{prefix} · {event}\n{direction} ({share:.0%}), {duration:.2f}s")
    ax.set_aspect("equal", adjustable="datalim")
    ax.grid(alpha=0.2)
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")

fig.suptitle("DLP DJI_0001 — exploratory maneuver candidates\nGray: complete track; colored: provisional 8 m maneuver segment", fontsize=16)
path = OUT / "DJI_0001_candidate_maneuvers.png"
fig.savefig(path, dpi=160)
print(path)
