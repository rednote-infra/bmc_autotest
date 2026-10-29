import unittest

from func.bmc_reset_state import (
    ProbeResult,
    ResetRecoveryStateMachine,
)


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds

    def wall_time(self):
        return f"T+{self.value:.0f}"


class SequenceProbe:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def __call__(self):
        index = min(self.calls, len(self.results) - 1)
        self.calls += 1
        return self.results[index]


class ResetRecoveryStateMachineTests(unittest.TestCase):
    def machine(self, clock):
        return ResetRecoveryStateMachine(
            monotonic=clock.monotonic,
            sleep=clock.sleep,
            wall_time=clock.wall_time,
        )

    def run_machine(self, redfish_results, ipmi_results, **timeouts):
        clock = FakeClock()
        redfish = SequenceProbe(redfish_results)
        ipmi = SequenceProbe(ipmi_results)
        result = self.machine(clock).run(
            request_accepted=True,
            request_detail="HTTP 204",
            redfish_probe=redfish,
            ipmi_probe=ipmi,
            outage_timeout=timeouts.get("outage_timeout", 3),
            redfish_timeout=timeouts.get("redfish_timeout", 5),
            ipmi_timeout=timeouts.get("ipmi_timeout", 5),
            poll_interval=1,
        )
        return result, redfish, ipmi

    def test_normal_reboot_tracks_every_phase(self):
        result, redfish, ipmi = self.run_machine(
            [
                ProbeResult(True, "HTTP 200 before shutdown"),
                ProbeResult(False, "connection refused"),
                ProbeResult(True, "HTTP 200 Manufacturer=Example"),
            ],
            [ProbeResult(True, "mc info ok")],
        )

        self.assertTrue(result.success)
        self.assertTrue(result.outage_observed)
        self.assertTrue(result.redfish_recovered)
        self.assertTrue(result.ipmi_recovered)
        self.assertEqual(3, redfish.calls)
        self.assertEqual(1, ipmi.calls)
        self.assertEqual("ipmi_recovery", result.final_successful_probe["phase"])

    def test_no_observed_outage_identifies_failed_phase(self):
        result, _redfish, ipmi = self.run_machine(
            [ProbeResult(True, "HTTP 200")],
            [ProbeResult(True, "mc info ok")],
        )

        self.assertFalse(result.success)
        self.assertEqual("service_outage", result.failed_phase)
        self.assertEqual(0, ipmi.calls)

    def test_transient_5xx_is_retried_during_redfish_recovery(self):
        result, redfish, _ipmi = self.run_machine(
            [
                ProbeResult(False, "connection reset"),
                ProbeResult(False, "HTTP 503 Service Unavailable"),
                ProbeResult(False, "HTTP 500 Internal Server Error"),
                ProbeResult(True, "HTTP 200 Manufacturer=Example"),
            ],
            [ProbeResult(True, "mc info ok")],
        )

        self.assertTrue(result.success)
        self.assertEqual(4, redfish.calls)
        self.assertEqual(
            "HTTP 200 Manufacturer=Example",
            result.diagnostics["redfish_recovery"]["final_probe"],
        )

    def test_delayed_success_terminates_recovery_wait(self):
        result, redfish, _ipmi = self.run_machine(
            [
                ProbeResult(False, "connection refused"),
                ProbeResult(False, "HTTP 503"),
                ProbeResult(False, "HTTP 503"),
                ProbeResult(True, "HTTP 200"),
                ProbeResult(False, "must not be called"),
            ],
            [ProbeResult(True, "mc info ok")],
            redfish_timeout=4,
        )

        self.assertTrue(result.success)
        self.assertEqual(4, redfish.calls)

    def test_true_timeout_identifies_redfish_phase_and_last_probe(self):
        result, _redfish, ipmi = self.run_machine(
            [
                ProbeResult(False, "connection refused"),
                ProbeResult(False, "HTTP 503 still booting"),
            ],
            [ProbeResult(True, "must not be called")],
            redfish_timeout=2,
        )

        self.assertFalse(result.success)
        self.assertEqual("redfish_recovery", result.failed_phase)
        self.assertEqual(
            "HTTP 503 still booting",
            result.diagnostics["redfish_recovery"]["final_probe"],
        )
        self.assertEqual(0, ipmi.calls)

    def test_ipmi_timeout_is_distinct_from_redfish_recovery(self):
        result, _redfish, ipmi = self.run_machine(
            [
                ProbeResult(False, "connection refused"),
                ProbeResult(True, "HTTP 200"),
            ],
            [ProbeResult(False, "session timeout")],
            ipmi_timeout=2,
        )

        self.assertFalse(result.success)
        self.assertTrue(result.redfish_recovered)
        self.assertEqual("ipmi_recovery", result.failed_phase)
        self.assertEqual(3, ipmi.calls)

    def test_rejected_request_stops_before_any_probe(self):
        clock = FakeClock()
        redfish = SequenceProbe([ProbeResult(True, "must not be called")])
        ipmi = SequenceProbe([ProbeResult(True, "must not be called")])

        result = self.machine(clock).run(
            request_accepted=False,
            request_detail="HTTP 400",
            redfish_probe=redfish,
            ipmi_probe=ipmi,
            outage_timeout=1,
            redfish_timeout=1,
            ipmi_timeout=1,
            poll_interval=1,
        )

        self.assertEqual("reset_request", result.failed_phase)
        self.assertEqual(0, redfish.calls)
        self.assertEqual(0, ipmi.calls)


if __name__ == "__main__":
    unittest.main()
