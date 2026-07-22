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


def compute_validation_metrics(
    truth_rows: Sequence[Mapping[str, object]],
    predicted_rows: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    truth = {str(row["maneuver_id"]): row for row in truth_rows}
    predicted = {str(row["maneuver_id"]): row for row in predicted_rows}
    ids = sorted(truth.keys() & predicted.keys())
    if not ids:
        raise ValueError("truth and predicted rows have no matching maneuver_id")
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
