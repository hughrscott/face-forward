#!/usr/bin/env python3
"""Reproduce the summary statistics and regression figure for methodology.tex."""
from __future__ import annotations

import csv
import json
import math
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import statsmodels.api as sm
from scipy import stats

ROOT = Path(__file__).resolve().parents[1]
CSV_PATH = ROOT / "simulation_results.csv"
FIGURE_PATH = Path(__file__).resolve().parent / "regression_curves.pdf"
SUMMARY_PATH = Path(__file__).resolve().parent / "statistical_summary.json"


def wilson(successes: int, total: int, alpha: float = 0.05) -> tuple[float, float]:
    z = stats.norm.ppf(1 - alpha / 2)
    p = successes / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    radius = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return centre - radius, centre + radius


def two_proportion_z(a_success: int, a_total: int, b_success: int, b_total: int) -> tuple[float, float]:
    pa, pb = a_success / a_total, b_success / b_total
    pooled = (a_success + b_success) / (a_total + b_total)
    se = math.sqrt(pooled * (1 - pooled) * (1 / a_total + 1 / b_total))
    z = (pa - pb) / se
    return z, 2 * stats.norm.sf(abs(z))


def main() -> None:
    frame = pd.read_csv(CSV_PATH)
    for column in ["suv_present", "los_blocked", "creep_activated", "proximity_warning", "critical_conflict", "collision"]:
        frame[column] = frame[column].astype(bool).astype(int)
    frame["forward"] = (frame["strategy"] == "forward").astype(int)
    frame["density_c"] = frame["ped_density_per_m"] - frame["ped_density_per_m"].mean()
    frame["aisle_c"] = frame["aisle_width_m"] - frame["aisle_width_m"].mean()
    frame["forward_suv"] = frame["forward"] * frame["suv_present"]

    summary: dict[str, object] = {
        "rows": len(frame),
        "seed": int(frame["seed"].iloc[0]),
        "centering_values": {
            "ped_density_per_m": float(frame["ped_density_per_m"].mean()),
            "aisle_width_m": float(frame["aisle_width_m"].mean()),
        },
        "strategies": {},
    }
    for name, group in frame.groupby("strategy"):
        entry: dict[str, object] = {"n": len(group)}
        for metric in ["total_cycle_time_s", "reaction_time_s"]:
            values = group[metric]
            entry[metric] = {
                "mean": float(values.mean()),
                "sd": float(values.std(ddof=1)),
                "ci95": [float(x) for x in stats.t.interval(0.95, len(values) - 1, loc=values.mean(), scale=stats.sem(values))],
            }
        for metric in ["proximity_warning", "critical_conflict", "collision", "los_blocked"]:
            successes = int(group[metric].sum())
            entry[metric] = {
                "count": successes,
                "rate": successes / len(group),
                "ci95": list(wilson(successes, len(group))),
            }
        summary["strategies"][name] = entry

    forward = frame[frame["strategy"] == "forward"]
    reverse = frame[frame["strategy"] == "reverse"]
    welch = stats.ttest_ind(forward["total_cycle_time_s"], reverse["total_cycle_time_s"], equal_var=False)
    mean_difference = forward["total_cycle_time_s"].mean() - reverse["total_cycle_time_s"].mean()
    se_difference = math.sqrt(forward["total_cycle_time_s"].var(ddof=1) / len(forward) + reverse["total_cycle_time_s"].var(ddof=1) / len(reverse))
    df = (forward["total_cycle_time_s"].var(ddof=1) / len(forward) + reverse["total_cycle_time_s"].var(ddof=1) / len(reverse)) ** 2 / (
        (forward["total_cycle_time_s"].var(ddof=1) / len(forward)) ** 2 / (len(forward) - 1)
        + (reverse["total_cycle_time_s"].var(ddof=1) / len(reverse)) ** 2 / (len(reverse) - 1)
    )
    summary["cycle_time_welch"] = {
        "mean_difference_forward_minus_reverse": float(mean_difference),
        "ci95": [float(x) for x in stats.t.interval(0.95, df, loc=mean_difference, scale=se_difference)],
        "t": float(welch.statistic),
        "df": float(df),
        "p": float(welch.pvalue),
    }

    proportion_tests: dict[str, object] = {}
    for metric in ["proximity_warning", "critical_conflict", "collision"]:
        z, p = two_proportion_z(int(forward[metric].sum()), len(forward), int(reverse[metric].sum()), len(reverse))
        proportion_tests[metric] = {"z": z, "p": p}
    summary["two_proportion_tests"] = proportion_tests

    predictors = sm.add_constant(frame[["forward", "density_c", "aisle_c", "suv_present", "forward_suv"]], has_constant="add")
    conflict_model = sm.Logit(frame["critical_conflict"], predictors).fit(disp=False)
    summary["critical_conflict_logit"] = {
        "terms": {
            term: {
                "coefficient": float(conflict_model.params[term]),
                "odds_ratio": float(math.exp(conflict_model.params[term])),
                "p": float(conflict_model.pvalues[term]),
                "ci95_coefficient": [float(x) for x in conflict_model.conf_int().loc[term]],
            }
            for term in conflict_model.params.index
        },
        "n": int(conflict_model.nobs),
        "pseudo_r2": float(conflict_model.prsquared),
    }

    time_model = sm.OLS(frame["total_cycle_time_s"], predictors).fit()
    summary["cycle_time_ols"] = {
        "terms": {
            term: {
                "coefficient": float(time_model.params[term]),
                "p": float(time_model.pvalues[term]),
                "ci95": [float(x) for x in time_model.conf_int().loc[term]],
            }
            for term in time_model.params.index
        },
        "r_squared": float(time_model.rsquared),
        "n": int(time_model.nobs),
    }

    densities = np.linspace(0.05, 0.30, 200)
    palette = {("forward", 0): "#0072B2", ("forward", 1): "#D55E00", ("reverse", 0): "#009E73", ("reverse", 1): "#CC79A7"}
    styles = {0: "--", 1: "-"}
    fig, axis = plt.subplots(figsize=(6.6, 3.8))
    for strategy, is_forward in [("forward", 1), ("reverse", 0)]:
        for suv in [0, 1]:
            design = pd.DataFrame({
                "const": 1.0,
                "forward": is_forward,
                "density_c": densities - frame["ped_density_per_m"].mean(),
                "aisle_c": 6.35 - frame["aisle_width_m"].mean(),
                "suv_present": suv,
                "forward_suv": is_forward * suv,
            })
            prediction = conflict_model.predict(design[conflict_model.params.index])
            axis.plot(densities, prediction, color=palette[(strategy, suv)], linestyle=styles[suv], linewidth=2,
                      label=f"{strategy.capitalize()}, {'SUV adjacent' if suv else 'no SUV'}")
    axis.set_xlabel("Pedestrian density (pedestrians/m)")
    axis.set_ylabel("Predicted critical-conflict probability")
    axis.set_ylim(0, 0.35)
    axis.grid(alpha=0.25)
    axis.legend(frameon=False, fontsize=8, ncol=2)
    fig.tight_layout()
    fig.savefig(FIGURE_PATH)
    plt.close(fig)

    SUMMARY_PATH.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"summary": str(SUMMARY_PATH), "figure": str(FIGURE_PATH), "rows": len(frame)}))


if __name__ == "__main__":
    main()
