"""Pure model conversion tests."""

from dataclasses import FrozenInstanceError

import pytest

from sigenergy_cloud import (
    BatteryLevelSettings,
    InstantManualControl,
    InstantManualMode,
    PeakShavingSchedule,
    PeakShavingSlot,
)


def test_battery_level_settings_round_trip() -> None:
    settings = BatteryLevelSettings.from_api(
        {
            "chargeSOC": "99.0",
            "dischargeSOC": "1",
            "peakShavingSOC": "11",
            "backupSOC": "4",
        }
    )

    assert settings == BatteryLevelSettings(
        charge_soc=99,
        discharge_soc=1,
        peak_shaving_soc=11,
        backup_soc=4,
    )
    assert settings.to_api(12345678901234) == {
        "stationId": 12345678901234,
        "chargeSOC": "99",
        "dischargeSOC": "1",
        "peakShavingSOC": "11",
        "backupSOC": "4",
    }



def test_battery_levels_empty_peak_shaving_soc_parses_none() -> None:
    # Live payload (HAR 2026-09-10 grid-settings): Sigen sends "" for peakShavingSOC.
    settings = BatteryLevelSettings.from_api(
        {
            "chargeSOC": "100.0",
            "dischargeSOC": "0.0",
            "backupSOC": "3.0",
            "peakShavingSOC": "",
            "ja12SOC": "0.0",
        }
    )

    assert settings == BatteryLevelSettings(
        charge_soc=100,
        discharge_soc=0,
        peak_shaving_soc=None,
        backup_soc=3,
    )


def test_battery_levels_to_api_sends_empty_string_for_none() -> None:
    settings = BatteryLevelSettings(
        charge_soc=95,
        discharge_soc=0,
        peak_shaving_soc=None,
        backup_soc=3,
    )

    assert settings.to_api(12345678901234) == {
        "stationId": 12345678901234,
        "chargeSOC": "95",
        "dischargeSOC": "0",
        "peakShavingSOC": "",
        "backupSOC": "3",
    }

def test_instant_manual_control_from_api() -> None:
    control = InstantManualControl.from_api(
        {"enable": True, "mode": "1", "endTime": "1776471497"}
    )

    assert control == InstantManualControl(
        enabled=True,
        mode=InstantManualMode.DISCHARGING,
        end_time=1776471497,
    )


def test_disabled_instant_manual_control_from_api() -> None:
    control = InstantManualControl.from_api(
        {"enable": False, "mode": "", "endTime": ""}
    )

    assert control == InstantManualControl(enabled=False, mode=None, end_time=None)


def test_peak_shaving_schedule_is_immutable_and_replaceable() -> None:
    schedule = PeakShavingSchedule.from_api(
        {
            "controlMode": 1,
            "shavingSOC": 10,
            "settingList": [
                {
                    "whichDay": "1,2,3,4,5,6,7",
                    "startTime": "00:00",
                    "endTime": "06:00",
                    "peakPower": "9.5",
                }
            ],
        }
    )

    assert schedule.enabled is True
    assert schedule.slots == (
        PeakShavingSlot(
            index=0,
            which_days=(1, 2, 3, 4, 5, 6, 7),
            start_time="00:00",
            end_time="06:00",
            peak_power_kw=9.5,
        ),
    )

    updated = schedule.with_slot(schedule.slots[0].with_peak_power(7.0))

    assert updated is not schedule
    assert updated.slots[0].peak_power_kw == 7.0
    assert schedule.slots[0].peak_power_kw == 9.5
    with pytest.raises(FrozenInstanceError):
        schedule.enabled = False


def test_peak_shaving_rejects_missing_slot() -> None:
    schedule = PeakShavingSchedule(enabled=True, shaving_soc=10)

    with pytest.raises(ValueError, match="out of range"):
        schedule.with_slot(
            PeakShavingSlot(
                index=0,
                which_days=(1,),
                start_time="00:00",
                end_time="01:00",
                peak_power_kw=1.0,
            )
        )
