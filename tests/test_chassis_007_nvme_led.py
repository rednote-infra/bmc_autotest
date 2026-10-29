"""Offline tests for NVMe drive indicator capability handling."""

import json
from pathlib import Path

import pytest

from redfish_sdk.models.drive import Drive

from bmc.chassis import chassis_007a_nvme_led_positive as positive
from bmc.chassis import chassis_007b_nvme_led_negative as negative

TESTDATA_DIR = Path(__file__).parent.parent / "testdata"


def _drives():
    with (TESTDATA_DIR / "nvme_drive_indicators.json").open(
        encoding="utf-8"
    ) as fixture:
        return {
            name: Drive.model_validate(payload)
            for name, payload in json.load(fixture).items()
        }


@pytest.mark.parametrize("module", [positive, negative])
def test_indicator_capability_prefers_modern_and_supports_legacy(module):
    drives = _drives()

    assert (
        module.advertised_indicator_property(drives["ModernOnly"])
        == module.MODERN_INDICATOR
    )
    assert (
        module.advertised_indicator_property(drives["LegacyOnly"])
        == module.LEGACY_INDICATOR
    )
    assert (
        module.advertised_indicator_property(drives["Both"])
        == module.MODERN_INDICATOR
    )
    assert module.advertised_indicator_property(drives["Unsupported"]) is None


def test_positive_check_reports_bmc_capability_failure(monkeypatch):
    checker = object.__new__(positive.ChassisNVMELEDPositiveCheck)
    checker.client = type("Client", (), {"get_drives": lambda self: [_drives()["Unsupported"]]})()
    checker.LED_STATES = ["Lit", "Off"]
    monkeypatch.setattr(positive.CommonFunction, "print_log", lambda *args, **kwargs: None)

    result, details = checker.nvme_led_positive_check()

    assert result == "FAIL"
    assert details[0]["state"] == "UNSUPPORTED"
    assert "BMC Drive resource" in details[0]["description"]


def test_negative_check_reports_bmc_capability_failure(monkeypatch):
    checker = object.__new__(negative.ChassisNVMELEDNegativeCheck)
    checker.client = type("Client", (), {"get_drives": lambda self: [_drives()["Unsupported"]]})()
    monkeypatch.setattr(negative.CommonFunction, "print_log", lambda *args, **kwargs: None)

    result, details = checker.nvme_led_negative_check()

    assert result == "FAIL"
    assert details[0]["name"] == "bmc_indicator_capability"
    assert details[0]["body"] == "not sent"
    assert "BMC capability failure" in details[0]["description"]
