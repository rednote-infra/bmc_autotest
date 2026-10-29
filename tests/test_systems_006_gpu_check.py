"""Offline tests for optional GPU power telemetry."""

from redfish_sdk.models.systems import Gpu, GpuOEM

from bmc.systems import systems_006_gpu_check as gpu_check


def _gpu(power_watts):
    return Gpu.model_construct(
        id="GPU0",
        manufacturer="Example",
        model="Accelerator",
        version="1.0",
        power_watts=power_watts,
        oem=GpuOEM.model_construct(serial_number="SERIAL0"),
    )


def test_missing_power_is_warning_without_failing_gpu(monkeypatch):
    checker = object.__new__(gpu_check.GpuInfoCheck)
    messages = []
    monkeypatch.setattr(
        gpu_check.CommonFunction,
        "print_log",
        lambda level, message: messages.append((level, message)),
    )

    passed, info = checker._check_gpu(_gpu(None), 0)

    assert passed is True
    assert info["power_watts"] == ""
    assert info["check_result"] == "PASS"
    assert any(
        level == "WARNING" and "PowerWatts" in message
        for level, message in messages
    )
    assert not any(
        level == "ERROR" and "PowerWatts" in message
        for level, message in messages
    )


def test_advertised_power_remains_an_information_result(monkeypatch):
    checker = object.__new__(gpu_check.GpuInfoCheck)
    messages = []
    monkeypatch.setattr(
        gpu_check.CommonFunction,
        "print_log",
        lambda level, message: messages.append((level, message)),
    )

    passed, info = checker._check_gpu(_gpu("275.5"), 0)

    assert passed is True
    assert info["power_watts"] == "275.5"
    assert any(
        level == "INFO" and "PowerWatts = 275.5" in message
        for level, message in messages
    )
