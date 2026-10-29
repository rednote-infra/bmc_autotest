"""Capability discovery for optional Redfish manager interfaces."""

import re
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


OPTIONAL_SCRIPT_CAPABILITIES = {
    "managers_008_ntp_test": ("NtpService",),
    "managers_010_syslog_test": ("SyslogService",),
    "managers_012_kvm_test": ("KVM", "KvmService"),
    "managers_013_lldp_test": ("LldpService",),
    "managers_016_dns_check": ("DnsService",),
    "managers_017_vnc_check": ("RfbService", "VncService"),
    "managers_018_firewall_check": ("FirewallRules",),
    "managers_019_ssl_cert_check": ("SecurityService",),
    "managers_021_sol_check": ("SOLSourceControlInfo",),
    "managers_022_vnc_config_test": ("RfbService", "VncService"),
    "managers_023_sol_log_test": ("SOLSourceControlInfo",),
    "managers_025_syslog_test": ("SyslogService",),
}


def normalize_capability(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.casefold())


@dataclass(frozen=True)
class CapabilityEvidence:
    name: str
    path: str
    uri: Optional[str] = None


@dataclass
class CapabilityInventory:
    complete: bool = True
    evidence: dict = field(default_factory=dict)
    diagnostics: list = field(default_factory=list)

    def add(self, name: str, path: str, uri: Optional[str] = None) -> None:
        key = normalize_capability(name)
        if not key:
            return
        self.evidence.setdefault(key, []).append(
            CapabilityEvidence(name=name, path=path, uri=uri)
        )

    def supports_any(self, names: Iterable[str]) -> bool:
        return any(normalize_capability(name) in self.evidence for name in names)


@dataclass(frozen=True)
class CapabilityDecision:
    run: bool
    classification: str
    reason: str


def _link_uri(value) -> Optional[str]:
    if isinstance(value, dict):
        uri = value.get("@odata.id") or value.get("target")
        return uri if isinstance(uri, str) and uri.startswith("/") else None
    return None


def _collect_advertised(node, inventory: CapabilityInventory, path: str) -> None:
    if isinstance(node, dict):
        for name, value in node.items():
            child_path = f"{path}.{name}" if path else name
            if name.startswith("@odata."):
                continue
            uri = _link_uri(value)
            advertised = (
                uri is not None
                or isinstance(value, dict)
                or isinstance(value, list)
            )
            if advertised:
                inventory.add(name, child_path, uri)
            _collect_advertised(value, inventory, child_path)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            _collect_advertised(item, inventory, f"{path}[{index}]")


def _member_uris(collection: dict) -> list:
    members = collection.get("Members", []) if isinstance(collection, dict) else []
    return [uri for uri in (_link_uri(member) for member in members) if uri]


def discover_manager_capabilities(client_factory: Callable, host: str,
                                  username: str, password: str) -> CapabilityInventory:
    """Discover capabilities without constructing conventional OEM URIs."""
    inventory = CapabilityInventory()
    client = None
    try:
        client = client_factory(host=host, username=username, password=password)
        root = client.get_raw("/redfish/v1/")
        _collect_advertised(root, inventory, "ServiceRoot")
        managers_uri = _link_uri(root.get("Managers", {}))
        if not managers_uri:
            return inventory

        managers = client.get_raw(managers_uri)
        for manager_uri in _member_uris(managers):
            manager = client.get_raw(manager_uri)
            _collect_advertised(manager, inventory, manager_uri)

            network_uri = _link_uri(manager.get("NetworkProtocol", {}))
            if network_uri:
                network = client.get_raw(network_uri)
                _collect_advertised(network, inventory, network_uri)

            ethernet_uri = _link_uri(manager.get("EthernetInterfaces", {}))
            if ethernet_uri:
                ethernet_collection = client.get_raw(ethernet_uri)
                for interface_uri in _member_uris(ethernet_collection):
                    interface = client.get_raw(interface_uri)
                    _collect_advertised(interface, inventory, interface_uri)
    except Exception as exc:
        inventory.complete = False
        inventory.diagnostics.append(f"{type(exc).__name__}: {str(exc)[:200]}")
    finally:
        if client:
            try:
                client.close()
            except Exception:
                pass
    return inventory


def decide_optional_script(script_name: str, inventory: CapabilityInventory,
                           required_capabilities: Iterable[str] = ()) -> CapabilityDecision:
    capabilities = OPTIONAL_SCRIPT_CAPABILITIES.get(script_name)
    if not capabilities:
        return CapabilityDecision(True, "NOT_OPTIONAL", "No optional capability gate")
    if not inventory.complete:
        return CapabilityDecision(
            True,
            "UNKNOWN",
            "Capability discovery was incomplete; execute the test instead of hiding a failure",
        )
    if inventory.supports_any(capabilities):
        return CapabilityDecision(
            True,
            "ADVERTISED",
            f"Advertised capability: {', '.join(capabilities)}",
        )
    required = {normalize_capability(item) for item in required_capabilities}
    if any(normalize_capability(item) in required for item in capabilities):
        return CapabilityDecision(
            True,
            "REQUIRED",
            f"Capability required by test profile: {', '.join(capabilities)}",
        )
    return CapabilityDecision(
        False,
        "UNSUPPORTED",
        f"UNSUPPORTED: BMC does not advertise optional capability "
        f"{', '.join(capabilities)}",
    )


def partition_optional_scripts(scripts, inventory: CapabilityInventory,
                               required_capabilities: Iterable[str] = ()):
    """Partition ``(suite, script)`` pairs into runnable and skipped lists."""
    runnable = []
    skipped = []
    for suite, script in scripts:
        decision = decide_optional_script(
            script,
            inventory,
            required_capabilities,
        )
        if decision.run:
            runnable.append((suite, script))
        else:
            skipped.append((suite, script, decision.reason))
    return runnable, skipped
