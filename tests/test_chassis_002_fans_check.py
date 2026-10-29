"""Schema-aware validation tests for chassis fan resources."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from bmc.chassis.chassis_002_fans_check import FansInfoCheck
from func.common_function import CommonFunction


def _status(health="OK"):
    return SimpleNamespace(health=health)


def _standalone_fan(*, reading=13800.0, speed_rpm=13800.0):
    return SimpleNamespace(
        id="160",
        name="Fan 160",
        odata_id="/redfish/v1/Chassis/Self/ThermalSubsystem/Fans/160",
        physical_context="Chassis",
        rated_speed_rpm=None,
        speed_percent=SimpleNamespace(
            reading=reading,
            speed_rpm=speed_rpm,
        ),
        status=_status(),
    )


def _legacy_fan():
    return SimpleNamespace(
        member_id="0",
        name="Fan 0",
        odata_id="/redfish/v1/Chassis/Self/Thermal#/Fans/0",
        reading=8000,
        reading_units="RPM",
        max_reading_range=16000,
        physical_context="Chassis",
        min_reading_range=0,
        upper_threshold_critical=None,
        upper_threshold_fatal=None,
        lower_threshold_critical=None,
        status=_status(),
    )


def test_standalone_fan_accepts_usable_rpm_and_ignores_rpm_like_percent(
    monkeypatch,
):
    logs = []
    monkeypatch.setattr(
        CommonFunction,
        "print_log",
        lambda level, message: logs.append((level, message)),
    )
    checker = FansInfoCheck.__new__(FansInfoCheck)

    passed, info = checker._check_single_fan(_standalone_fan())

    assert passed is True
    assert info["schema"] == "standalone"
    assert info["fan_id"] == "160"
    assert info["speed_rpm"] == 13800.0
    assert info["speed_percent"] is None
    assert info["reading_units"] == "RPM"
    assert any(
        level == "WARNING" and "0-100" in message
        for level, message in logs
    )


def test_standalone_fan_records_valid_percent_reading(monkeypatch):
    monkeypatch.setattr(CommonFunction, "print_log", lambda *args: None)
    checker = FansInfoCheck.__new__(FansInfoCheck)

    passed, info = checker._check_single_fan(
        _standalone_fan(reading=62.5, speed_rpm=7500)
    )

    assert passed is True
    assert info["speed_percent"] == 62.5
    assert info["speed_rpm"] == 7500


def test_standalone_fan_requires_a_usable_speed(monkeypatch):
    monkeypatch.setattr(CommonFunction, "print_log", lambda *args: None)
    checker = FansInfoCheck.__new__(FansInfoCheck)

    passed, info = checker._check_single_fan(
        _standalone_fan(reading=None, speed_rpm=None)
    )

    assert passed is False
    assert info["check_result"] == "FAIL"


def test_legacy_fan_keeps_existing_validation(monkeypatch):
    monkeypatch.setattr(CommonFunction, "print_log", lambda *args: None)
    checker = FansInfoCheck.__new__(FansInfoCheck)

    passed, info = checker._check_single_fan(_legacy_fan())

    assert passed is True
    assert info["schema"] == "legacy"
    assert info["member_id"] == "0"
    assert info["reading"] == 8000
    assert info["reading_units"] == "RPM"
