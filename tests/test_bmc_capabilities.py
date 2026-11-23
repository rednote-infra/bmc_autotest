import unittest

from func.bmc_capabilities import (
    decide_optional_script,
    discover_manager_capabilities,
    partition_optional_scripts,
)


class FakeClient:
    def __init__(self, documents, forbidden=(), **_kwargs):
        self.documents = documents
        self.forbidden = set(forbidden)
        self.requests = []
        self.closed = False

    def get_raw(self, uri):
        self.requests.append(uri)
        if uri in self.forbidden:
            raise AssertionError(f"discovery must not probe advertised service {uri}")
        return self.documents[uri]

    def close(self):
        self.closed = True


def client_factory(documents, forbidden=()):
    instances = []

    def create(**kwargs):
        client = FakeClient(documents, forbidden, **kwargs)
        instances.append(client)
        return client

    return create, instances


class CapabilityDiscoveryTests(unittest.TestCase):
    def base_documents(self, manager):
        return {
            "/redfish/v1/": {
                "Managers": {"@odata.id": "/redfish/v1/Managers"},
            },
            "/redfish/v1/Managers": {
                "Members": [{"@odata.id": "/redfish/v1/Managers/Self"}],
            },
            "/redfish/v1/Managers/Self": manager,
        }

    def test_discovers_advertised_standard_capability(self):
        documents = self.base_documents({
            "NetworkProtocol": {
                "@odata.id": "/redfish/v1/Managers/Self/NetworkProtocol"
            },
        })
        documents["/redfish/v1/Managers/Self/NetworkProtocol"] = {
            "NTP": {"ProtocolEnabled": False, "Port": 123},
        }
        factory, _instances = client_factory(documents)

        inventory = discover_manager_capabilities(factory, "bmc", "admin", "pwd")

        self.assertTrue(inventory.complete)
        self.assertTrue(inventory.supports_any(["NTP"]))

    def test_discovers_advertised_oem_link(self):
        documents = self.base_documents({
            "Oem": {
                "Vendor": {
                    "SyslogService": {
                        "@odata.id": "/redfish/v1/Managers/Self/SyslogService"
                    }
                }
            }
        })
        factory, _instances = client_factory(documents)

        inventory = discover_manager_capabilities(factory, "bmc", "admin", "pwd")

        self.assertTrue(inventory.supports_any(["SyslogService"]))

    def test_unadvertised_optional_capability_is_skipped(self):
        factory, _instances = client_factory(self.base_documents({"Oem": {}}))
        inventory = discover_manager_capabilities(factory, "bmc", "admin", "pwd")

        decision = decide_optional_script("managers_010_syslog_test", inventory)

        self.assertFalse(decision.run)
        self.assertEqual("UNSUPPORTED", decision.classification)

    def test_advertised_but_broken_capability_is_executed(self):
        service_uri = "/redfish/v1/Managers/Self/SyslogService"
        documents = self.base_documents({
            "Oem": {"Vendor": {"SyslogService": {"@odata.id": service_uri}}}
        })
        factory, instances = client_factory(documents, forbidden=[service_uri])

        inventory = discover_manager_capabilities(factory, "bmc", "admin", "pwd")
        decision = decide_optional_script("managers_010_syslog_test", inventory)

        self.assertTrue(decision.run)
        self.assertEqual("ADVERTISED", decision.classification)
        self.assertNotIn(service_uri, instances[0].requests)

    def test_profile_can_require_unadvertised_capability(self):
        factory, _instances = client_factory(self.base_documents({}))
        inventory = discover_manager_capabilities(factory, "bmc", "admin", "pwd")

        decision = decide_optional_script(
            "managers_018_firewall_check",
            inventory,
            required_capabilities=["FirewallRules"],
        )

        self.assertTrue(decision.run)
        self.assertEqual("REQUIRED", decision.classification)

    def test_incomplete_discovery_does_not_hide_functional_failure(self):
        def broken_factory(**_kwargs):
            raise RuntimeError("service root unavailable")

        inventory = discover_manager_capabilities(
            broken_factory, "bmc", "admin", "pwd"
        )
        decision = decide_optional_script("managers_008_ntp_test", inventory)

        self.assertTrue(decision.run)
        self.assertEqual("UNKNOWN", decision.classification)

    def test_runner_partition_preserves_unsupported_skip_reason(self):
        factory, _instances = client_factory(self.base_documents({}))
        inventory = discover_manager_capabilities(factory, "bmc", "admin", "pwd")

        runnable, skipped = partition_optional_scripts(
            [
                ("managers", "managers_001_info_check"),
                ("managers", "managers_010_syslog_test"),
            ],
            inventory,
        )

        self.assertEqual([("managers", "managers_001_info_check")], runnable)
        self.assertEqual("managers_010_syslog_test", skipped[0][1])
        self.assertIn("UNSUPPORTED", skipped[0][2])


if __name__ == "__main__":
    unittest.main()
