"""Explicit ComputerSystem discovery and SDK method binding.

The Redfish SDK intentionally refuses to guess when a BMC advertises more
than one ComputerSystem.  This module keeps that safety property while making
the selected resource reusable by every system-scoped test call.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from functools import wraps
from typing import Any, Optional


class SystemSelectionBlocked(RuntimeError):
    """The test cannot run until a ComputerSystem is selected explicitly."""


@dataclass(frozen=True)
class SelectedSystem:
    """The stable identity of one advertised ComputerSystem resource."""

    system_id: str
    odata_id: str


SYSTEM_SCOPED_METHODS = frozenset(
    {
        "get_system",
        "get_processors",
        "get_processor",
        "get_memory",
        "get_memory_device",
        "get_storages",
        "get_volumes",
        "get_gpus",
        "get_bios",
        "get_system_log_services",
        "get_system_log_entries",
        "get_system_log_service",
        "clear_system_log",
        "get_boot_options",
        "get_boot_option",
        "set_boot_option_enabled",
        "get_system_fru",
        "change_boot_source",
        "reset",
        "get_secure_boot",
        "update_secure_boot",
        "reset_secure_boot_keys",
        "get_network_interfaces",
        "get_network_ports",
        "get_network_port",
        "get_memory_domains",
        "get_memory_domain",
        "get_memory_chunks",
        "get_memory_chunk",
        "get_memory_metrics",
        "graphical_console_get_one_time_url",
        "graphical_console_set_one_time_password",
        "graphical_console_set_password",
        "get_hardware_info",
        "get_manufacturer",
    }
)


def _resource_identity(resource: Any) -> tuple[str, str]:
    system_id = str(getattr(resource, "id", "") or "").strip()
    odata_id = str(getattr(resource, "odata_id", "") or "").strip().rstrip("/")
    if not system_id and odata_id:
        system_id = odata_id.rsplit("/", 1)[-1]
    return system_id, odata_id


def _available_text(resources: Iterable[Any]) -> str:
    identities = []
    for resource in resources:
        system_id, odata_id = _resource_identity(resource)
        identities.append(f"{system_id or '<missing Id>'} ({odata_id or '<missing @odata.id>'})")
    return ", ".join(identities) or "none"


def discover_system(
    client: Any,
    requested_id: Optional[str] = None,  # noqa: UP045 -- Python 3.9 support
) -> SelectedSystem:
    """Discover and validate one ComputerSystem without selecting the first member.

    A sole advertised member may be selected automatically.  Multiple members
    require ``requested_id`` to match an advertised Id or ``@odata.id``.
    """

    resources = list(client.get_systems() or [])
    if not resources:
        raise SystemSelectionBlocked("No ComputerSystem resources were advertised by the BMC")

    requested = str(requested_id or "").strip().rstrip("/")
    if not requested:
        if len(resources) != 1:
            raise SystemSelectionBlocked(
                "Multiple ComputerSystem resources were advertised; configure SystemId "
                f"or pass --system-id. Available: {_available_text(resources)}"
            )
        selected = resources[0]
    else:
        matches = []
        for resource in resources:
            system_id, odata_id = _resource_identity(resource)
            if requested in {system_id, odata_id}:
                matches.append(resource)
        if len(matches) != 1:
            reason = "does not match" if not matches else "matches more than one"
            raise SystemSelectionBlocked(
                f"Configured SystemId {requested!r} {reason} advertised ComputerSystem resources. "
                f"Available: {_available_text(resources)}"
            )
        selected = matches[0]

    system_id, odata_id = _resource_identity(selected)
    if not system_id:
        raise SystemSelectionBlocked(
            "Selected ComputerSystem has neither Id nor a usable @odata.id: "
            f"{odata_id or '<missing @odata.id>'}"
        )
    return SelectedSystem(system_id=system_id, odata_id=odata_id)


def bind_system_id(client: Any, system_id: str) -> Any:
    """Inject ``system_id`` into every supported system-scoped SDK call."""

    already_bound = getattr(client, "_bmc_autotest_system_id", None)
    if already_bound:
        if already_bound != system_id:
            raise SystemSelectionBlocked(
                f"SDK client is already bound to ComputerSystem {already_bound!r}, "
                f"not {system_id!r}"
            )
        return client

    for method_name in SYSTEM_SCOPED_METHODS:
        method = getattr(client, method_name, None)
        if not callable(method):
            continue

        @wraps(method)
        def bound(*args, _method=method, _method_name=method_name, **kwargs):
            explicit = kwargs.get("system_id")
            if explicit is not None and explicit != system_id:
                raise SystemSelectionBlocked(
                    f"{_method_name} requested ComputerSystem {explicit!r}, but this test "
                    f"is bound to {system_id!r}"
                )
            kwargs["system_id"] = system_id
            return _method(*args, **kwargs)

        setattr(client, method_name, bound)

    client._bmc_autotest_system_id = system_id
    return client
