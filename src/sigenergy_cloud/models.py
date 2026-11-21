"""Pure value objects for Sigenergy Cloud settings."""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any


def _int_percent(value: Any) -> int | None:
    # The cloud sends "" for thresholds it has no value for (peakShavingSOC).
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    return int(float(value))


def _percent_to_api(value: int | None) -> str:
    return "" if value is None else str(value)


class InstantManualMode(str, Enum):
    """Instant Manual Control action values as labelled by the Sigenergy app."""

    CHARGING = "0"
    DISCHARGING = "1"
    HOLD_BATTERY = "2"
    SELF_CONSUMPTION = "3"


@dataclass(frozen=True, slots=True)
class InstantManualControl:
    """Current Instant Manual Control state."""

    enabled: bool
    mode: InstantManualMode | None
    end_time: int | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "InstantManualControl":
        raw_mode = data.get("mode")
        mode = InstantManualMode(str(raw_mode)) if raw_mode not in (None, "") else None
        raw_end_time = data.get("endTime")
        end_time = int(raw_end_time) if raw_end_time not in (None, "") else None
        return cls(
            enabled=bool(data.get("enable")),
            mode=mode,
            end_time=end_time,
        )


@dataclass(frozen=True, slots=True)
class BatteryLevelSettings:
    """Battery SOC thresholds, all expressed as percentages."""

    charge_soc: int | None
    discharge_soc: int | None
    peak_shaving_soc: int | None
    backup_soc: int | None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "BatteryLevelSettings":
        return cls(
            charge_soc=_int_percent(data.get("chargeSOC")),
            discharge_soc=_int_percent(data.get("dischargeSOC")),
            peak_shaving_soc=_int_percent(data.get("peakShavingSOC")),
            backup_soc=_int_percent(data.get("backupSOC")),
        )

    def to_api(self, station_id: int) -> dict[str, Any]:
        return {
            "stationId": station_id,
            "chargeSOC": _percent_to_api(self.charge_soc),
            "dischargeSOC": _percent_to_api(self.discharge_soc),
            "peakShavingSOC": _percent_to_api(self.peak_shaving_soc),
            "backupSOC": _percent_to_api(self.backup_soc),
        }


@dataclass(frozen=True, slots=True)
class PeakShavingSlot:
    """One demand-limit window in a peak-shaving schedule."""

    index: int
    which_days: tuple[int, ...]
    start_time: str
    end_time: str
    peak_power_kw: float

    @classmethod
    def from_api(cls, index: int, data: dict[str, Any]) -> "PeakShavingSlot":
        days = tuple(int(day) for day in str(data["whichDay"]).split(",") if day)
        return cls(
            index=index,
            which_days=days,
            start_time=str(data["startTime"]),
            end_time=str(data["endTime"]),
            peak_power_kw=float(data["peakPower"]),
        )

    def with_peak_power(self, value: float) -> "PeakShavingSlot":
        """Return a copy with a different power cap."""
        return replace(self, peak_power_kw=value)

    def to_api(self) -> dict[str, Any]:
        return {
            "whichDay": ",".join(str(day) for day in self.which_days),
            "startTime": self.start_time,
            "endTime": self.end_time,
            "peakPower": self.peak_power_kw,
        }


@dataclass(frozen=True, slots=True)
class PeakShavingSchedule:
    """Full station peak-shaving schedule."""

    enabled: bool
    shaving_soc: int
    slots: tuple[PeakShavingSlot, ...] = ()

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "PeakShavingSchedule":
        slots = tuple(
            PeakShavingSlot.from_api(index, slot)
            for index, slot in enumerate(data.get("settingList", ()))
        )
        return cls(
            enabled=data.get("controlMode", 0) == 1,
            shaving_soc=int(data.get("shavingSOC", 0)),
            slots=slots,
        )

    def with_slot(self, slot: PeakShavingSlot) -> "PeakShavingSchedule":
        """Return a copy with one slot replaced by index."""
        if slot.index < 0 or slot.index >= len(self.slots):
            raise ValueError(
                f"Slot index {slot.index} out of range for {len(self.slots)} slots"
            )
        slots = list(self.slots)
        slots[slot.index] = slot
        return replace(self, slots=tuple(slots))

    def to_api(self, station_id: int) -> dict[str, Any]:
        return {
            "stationId": station_id,
            "controlMode": 1 if self.enabled else 0,
            "shavingSOC": self.shaving_soc,
            "settingList": [slot.to_api() for slot in self.slots],
        }
