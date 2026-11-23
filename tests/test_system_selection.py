from types import SimpleNamespace

import pytest

from func.system_selection import (
    SystemSelectionBlocked,
    bind_system_id,
    discover_system,
)


def _system(system_id):
    return SimpleNamespace(
        id=system_id,
        odata_id=f"/redfish/v1/Systems/{system_id}",
    )


class FakeClient:
    def __init__(self, systems):
        self.systems = systems
        self.calls = []

    def get_systems(self):
        return self.systems

    def get_processors(self, system_id=None):
        self.calls.append(("get_processors", system_id))
        return []

    def get_system_log_entries(self, log_id=None, system_id=None):
        self.calls.append(("get_system_log_entries", log_id, system_id))
        return []


def test_single_system_is_selected_without_guessing():
    selected = discover_system(FakeClient([_system("Host")]))

    assert selected.system_id == "Host"
    assert selected.odata_id == "/redfish/v1/Systems/Host"


def test_configured_multi_system_selects_the_requested_resource():
    selected = discover_system(
        FakeClient([_system("Host"), _system("Accelerator")]),
        "/redfish/v1/Systems/Accelerator",
    )

    assert selected.system_id == "Accelerator"


def test_ambiguous_multi_system_is_blocked_instead_of_using_first_member():
    with pytest.raises(SystemSelectionBlocked, match="Multiple ComputerSystem") as exc_info:
        discover_system(FakeClient([_system("Host"), _system("Baseboard")]))

    message = str(exc_info.value)
    assert "Host" in message
    assert "Baseboard" in message


def test_bound_client_passes_selected_id_to_every_scoped_call():
    client = FakeClient([_system("Host"), _system("Accelerator")])
    bind_system_id(client, "Accelerator")

    client.get_processors()
    client.get_system_log_entries("SEL")

    assert client.calls == [
        ("get_processors", "Accelerator"),
        ("get_system_log_entries", "SEL", "Accelerator"),
    ]


def test_configured_unknown_system_is_blocked_with_available_resources():
    with pytest.raises(SystemSelectionBlocked, match="does not match") as exc_info:
        discover_system(FakeClient([_system("Host"), _system("Baseboard")]), "Missing")

    assert "Host" in str(exc_info.value)
    assert "Baseboard" in str(exc_info.value)
