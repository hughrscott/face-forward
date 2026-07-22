"""Empirical parking-maneuver data model and inference primitives.

The video adapter is intentionally separated from these deterministic functions so the
scientific labels can be unit-tested without a GPU or camera connection.
"""

from __future__ import annotations

import csv
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Optional, Sequence


MANEUVER_COLUMNS = (
    "timestamp",
    "strategy",
    "entry_duration_s",
    "exit_duration_s",
    "total_cycle_s",
    "los_blocked",
    "suv_adjacent",
    "ped_count",
    "near_miss_ttc_s",
    "conflict_flag",
)


@dataclass(frozen=True)
class KinematicPoint:
    position: tuple[float, float]
    velocity: tuple[float, float]


@dataclass(frozen=True)
class ManeuverRecord:
    timestamp: str
    strategy: str
    entry_duration_s: float
    exit_duration_s: float
    total_cycle_s: float
    los_blocked: bool
    suv_adjacent: bool
    ped_count: int
    near_miss_ttc_s: Optional[float]
    conflict_flag: bool

    def __post_init__(self) -> None:
        if self.strategy not in {"forward", "reverse"}:
            raise ValueError("strategy must be 'forward' or 'reverse'")
        for name in ("entry_duration_s", "exit_duration_s", "total_cycle_s"):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be non-negative")
        if self.ped_count < 0:
            raise ValueError("ped_count must be non-negative")


def _unit(vector: tuple[float, float]) -> tuple[float, float]:
    magnitude = math.hypot(*vector)
    if magnitude <= 1e-12:
        raise ValueError("vector must be non-zero")
    return vector[0] / magnitude, vector[1] / magnitude


def classify_strategy(
    parked_heading: tuple[float, float], stall_inward_vector: tuple[float, float]
) -> str:
    """Classify nose-in (forward) versus back-in (reverse) at parked state."""
    heading = _unit(parked_heading)
    inward = _unit(stall_inward_vector)
    return "forward" if heading[0] * inward[0] + heading[1] * inward[1] >= 0 else "reverse"


def project_point(
    point: tuple[float, float],
    homography: Sequence[Sequence[float]],
) -> tuple[float, float]:
    """Project an image point into calibrated ground-plane metres."""
    if len(homography) != 3 or any(len(row) != 3 for row in homography):
        raise ValueError("homography must be a 3x3 matrix")
    x, y = point
    denominator = homography[2][0] * x + homography[2][1] * y + homography[2][2]
    if abs(denominator) <= 1e-12:
        raise ValueError("point projects to infinity")
    return (
        (homography[0][0] * x + homography[0][1] * y + homography[0][2]) / denominator,
        (homography[1][0] * x + homography[1][1] * y + homography[1][2]) / denominator,
    )


def point_in_polygon(
    point: tuple[float, float], polygon: Sequence[tuple[float, float]]
) -> bool:
    """Ray-casting containment test for a simple ground-plane polygon."""
    if len(polygon) < 3:
        raise ValueError("polygon must contain at least three points")
    x, y = point
    inside = False
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / (y2 - y1) + x1
        ):
            inside = not inside
        previous = current
    return inside


def segment_intersects_bbox(
    start: tuple[float, float],
    end: tuple[float, float],
    bbox: tuple[float, float, float, float],
) -> bool:
    """Liang-Barsky line clipping against an axis-aligned ground-plane box."""
    x_min, y_min, x_max, y_max = bbox
    dx, dy = end[0] - start[0], end[1] - start[1]
    p = (-dx, dx, -dy, dy)
    q = (start[0] - x_min, x_max - start[0], start[1] - y_min, y_max - start[1])
    lower, upper = 0.0, 1.0
    for direction, distance in zip(p, q):
        if abs(direction) <= 1e-12:
            if distance < 0:
                return False
            continue
        ratio = distance / direction
        if direction < 0:
            lower = max(lower, ratio)
        else:
            upper = min(upper, ratio)
        if lower > upper:
            return False
    return True


def pair_ttc(
    vehicle: KinematicPoint,
    pedestrian: KinematicPoint,
    collision_radius_m: float = 0.75,
    horizon_s: float = 10.0,
) -> Optional[float]:
    """Return constant-velocity TTC when closest approach enters the safety radius."""
    if collision_radius_m <= 0 or horizon_s <= 0:
        raise ValueError("collision radius and horizon must be positive")
    relative_position = (
        pedestrian.position[0] - vehicle.position[0],
        pedestrian.position[1] - vehicle.position[1],
    )
    relative_velocity = (
        pedestrian.velocity[0] - vehicle.velocity[0],
        pedestrian.velocity[1] - vehicle.velocity[1],
    )
    velocity_squared = relative_velocity[0] ** 2 + relative_velocity[1] ** 2
    if velocity_squared <= 1e-12:
        return None
    ttc = -(
        relative_position[0] * relative_velocity[0]
        + relative_position[1] * relative_velocity[1]
    ) / velocity_squared
    if ttc < 0 or ttc > horizon_s:
        return None
    closest = (
        relative_position[0] + relative_velocity[0] * ttc,
        relative_position[1] + relative_velocity[1] * ttc,
    )
    return ttc if math.hypot(*closest) <= collision_radius_m else None


class ManeuverAccumulator:
    """State machine for one continuously tracked parking vehicle.

    A complete row requires an aisle→stall→stationary entry followed by a
    stationary→aisle exit. Dwell time is excluded from ``total_cycle_s``.
    """

    def __init__(
        self,
        track_id: int,
        timestamp_origin: str,
        stall_inward_vector: tuple[float, float],
        stationary_threshold_mps: float = 0.2,
        conflict_ttc_s: float = 1.5,
    ) -> None:
        self.track_id = track_id
        self.timestamp_origin = timestamp_origin
        self.stall_inward_vector = _unit(stall_inward_vector)
        self.stationary_threshold_mps = stationary_threshold_mps
        self.conflict_ttc_s = conflict_ttc_s
        self._phase = "waiting"
        self._entry_start: Optional[float] = None
        self._entry_end: Optional[float] = None
        self._exit_start: Optional[float] = None
        self._strategy: Optional[str] = None
        self._los_blocked = False
        self._suv_adjacent = False
        self._pedestrian_ids: set[int] = set()
        self._minimum_ttc: Optional[float] = None

    def observe(
        self,
        timestamp_s: float,
        speed_mps: float,
        in_stall: bool,
        in_aisle: bool,
        heading: tuple[float, float],
        los_blocked: bool,
        suv_adjacent: bool,
        pedestrian_ttcs: Mapping[int, Optional[float]],
    ) -> Optional[ManeuverRecord]:
        if timestamp_s < 0 or speed_mps < 0:
            raise ValueError("timestamp and speed must be non-negative")

        if self._phase == "waiting" and in_aisle:
            self._entry_start = timestamp_s
            self._phase = "entering"

        if self._phase == "entering" and in_stall and speed_mps <= self.stationary_threshold_mps:
            self._entry_end = timestamp_s
            self._strategy = classify_strategy(heading, self.stall_inward_vector)
            self._suv_adjacent = suv_adjacent
            self._phase = "parked"
            return None

        if self._phase == "parked" and in_stall and speed_mps > self.stationary_threshold_mps:
            self._exit_start = timestamp_s
            self._phase = "exiting"

        if self._phase == "exiting":
            self._los_blocked = self._los_blocked or los_blocked
            self._suv_adjacent = self._suv_adjacent or suv_adjacent
            self._pedestrian_ids.update(pedestrian_ttcs)
            valid_ttcs = [value for value in pedestrian_ttcs.values() if value is not None]
            if valid_ttcs:
                candidate = min(valid_ttcs)
                self._minimum_ttc = (
                    candidate if self._minimum_ttc is None else min(self._minimum_ttc, candidate)
                )
            if in_aisle and not in_stall:
                assert self._entry_start is not None
                assert self._entry_end is not None
                assert self._exit_start is not None
                assert self._strategy is not None
                entry_duration = self._entry_end - self._entry_start
                exit_duration = timestamp_s - self._exit_start
                self._phase = "complete"
                return ManeuverRecord(
                    timestamp=self.timestamp_origin,
                    strategy=self._strategy,
                    entry_duration_s=round(entry_duration, 3),
                    exit_duration_s=round(exit_duration, 3),
                    total_cycle_s=round(entry_duration + exit_duration, 3),
                    los_blocked=self._los_blocked,
                    suv_adjacent=self._suv_adjacent,
                    ped_count=len(self._pedestrian_ids),
                    near_miss_ttc_s=(
                        None if self._minimum_ttc is None else round(self._minimum_ttc, 3)
                    ),
                    conflict_flag=(
                        self._minimum_ttc is not None
                        and self._minimum_ttc < self.conflict_ttc_s
                    ),
                )
        return None


def analyze_observation_frames(
    frames: Iterable[Mapping[str, object]],
    stall_inward_vector: tuple[float, float],
) -> list[ManeuverRecord]:
    """Convert normalized detector/tracker frames into completed maneuver rows.

    This is the stable adapter boundary: camera-specific code only needs to emit the
    documented normalized frame schema. Track IDs must remain stable through entry,
    parking, and exit.
    """
    accumulators: dict[int, ManeuverAccumulator] = {}
    last_seen: dict[int, float] = {}
    completed_tracks: set[int] = set()
    records: list[ManeuverRecord] = []
    for frame in frames:
        timestamp_s = float(frame["timestamp_s"])
        expired = [key for key, seen in last_seen.items() if timestamp_s - seen > 300.0]
        for key in expired:
            last_seen.pop(key, None)
            accumulators.pop(key, None)
        timestamp = str(frame["timestamp"])
        vehicles = frame.get("vehicles", [])
        if not isinstance(vehicles, Sequence):
            raise ValueError("frame vehicles must be a sequence")
        for vehicle in vehicles:
            if not isinstance(vehicle, Mapping):
                raise ValueError("vehicle observation must be an object")
            track_id = int(vehicle["track_id"])
            if track_id in completed_tracks:
                continue
            last_seen[track_id] = timestamp_s
            accumulator = accumulators.setdefault(
                track_id,
                ManeuverAccumulator(track_id, timestamp, stall_inward_vector),
            )
            raw_ttcs = vehicle.get("pedestrian_ttcs", {})
            if not isinstance(raw_ttcs, Mapping):
                raise ValueError("pedestrian_ttcs must be an object")
            ttcs = {
                int(key): None if value is None else float(value)
                for key, value in raw_ttcs.items()
            }
            heading_raw = vehicle["heading"]
            if not isinstance(heading_raw, Sequence) or len(heading_raw) != 2:
                raise ValueError("heading must contain two numbers")
            record = accumulator.observe(
                timestamp_s,
                float(vehicle["speed_mps"]),
                bool(vehicle["in_stall"]),
                bool(vehicle["in_aisle"]),
                (float(heading_raw[0]), float(heading_raw[1])),
                bool(vehicle.get("los_blocked", False)),
                bool(vehicle.get("suv_adjacent", False)),
                ttcs,
            )
            if record is not None:
                records.append(record)
                completed_tracks.add(track_id)
    return records


def export_maneuvers_csv(records: Iterable[ManeuverRecord], path: str | Path) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANEUVER_COLUMNS)
        writer.writeheader()
        for record in records:
            row = asdict(record)
            if row["near_miss_ttc_s"] is None:
                row["near_miss_ttc_s"] = ""
            writer.writerow(row)


def generate_comparison_figure(
    empirical_csv: str | Path,
    simulation_csv: str | Path,
    output_path: str | Path,
) -> dict[str, int]:
    """Create the two-panel publication figure required by the journal package."""
    try:
        import matplotlib.pyplot as plt
    except ImportError as exc:  # pragma: no cover - environment-specific error path
        raise RuntimeError("matplotlib is required; install the empirical dependencies") from exc

    empirical = _read_comparison_rows(empirical_csv, "conflict_flag")
    simulation = _read_comparison_rows(simulation_csv, "critical_conflict")
    destination = Path(output_path)
    destination.parent.mkdir(parents=True, exist_ok=True)

    fig, (cdf_axis, conflict_axis) = plt.subplots(1, 2, figsize=(10.5, 4.2))
    colors = {"forward": "#9d1c20", "reverse": "#1f4e5f"}
    for strategy in ("forward", "reverse"):
        for rows, source, linestyle in (
            (empirical, "Empirical", "-"),
            (simulation, "Simulation", "--"),
        ):
            values = sorted(
                float(row["entry_duration_s"])
                for row in rows
                if row["strategy"] == strategy
            )
            if not values:
                raise ValueError(f"{source.lower()} CSV has no {strategy} rows")
            probabilities = [(index + 1) / len(values) for index in range(len(values))]
            cdf_axis.step(
                values,
                probabilities,
                where="post",
                color=colors[strategy],
                linestyle=linestyle,
                linewidth=2,
                label=f"{strategy.title()} — {source}",
            )
    cdf_axis.set_title("A. Entry-time distribution")
    cdf_axis.set_xlabel("Entry duration (s)")
    cdf_axis.set_ylabel("Cumulative probability")
    cdf_axis.set_ylim(0, 1.03)
    cdf_axis.grid(alpha=0.2)
    cdf_axis.legend(frameon=False, fontsize=8)

    positions = [0, 1]
    empirical_rates: list[float] = []
    empirical_errors: list[list[float]] = [[], []]
    simulation_rates: list[float] = []
    for strategy in ("forward", "reverse"):
        empirical_flags = [
            _as_bool(row["conflict_flag"])
            for row in empirical
            if row["strategy"] == strategy
        ]
        simulation_flags = [
            _as_bool(row["critical_conflict"])
            for row in simulation
            if row["strategy"] == strategy
        ]
        rate = sum(empirical_flags) / len(empirical_flags)
        low, high = _wilson_interval(sum(empirical_flags), len(empirical_flags))
        empirical_rates.append(rate)
        empirical_errors[0].append(rate - low)
        empirical_errors[1].append(high - rate)
        simulation_rates.append(sum(simulation_flags) / len(simulation_flags))
    conflict_axis.bar(
        positions,
        empirical_rates,
        yerr=empirical_errors,
        capsize=4,
        width=0.55,
        color=[colors["forward"], colors["reverse"]],
        alpha=0.85,
        label="Empirical (95% CI)",
    )
    conflict_axis.scatter(
        positions,
        simulation_rates,
        marker="D",
        s=50,
        color="black",
        zorder=3,
        label="Simulation",
    )
    conflict_axis.set_xticks(positions, ["Forward", "Reverse"])
    conflict_axis.set_ylabel("Exit conflict rate")
    conflict_axis.set_ylim(0, min(1.0, max(empirical_rates + simulation_rates + [0.1]) * 1.45))
    conflict_axis.set_title("B. Exit conflicts by strategy")
    conflict_axis.grid(axis="y", alpha=0.2)
    conflict_axis.legend(frameon=False, fontsize=8)
    fig.tight_layout()
    fig.savefig(destination, bbox_inches="tight")
    plt.close(fig)
    return {"empirical_rows": len(empirical), "simulation_rows": len(simulation)}


def _read_comparison_rows(path: str | Path, conflict_column: str) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"strategy", "entry_duration_s", conflict_column}
        missing = required - set(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path} missing columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path} has no data rows")
    return rows


def _wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    if trials <= 0:
        raise ValueError("trials must be positive")
    proportion = successes / trials
    denominator = 1 + z**2 / trials
    center = (proportion + z**2 / (2 * trials)) / denominator
    margin = z * math.sqrt(
        proportion * (1 - proportion) / trials + z**2 / (4 * trials**2)
    ) / denominator
    return max(0.0, center - margin), min(1.0, center + margin)


def compute_validation_metrics(
    truth_rows: Sequence[Mapping[str, object]],
    predicted_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    if not truth_rows or not predicted_rows:
        raise ValueError("truth and predicted rows must not be empty")
    id_column = (
        "maneuver_id"
        if "maneuver_id" in truth_rows[0] and "maneuver_id" in predicted_rows[0]
        else "timestamp"
    )
    truth = {str(row[id_column]): row for row in truth_rows}
    predicted = {str(row[id_column]): row for row in predicted_rows}
    ids = sorted(truth.keys() & predicted.keys())
    if not ids:
        raise ValueError(f"truth and predicted rows have no matching {id_column}")
    strategy_correct = sum(truth[key]["strategy"] == predicted[key]["strategy"] for key in ids)
    los_correct = sum(
        _as_bool(truth[key]["los_blocked"]) == _as_bool(predicted[key]["los_blocked"])
        for key in ids
    )
    strategy_accuracy = strategy_correct / len(ids)
    los_accuracy = los_correct / len(ids)
    return {
        "matched": len(ids),
        "strategy_accuracy": strategy_accuracy,
        "los_blocked_accuracy": los_accuracy,
        "strategy_gate_passed": strategy_accuracy > 0.85,
        "los_blocked_gate_passed": los_accuracy > 0.80,
    }


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.lower() in {"true", "false"}:
        return value.lower() == "true"
    raise ValueError(f"not a boolean value: {value!r}")


def rtsp_candidates(vendor: str, host: str, channel: int = 1) -> list[str]:
    """Return credential-free main-stream RTSP URLs for supported NVR vendors."""
    if channel < 1:
        raise ValueError("channel must be at least 1")
    vendor_key = vendor.strip().lower()
    templates = {
        "hikvision": f"rtsp://{host}:554/Streaming/Channels/{channel}01",
        "dahua": f"rtsp://{host}:554/cam/realmonitor?channel={channel}&subtype=0",
        "uniview": f"rtsp://{host}:554/unicast/c{channel}/s0/live",
        "axis": f"rtsp://{host}:554/axis-media/media.amp?camera={channel}",
    }
    if vendor_key not in templates:
        raise ValueError(f"vendor must be one of supported vendors: {', '.join(sorted(templates))}")
    return [templates[vendor_key]]
