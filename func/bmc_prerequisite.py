"""Helpers for stopping dependent test phases after prerequisite failures."""

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass
class PrerequisiteFailure(Exception):
    """A known prerequisite failure with its dependent phase chain."""

    prerequisite: str
    reason: str
    dependents: tuple = ()

    def __post_init__(self):
        super().__init__(f"{self.prerequisite}: {self.reason}")

    @property
    def chain(self) -> list:
        return [self.prerequisite, *self.dependents]

    def to_dict(self) -> dict:
        return {
            "prerequisite": self.prerequisite,
            "root_cause": self.reason,
            "prerequisite_chain": self.chain,
        }


def require_object_fields(obj, fields: Iterable[str], prerequisite: str,
                          dependents: Iterable[str] = ()) -> None:
    """Require attributes to exist before later validation accesses them."""
    missing = [field for field in fields if not hasattr(obj, field)]
    if missing:
        raise PrerequisiteFailure(
            prerequisite,
            f"discovered resource is missing fields: {', '.join(missing)}",
            tuple(dependents),
        )


def validate_sol_configuration(sol_data) -> dict:
    """Return normalized SOL configuration or raise one prerequisite failure."""
    serial_source = getattr(sol_data, "SerialSource", None)
    parameters = getattr(sol_data, "Parameters", None)
    missing = []
    if not isinstance(serial_source, str) or not serial_source.strip():
        missing.append("SerialSource")
    if not isinstance(parameters, dict):
        missing.append("Parameters")
    if missing:
        raise PrerequisiteFailure(
            "SOL configuration discovery",
            f"SOLSourceControlInfo is missing valid fields: {', '.join(missing)}",
            ("SOL session activation", "SOL log capture", "SOL deactivation"),
        )
    return {
        "SerialSource": serial_source,
        "AllowableValues": parameters.get("AllowableValues", []),
    }


def subscription_member_uris(members, prerequisite: str,
                             dependents: Iterable[str] = ()) -> set:
    """Validate subscription collection members before URI-dependent phases."""
    if not isinstance(members, list):
        raise PrerequisiteFailure(
            prerequisite,
            f"subscription members must be a list, got {type(members).__name__}",
            tuple(dependents),
        )
    uris = set()
    for index, member in enumerate(members):
        if not isinstance(member, dict):
            raise PrerequisiteFailure(
                prerequisite,
                f"subscription member {index} must be an object",
                tuple(dependents),
            )
        uri = member.get("@odata.id")
        if not isinstance(uri, str) or not uri.strip():
            raise PrerequisiteFailure(
                prerequisite,
                f"subscription member {index} has no @odata.id",
                tuple(dependents),
            )
        uris.add(uri)
    return uris


def blocked_findings(failure: PrerequisiteFailure) -> list:
    """Build one root-cause finding followed by dependent BLOCKED findings."""
    findings = [{
        "item": failure.prerequisite,
        "result": "BLOCKED",
        "detail": failure.reason,
        "root_cause": failure.reason,
        "prerequisite_chain": failure.chain,
    }]
    for dependent in failure.dependents:
        findings.append({
            "item": dependent,
            "result": "BLOCKED",
            "detail": f"Blocked by prerequisite: {failure.prerequisite}",
            "prerequisite_chain": failure.chain,
        })
    return findings
