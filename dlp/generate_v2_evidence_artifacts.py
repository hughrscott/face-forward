#!/usr/bin/env python3
"""Generate Face Forward's web and print artifacts from the frozen v2 result."""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch

FOREST = "#131316"
EMERALD = "#202024"
CHALK = "#F2F0EA"
GOLD = "#E8B62B"
MUTED = "#A7A6A1"
GREEN = "#C9C7C0"
GRID = "#3B3B3F"
SLATE = "#777773"

plt.rcParams.update(
    {
        "figure.facecolor": FOREST,
        "axes.facecolor": FOREST,
        "savefig.facecolor": FOREST,
        "font.family": "DejaVu Sans",
        "font.size": 13,
        "text.color": CHALK,
        "axes.labelcolor": MUTED,
        "xtick.color": MUTED,
        "ytick.color": CHALK,
        "axes.edgecolor": GRID,
        "svg.fonttype": "none",
        "pdf.fonttype": 42,
    }
)


def _brand_header(fig: Figure, eyebrow: str, title: str, subtitle: str) -> None:
    fig.text(0.07, 0.925, eyebrow.upper(), color=GOLD, fontsize=10, weight="bold")
    fig.text(0.07, 0.865, title, color=CHALK, fontsize=27, weight="bold")
    fig.text(0.07, 0.815, subtitle, color=MUTED, fontsize=11.5)


def _footer(fig: Figure, text: str) -> None:
    fig.text(0.07, 0.035, text, color=MUTED, fontsize=8.5)
    fig.text(0.93, 0.035, "FACE FORWARD · OBSERVED EVIDENCE", color=GOLD, fontsize=8, ha="right", weight="bold")


def lifecycle_figure(result: dict) -> Figure:
    c = result["contrasts"]
    entry_nose_in = result["groups"]["parking:forward"]["mean_seconds"]
    exit_nose_in = result["groups"]["unparking:reverse"]["mean_seconds"]
    entry_nose_out = result["groups"]["parking:reverse"]["mean_seconds"]
    exit_nose_out = result["groups"]["unparking:forward"]["mean_seconds"]
    totals = [c["nose_in_lifecycle_seconds"], c["nose_out_lifecycle_seconds"]]

    fig = plt.figure(figsize=(12, 7.5))
    _brand_header(
        fig,
        "Observed in the Dragon Lake Parking Dataset",
        "Ten seconds, end to end.",
        "Mean maneuver time across 548 complete events in 30 recordings",
    )
    ax = fig.add_axes((0.19, 0.24, 0.69, 0.48))
    y = [1, 0]
    entries = [entry_nose_in, entry_nose_out]
    exits = [exit_nose_in, exit_nose_out]
    ax.barh(y, entries, height=0.48, color=GOLD, edgecolor="none", label="Entry")
    ax.barh(y, exits, left=entries, height=0.48, color=GREEN, edgecolor="none", label="Exit")
    ax.set_yticks(y, ["Nose-in\nforward entry", "Nose-out\nreverse entry"])
    ax.tick_params(axis="y", labelsize=12, pad=16, length=0)
    ax.set_xlim(0, 52)
    ax.set_xticks([0, 10, 20, 30, 40, 50], ["0", "10", "20", "30", "40", "50 sec"])
    ax.grid(axis="x", color=GRID, linewidth=0.8, alpha=0.65)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)

    for yi, entry, exit_time, total in zip(y, entries, exits, totals):
        ax.text(entry / 2, yi, f"{entry:.1f}s\nentry", ha="center", va="center", color=FOREST, fontsize=10, weight="bold")
        ax.text(entry + exit_time / 2, yi, f"{exit_time:.1f}s\nexit", ha="center", va="center", color=FOREST, fontsize=10, weight="bold")
        ax.text(total + 1.1, yi, f"{total:.1f}s", ha="left", va="center", color=CHALK, fontsize=15, weight="bold")

    ax.set_ylim(-0.62, 1.62)
    ax.annotate(
        "",
        xy=(totals[0], 1.34),
        xytext=(totals[1], 1.34),
        arrowprops={"arrowstyle": "<->", "color": GOLD, "lw": 1.6},
    )
    ax.text(
        sum(totals) / 2,
        1.49,
        "10.0 seconds faster",
        color=GOLD,
        fontsize=12,
        weight="bold",
        ha="center",
    )
    ax.legend(
        loc="lower right",
        bbox_to_anchor=(1, -0.30),
        ncol=2,
        frameon=False,
        labelcolor=CHALK,
        fontsize=10,
    )
    _footer(fig, "Parking dwell time excluded. Lifecycle is an unpaired sum of entry and exit means.")
    return fig


def tradeoff_figure(result: dict) -> Figure:
    groups = result["groups"]
    fig = plt.figure(figsize=(12, 7.5))
    _brand_header(
        fig,
        "The trade-off",
        "Faster in. Slower out. Still ahead overall.",
        "Reverse entry buys a quicker exit—but the observed entry cost was larger",
    )
    axes = [
        fig.add_axes((0.13, 0.21, 0.34, 0.50)),
        fig.add_axes((0.68, 0.21, 0.27, 0.50)),
    ]
    panels = [
        (
            axes[0],
            "PARKING ENTRY",
            [groups["parking:forward"]["mean_seconds"], groups["parking:reverse"]["mean_seconds"]],
            ["Forward / nose-in", "Reverse / nose-out"],
            "21.2s advantage",
            40,
        ),
        (
            axes[1],
            "UNPARKING EXIT",
            [groups["unparking:reverse"]["mean_seconds"], groups["unparking:forward"]["mean_seconds"]],
            ["Reverse from nose-in", "Forward from nose-out"],
            "11.2s disadvantage",
            25,
        ),
    ]
    for ax, title, values, labels, callout, xmax in panels:
        y = [1, 0]
        colors = [GOLD, GREEN]
        ax.barh(y, values, color=colors, height=0.42)
        ax.set_yticks(y, labels)
        ax.tick_params(axis="y", length=0, labelsize=10.5, pad=10)
        ax.set_xlim(0, xmax)
        ax.grid(axis="x", color=GRID, linewidth=0.8, alpha=0.65)
        ax.set_axisbelow(True)
        ax.set_title(title, loc="left", color=MUTED, fontsize=10, weight="bold", pad=18)
        for spine in ax.spines.values():
            spine.set_visible(False)
        for yi, value in zip(y, values):
            ax.text(value - xmax * 0.025, yi, f"{value:.1f}s", va="center", ha="right", color=FOREST, fontsize=12, weight="bold")
        ax.text(0, -0.68, callout, color=GOLD, fontsize=12, weight="bold")
    _footer(fig, "Means shown. Nose-in = forward entry + reverse exit; nose-out = reverse entry + forward exit.")
    return fig


def robustness_figure(result: dict) -> Figure:
    c = result["contrasts"]
    s = result["sensitivity"]
    boot = result["scene_cluster_bootstrap"]
    scenarios = [
        ("Raw estimate", c["nose_in_minus_nose_out_seconds"]),
        ("10% trimmed means", c["trimmed_10_nose_in_minus_nose_out_seconds"]),
        ("False-positive stress", s["adverse_strict_primary_false_positive"]["contrast_seconds"]),
        ("Boundary + false-positive stress", s["adverse_fp_plus_observed_boundary_medians_seconds"]),
        ("Extreme: 1s at every boundary", s["adverse_fp_plus_one_second_each_boundary_seconds"]),
    ]
    fig = plt.figure(figsize=(12, 8))
    _brand_header(
        fig,
        "Robustness",
        "The result holds under the evidence-based stress test.",
        "Negative values favor nose-in; the gold line marks the predeclared two-second practical margin",
    )
    ci_low, ci_high = boot["ci95_seconds"]
    fig.text(
        0.07,
        0.745,
        f"10.0s estimate  ·  {abs(ci_high):.1f}–{abs(ci_low):.1f}s scene-cluster interval  ·  "
        f"{boot['probability_beyond_minus_2_seconds'] * 100:.2f}% beyond the practical margin",
        color=CHALK,
        fontsize=11,
        weight="bold",
    )
    ax = fig.add_axes((0.31, 0.16, 0.61, 0.50))
    ys = list(range(len(scenarios) - 1, -1, -1))
    ax.axvspan(-18, -2, color=GOLD, alpha=0.07)
    ax.axvline(-2, color=GOLD, linewidth=1.6, linestyle="--")
    ax.axvline(0, color=MUTED, linewidth=1.2)
    ax.grid(axis="x", color=GRID, linewidth=0.8, alpha=0.65)
    ax.set_axisbelow(True)
    for y, (label, value) in zip(ys, scenarios):
        color = GOLD if value < -2 else SLATE
        ax.hlines(y, min(value, 0), max(value, 0), color=color, linewidth=2.3, alpha=0.9)
        ax.scatter([value], [y], s=115, color=color, edgecolor=FOREST, linewidth=1.5, zorder=3)
        ax.text(value - 0.35 if value < 0 else value + 0.35, y + 0.18, f"{abs(value):.1f}s", color=CHALK, fontsize=10, weight="bold", ha="right" if value < 0 else "left")
    ax.set_yticks(ys, [label for label, _ in scenarios])
    ax.tick_params(axis="y", length=0, labelsize=10.5, pad=12)
    ax.set_xlim(-18, 3)
    ax.set_xticks([-16, -12, -8, -4, -2, 0, 2], ["16", "12", "8", "4", "2", "equal", "+2"])
    ax.set_xlabel("SECONDS FASTER FOR NOSE-IN  ←", color=MUTED, fontsize=9, weight="bold", labelpad=13)
    for spine in ax.spines.values():
        spine.set_visible(False)

    _footer(fig, "The final scenario is intentionally harsher than observed and falls inside the practical margin.")
    return fig


def share_card_figure(result: dict) -> Figure:
    c = result["contrasts"]
    fig = plt.figure(figsize=(12, 6.3))
    ax = fig.add_axes((0, 0, 1, 1))
    ax.axis("off")
    ax.add_patch(FancyBboxPatch((0.045, 0.07), 0.91, 0.86, boxstyle="round,pad=0.006,rounding_size=0.012", facecolor=EMERALD, edgecolor=GRID, linewidth=1.2))
    fig.text(0.08, 0.84, "FACE FORWARD · OBSERVED EVIDENCE", color=GOLD, fontsize=10, weight="bold")
    fig.text(0.08, 0.66, "10.0", color=GOLD, fontsize=79, weight="bold")
    fig.text(0.42, 0.685, "SECONDS", color=CHALK, fontsize=23, weight="bold")
    fig.text(0.42, 0.61, "faster, end to end", color=CHALK, fontsize=20)
    fig.text(0.08, 0.43, "Nose-in parking averaged 36.2 seconds across entry and exit.", color=CHALK, fontsize=17, weight="bold")
    fig.text(0.08, 0.36, "Reverse-entry / nose-out averaged 46.2 seconds.", color=MUTED, fontsize=14)
    fig.text(0.08, 0.20, "548 complete events · 30 recordings · Dragon Lake Parking Dataset", color=CHALK, fontsize=11)
    fig.text(0.08, 0.135, "Exploratory observed evidence. Parking dwell excluded; lifecycle uses unpaired component means.", color=MUTED, fontsize=8.5)
    return fig


def _save_figure(fig: Figure, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    svg_path = stem.with_suffix(".svg")
    fig.savefig(svg_path, bbox_inches="tight", pad_inches=0.08)
    # Matplotlib emits trailing spaces in multiline SVG path data. They are
    # semantically irrelevant but make repository hygiene checks noisy.
    svg_text = svg_path.read_text(encoding="utf-8")
    svg_path.write_text(
        "\n".join(line.rstrip() for line in svg_text.splitlines()) + "\n",
        encoding="utf-8",
    )
    fig.savefig(stem.with_suffix(".png"), dpi=180, bbox_inches="tight", pad_inches=0.08)


def _write_summary_csv(result: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        ("nose_in_lifecycle_mean_seconds", result["contrasts"]["nose_in_lifecycle_seconds"]),
        ("nose_out_lifecycle_mean_seconds", result["contrasts"]["nose_out_lifecycle_seconds"]),
        ("nose_in_minus_nose_out_seconds", result["contrasts"]["nose_in_minus_nose_out_seconds"]),
        ("bootstrap_ci95_low_seconds", result["scene_cluster_bootstrap"]["ci95_seconds"][0]),
        ("bootstrap_ci95_high_seconds", result["scene_cluster_bootstrap"]["ci95_seconds"][1]),
        ("broad_weighted_precision", result["validation"]["broad_weighted_precision"]),
        ("strict_primary_weighted_precision", result["validation"]["strict_primary_weighted_precision"]),
        ("weighted_boundary_median_seconds", result["validation"]["weighted_boundary_median_seconds"]["overall"]),
    ]
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["metric", "value"])
        writer.writerows(rows)


def generate(result_path: Path, asset_dir: Path, docs_dir: Path) -> list[Path]:
    result = json.loads(result_path.read_text())
    figures = {
        "dlp-lifecycle-comparison": lifecycle_figure(result),
        "dlp-component-tradeoff": tradeoff_figure(result),
        "dlp-robustness": robustness_figure(result),
        "dlp-headline-card": share_card_figure(result),
    }
    outputs: list[Path] = []
    for name, fig in figures.items():
        stem = asset_dir / name
        _save_figure(fig, stem)
        outputs.extend([stem.with_suffix(".svg"), stem.with_suffix(".png")])

    docs_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = docs_dir / "dlp-evidence-charts.pdf"
    with PdfPages(pdf_path) as pdf:
        for fig in figures.values():
            pdf.savefig(fig, bbox_inches="tight", pad_inches=0.08)
    outputs.append(pdf_path)

    csv_path = docs_dir / "dlp-v2-summary.csv"
    _write_summary_csv(result, csv_path)
    outputs.append(csv_path)
    for fig in figures.values():
        plt.close(fig)
    return outputs


def main() -> None:
    repo = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--result", type=Path, default=repo / "dlp" / "results" / "v2-exploratory-analysis.json")
    parser.add_argument("--assets", type=Path, default=repo / "site" / "public" / "assets" / "research")
    parser.add_argument("--docs", type=Path, default=repo / "site" / "public" / "docs")
    args = parser.parse_args()
    outputs = generate(args.result, args.assets, args.docs)
    for output in outputs:
        print(output)


if __name__ == "__main__":
    main()
