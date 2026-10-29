"""Phase-aware state machine for BMC reset recovery."""

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import time
from typing import Callable, Optional


@dataclass(frozen=True)
class ProbeResult:
    """Result of one recovery probe."""

    success: bool
    detail: str


@dataclass
class ResetRecoveryResult:
    """Recorded outcome of every reset/recovery phase."""

    request_accepted: bool = False
    outage_observed: bool = False
    network_recovered: Optional[bool] = None
    redfish_recovered: bool = False
    ipmi_recovered: bool = False
    failed_phase: Optional[str] = None
    events: dict = field(default_factory=dict)
    diagnostics: dict = field(default_factory=dict)
    final_successful_probe: Optional[dict] = None

    @property
    def success(self) -> bool:
        return (
            self.request_accepted
            and self.outage_observed
            and self.network_recovered is not False
            and self.redfish_recovered
            and self.ipmi_recovered
        )

    def to_dict(self) -> dict:
        data = asdict(self)
        data["success"] = self.success
        return data


class ResetRecoveryStateMachine:
    """Track reset request, outage, Redfish recovery, and IPMI recovery."""

    def __init__(
        self,
        *,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        wall_time: Optional[Callable[[], str]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
    ):
        self._monotonic = monotonic
        self._sleep = sleep
        self._wall_time = wall_time or self._utc_now
        self._logger = logger or (lambda _level, _message: None)

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def run(
        self,
        *,
        request_accepted: bool,
        request_detail: str,
        redfish_probe: Callable[[], ProbeResult],
        ipmi_probe: Callable[[], ProbeResult],
        network_probe: Optional[Callable[[], ProbeResult]] = None,
        outage_timeout: float,
        network_timeout: Optional[float] = None,
        redfish_timeout: float,
        ipmi_timeout: float,
        poll_interval: float,
    ) -> ResetRecoveryResult:
        result = ResetRecoveryResult(request_accepted=request_accepted)
        result.diagnostics["reset_request"] = request_detail
        if not request_accepted:
            result.failed_phase = "reset_request"
            self._logger("ERROR", f"Reset request was not accepted: {request_detail}")
            return result

        result.events["request_accepted_at"] = self._wall_time()
        self._logger("INFO", f"Reset request accepted: {request_detail}")

        outage = self._wait_for_outage(
            redfish_probe, outage_timeout, poll_interval, result
        )
        if not outage:
            return result

        if network_probe is not None:
            network = self._wait_for_recovery(
                phase="network_recovery",
                probe=network_probe,
                timeout=network_timeout if network_timeout is not None else redfish_timeout,
                poll_interval=poll_interval,
                result=result,
            )
            result.network_recovered = network
            if not network:
                return result

        redfish = self._wait_for_recovery(
            phase="redfish_recovery",
            probe=redfish_probe,
            timeout=redfish_timeout,
            poll_interval=poll_interval,
            result=result,
        )
        if not redfish:
            return result
        result.redfish_recovered = True

        ipmi = self._wait_for_recovery(
            phase="ipmi_recovery",
            probe=ipmi_probe,
            timeout=ipmi_timeout,
            poll_interval=poll_interval,
            result=result,
        )
        if not ipmi:
            return result
        result.ipmi_recovered = True
        return result

    def _wait_for_outage(self, probe, timeout, poll_interval, result) -> bool:
        phase = "service_outage"
        started = self._monotonic()
        deadline = started + timeout
        attempts = 0
        last_probe = ProbeResult(True, "not probed")
        self._logger("INFO", f"Waiting for Redfish outage (timeout {timeout:g}s)")

        while True:
            attempts += 1
            last_probe = probe()
            now = self._monotonic()
            if not last_probe.success:
                elapsed = now - started
                result.outage_observed = True
                result.events["outage_observed_at"] = self._wall_time()
                result.diagnostics[phase] = {
                    "attempts": attempts,
                    "elapsed_s": elapsed,
                    "final_probe": last_probe.detail,
                }
                self._logger(
                    "INFO",
                    f"Redfish outage observed after {elapsed:.1f}s: {last_probe.detail}",
                )
                return True
            if now >= deadline:
                elapsed = now - started
                result.failed_phase = phase
                result.diagnostics[phase] = {
                    "attempts": attempts,
                    "elapsed_s": elapsed,
                    "final_probe": last_probe.detail,
                }
                self._logger(
                    "ERROR",
                    f"Phase {phase} timed out after {elapsed:.1f}s; "
                    f"no outage observed; final probe: {last_probe.detail}",
                )
                return False
            self._sleep(min(poll_interval, max(0.0, deadline - now)))

    def _wait_for_recovery(
        self, *, phase, probe, timeout, poll_interval, result
    ) -> bool:
        started = self._monotonic()
        deadline = started + timeout
        attempts = 0
        last_probe = ProbeResult(False, "not probed")
        self._logger("INFO", f"Waiting for {phase} (timeout {timeout:g}s)")

        while True:
            attempts += 1
            last_probe = probe()
            now = self._monotonic()
            if last_probe.success:
                elapsed = now - started
                event_name = f"{phase.replace('_recovery', '')}_recovered_at"
                result.events[event_name] = self._wall_time()
                result.diagnostics[phase] = {
                    "attempts": attempts,
                    "elapsed_s": elapsed,
                    "final_probe": last_probe.detail,
                }
                result.final_successful_probe = {
                    "phase": phase,
                    "at": result.events[event_name],
                    "detail": last_probe.detail,
                }
                self._logger(
                    "INFO",
                    f"Phase {phase} recovered after {elapsed:.1f}s: {last_probe.detail}",
                )
                return True
            if now >= deadline:
                elapsed = now - started
                result.failed_phase = phase
                result.diagnostics[phase] = {
                    "attempts": attempts,
                    "elapsed_s": elapsed,
                    "final_probe": last_probe.detail,
                }
                self._logger(
                    "ERROR",
                    f"Phase {phase} timed out after {elapsed:.1f}s; "
                    f"final probe: {last_probe.detail}",
                )
                return False
            self._sleep(min(poll_interval, max(0.0, deadline - now)))
