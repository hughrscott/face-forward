#!/usr/bin/env python3
"""One-at-a-time sensitivity analysis for the physics-derived safety model."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sys
from typing import Iterator

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import park_sim

DEFAULT_FIGURE_PATH = Path(__file__).resolve().parent / "sensitivity_tornado.pdf"
DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "sensitivity_analysis.json"

PARAMETER_SWEEPS = {
    "REACTION_MEAN_S": (0.525, 0.975),
    "REACTION_STD_S": (0.105, 0.195),
    "GEAR_SHIFT_DELAY_S": (0.7, 1.3),
    "CREEP_SPEED_MPS": (0.35, 0.65),
    "SCAN_SWEEP_S": (0.35, 0.65),
    "PEDESTRIAN_SPEED_MEAN_MPS": (0.98, 1.82),
    "PEDESTRIAN_SPEED_STD_MPS": (0.14, 0.26),
    "COLLISION_THRESHOLD_M": (0.175, 0.325),
    "CONFLICT_BRAKING_MPS2": (2.1, 3.9),
}
ENTRY_SPEEDS_MPS = (0.5, 0.8, 0.95, 1.2, 1.5)


@contextmanager
def _temporary_constants(changes: dict[str, float]) -> Iterator[None]:
    original = {name: getattr(park_sim, name) for name in changes}
    try:
        for name, value in changes.items():
            setattr(park_sim, name, value)
        yield
    finally:
        for name, value in original.items():
            setattr(park_sim, name, value)


def _metrics(runs: int, seed: int) -> dict[str, float]:
    rows = park_sim.run_monte_carlo(park_sim.SimulationConfig(seed=seed, runs=runs))
    groups = {
        strategy: [row for row in rows if row["strategy"] == strategy]
        for strategy in ("forward", "reverse")
    }
    rates = {
        strategy: sum(bool(row["critical_conflict"]) for row in group) / len(group)
        for strategy, group in groups.items()
    }
    continuity = 1.0 / max(1, runs // 2)
    return {
        "forward_conflict_rate": rates["forward"],
        "reverse_conflict_rate": rates["reverse"],
        "conflict_ratio_forward_to_reverse":
            (rates["forward"] + continuity) / (rates["reverse"] + continuity),
        "forward_mean_entry_time_s": sum(row["entry_time_s"] for row in groups["forward"]) / len(groups["forward"]),
        "reverse_mean_entry_time_s": sum(row["entry_time_s"] for row in groups["reverse"]) / len(groups["reverse"]),
    }


def run_sensitivity_analysis(
    runs: int = 3_000,
    seed: int = 42,
    figure_path: str | Path = DEFAULT_FIGURE_PATH,
    output_path: str | Path = DEFAULT_OUTPUT_PATH,
) -> dict[str, object]:
    """Run deterministic OAT sweeps and write the tornado plot plus JSON data."""
    if runs < 20:
        raise ValueError("runs must be at least 20")
    baseline = _metrics(runs, seed)
    tornado = []
    for parameter, (low, high) in PARAMETER_SWEEPS.items():
        with _temporary_constants({parameter: low}):
            low_metrics = _metrics(runs, seed)
        with _temporary_constants({parameter: high}):
            high_metrics = _metrics(runs, seed)
        tornado.append({
            "parameter": parameter,
            "low": low,
            "high": high,
            "low_conflict_ratio": low_metrics["conflict_ratio_forward_to_reverse"],
            "high_conflict_ratio": high_metrics["conflict_ratio_forward_to_reverse"],
            "span": abs(
                high_metrics["conflict_ratio_forward_to_reverse"]
                - low_metrics["conflict_ratio_forward_to_reverse"]
            ),
        })
    tornado.sort(key=lambda item: item["span"], reverse=True)

    entry_speed_sweep = []
    for speed in ENTRY_SPEEDS_MPS:
        with _temporary_constants({
            "FORWARD_ENTRY_SPEED_MPS": speed,
            "REVERSE_ENTRY_SPEED_MPS": speed,
        }):
            metrics = _metrics(runs, seed)
        entry_speed_sweep.append({"speed_mps": speed, **metrics})

    destination = Path(figure_path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    baseline_ratio = baseline["conflict_ratio_forward_to_reverse"]
    changes = [
        value - baseline_ratio
        for entry in tornado
        for value in (entry["low_conflict_ratio"], entry["high_conflict_ratio"])
    ]
    if destination.suffix.lower() == ".pdf":
        try:
            import matplotlib.pyplot as plt
        except ImportError as exc:
            raise RuntimeError("matplotlib is required to write a PDF tornado plot") from exc
        labels = [entry["parameter"].replace("_", " ").title() for entry in tornado]
        lows = [entry["low_conflict_ratio"] - baseline_ratio for entry in tornado]
        highs = [entry["high_conflict_ratio"] - baseline_ratio for entry in tornado]
        y = list(range(len(tornado)))
        fig, axis = plt.subplots(figsize=(8.0, 5.2))
        axis.barh(y, lows, color="#4477AA", alpha=0.85, label="Low bound")
        axis.barh(y, highs, color="#CC6677", alpha=0.85, label="High bound")
        axis.axvline(0.0, color="black", linewidth=0.8)
        axis.set_yticks(y, labels)
        axis.invert_yaxis()
        axis.set_xlabel("Change in forward/reverse critical-conflict ratio")
        axis.legend(frameon=False)
        axis.grid(axis="x", alpha=0.2)
        fig.tight_layout()
        fig.savefig(destination)
        plt.close(fig)
    else:
        _write_svg_tornado(destination, tornado, baseline_ratio, changes)

    result: dict[str, object] = {
        "runs_per_scenario": runs,
        "seed": seed,
        "method": "one-at-a-time ±30% sweep with deterministic common seed",
        "baseline": baseline,
        "tornado": tornado,
        "entry_speed_sweep": entry_speed_sweep,
    }
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def _write_svg_tornado(
    destination: Path,
    tornado: list[dict[str, object]],
    baseline_ratio: float,
    changes: list[float],
) -> None:
    """Dependency-free SVG fallback used by the unit tests and minimal installs."""
    scale = 230.0 / max([abs(value) for value in changes] + [0.01])
    centre_x, row_height = 520, 42
    height = 90 + row_height * len(tornado)
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="900" height="{height}" viewBox="0 0 900 {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        '<style>text{font-family:Arial,sans-serif;font-size:13px}.title{font-size:18px;font-weight:bold}.axis{stroke:#222;stroke-width:1}</style>',
        '<text x="24" y="30" class="title">Critical-conflict ratio sensitivity</text>',
        '<text x="24" y="52">One-at-a-time ±30% parameter bounds; bars show change from baseline</text>',
        f'<line x1="{centre_x}" y1="65" x2="{centre_x}" y2="{height - 25}" class="axis"/>',
    ]
    for index, entry in enumerate(tornado):
        y = 82 + index * row_height
        label = entry["parameter"].replace("_", " ").title()
        parts.append(f'<text x="24" y="{y + 14}">{label}</text>')
        for value, color, offset in (
            (entry["low_conflict_ratio"] - baseline_ratio, "#4477AA", 2),
            (entry["high_conflict_ratio"] - baseline_ratio, "#CC6677", 20),
        ):
            width = abs(value) * scale
            x = centre_x if value >= 0 else centre_x - width
            parts.append(f'<rect x="{x:.2f}" y="{y + offset}" width="{width:.2f}" height="14" fill="{color}"/>')
    parts.append('</svg>')
    destination.write_text("\n".join(parts) + "\n", encoding="utf-8")


if __name__ == "__main__":
    payload = run_sensitivity_analysis()
    print(json.dumps({
        "figure": str(DEFAULT_FIGURE_PATH),
        "output": str(DEFAULT_OUTPUT_PATH),
        "parameters": len(payload["tornado"]),
    }))
