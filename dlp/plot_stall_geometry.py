#!/usr/bin/env python3
"""Render generated DLP stall geometry against static-obstacle centroids."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from dlp.pipeline import generate_stalls, obstacle_stall_coverage, stall_for_point


def main() -> None:
    root = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--obstacles",
        type=Path,
        default=root / "data" / "DJI_0001_obstacles.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=root / "results" / "stall_geometry_validation.png",
    )
    args = parser.parse_args()

    obstacles = json.loads(args.obstacles.read_text(encoding="utf-8"))
    stalls = generate_stalls()
    covered, total, fraction = obstacle_stall_coverage(obstacles, stalls)

    colors = {
        "A": "#1f77b4", "B": "#ff7f0e", "C": "#2ca02c",
        "D": "#d62728", "E": "#9467bd", "F": "#8c564b",
        "G": "#e377c2", "H": "#7f7f7f", "I": "#bcbd22",
    }
    fig, ax = plt.subplots(figsize=(15, 9))
    for stall in stalls:
        ax.add_patch(
            Rectangle(
                (stall.xmin, stall.ymin),
                stall.xmax - stall.xmin,
                stall.ymax - stall.ymin,
                fill=False,
                edgecolor=colors[stall.area],
                linewidth=0.45,
                alpha=0.8,
            )
        )

    inside_x, inside_y, outside_x, outside_y = [], [], [], []
    for obstacle in obstacles.values():
        x, y = obstacle["coords"]
        if stall_for_point(obstacle["coords"], stalls):
            inside_x.append(x)
            inside_y.append(y)
        else:
            outside_x.append(x)
            outside_y.append(y)
    ax.scatter(inside_x, inside_y, s=13, c="#111111", label="obstacle centroid inside stall")
    ax.scatter(outside_x, outside_y, s=20, c="#e31a1c", marker="x", label="centroid outside stall")

    for area in colors:
        area_stalls = [stall for stall in stalls if stall.area == area]
        x = sum(stall.center[0] for stall in area_stalls) / len(area_stalls)
        y = sum(stall.center[1] for stall in area_stalls) / len(area_stalls)
        ax.text(x, y, area, fontsize=13, weight="bold", ha="center", va="center",
                bbox={"facecolor": "white", "alpha": 0.75, "edgecolor": "none"})

    ax.set_title(
        f"Generated DLP stall grid vs. scene-1 static obstacles — {covered}/{total} "
        f"centroids inside ({fraction:.1%})"
    )
    ax.set_xlabel("x (m)")
    ax.set_ylabel("y (m)")
    ax.set_aspect("equal", adjustable="box")
    ax.grid(alpha=0.15)
    ax.legend(loc="upper right")
    fig.tight_layout()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(args.output, dpi=180)
    print(f"WROTE {args.output}")
    print(f"COVERAGE {covered}/{total} ({fraction:.6f})")


if __name__ == "__main__":
    main()
