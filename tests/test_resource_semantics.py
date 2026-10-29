import unittest
import json
from pathlib import Path
from types import SimpleNamespace

from func.resource_semantics import evaluate_sensor_group, select_log_service


def _service(service_id, **kwargs):
    defaults = {
        "id": service_id,
        "name": service_id,
        "description": "",
        "log_purposes": [],
        "log_entry_type": "Event",
        "entries": SimpleNamespace(odata_id=f"/logs/{service_id}/Entries"),
        "syslog_filters": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class LogServiceSemanticTests(unittest.TestCase):
    def test_alternative_identifier_matches_advertised_operations_purpose(self):
        services = [
            _service("EventLog", log_purposes=["Diagnostic"]),
            _service("ManagementHistory", log_purposes=["Operations"]),
        ]

        selected, evidence = select_log_service(services, {
            "purposes": ["Operations"],
            "fallback_keywords": ["operate", "operation"],
        })

        self.assertEqual("ManagementHistory", selected.id)
        self.assertIn("LogPurposes:Operations", evidence["selected"]["matched_by"])

    def test_security_and_operations_can_share_one_combined_service(self):
        combined = _service("UnifiedLog", log_purposes=["Operations", "Security"])

        operation, _ = select_log_service([combined], {"purposes": ["Operations"]})
        audit, _ = select_log_service([combined], {"purposes": ["Security"]})

        self.assertIs(combined, operation)
        self.assertIs(combined, audit)

    def test_configured_event_log_is_valid_operations_fallback(self):
        event_log = _service("EventLog")

        selected, evidence = select_log_service([event_log], {
            "purposes": ["Operations"],
            "preferred_ids": ["OperateLog", "EventLog"],
        })

        self.assertIs(event_log, selected)
        self.assertIn("preferred_id:EventLog", evidence["selected"]["matched_by"])

    def test_audit_event_and_sel_combination_selects_requested_services(self):
        services = [_service("AuditLog"), _service("EventLog"), _service("SEL")]

        operation, _ = select_log_service(services, {
            "purposes": ["Operations"],
            "preferred_ids": ["EventLog"],
        })
        audit, _ = select_log_service(services, {
            "purposes": ["Security"],
            "preferred_ids": ["AuditLog"],
        })

        self.assertEqual("EventLog", operation.id)
        self.assertEqual("AuditLog", audit.id)

    def test_disabled_semantic_match_does_not_hide_enabled_alternative(self):
        disabled = _service("PrimaryOperations", log_purposes=["Operations"],
                            service_enabled=False)
        enabled = _service("EventLog", service_enabled=True)

        selected, evidence = select_log_service([disabled, enabled], {
            "purposes": ["Operations"],
            "preferred_ids": ["EventLog"],
        })

        self.assertIs(enabled, selected)
        self.assertFalse(evidence["candidates"][0]["eligible"])

    def test_advertised_purpose_overrides_identifier_fallback(self):
        diagnostic_event_log = _service(
            "EventLog", log_purposes=["Diagnostic"], service_enabled=True
        )

        selected, evidence = select_log_service([diagnostic_event_log], {
            "purposes": ["Operations"],
            "preferred_ids": ["EventLog"],
            "fallback_keywords": ["event"],
        })

        self.assertIsNone(selected)
        self.assertTrue(evidence["candidates"][0]["purpose_conflict"])

    def test_legacy_service_uses_keyword_fallback_and_preserves_evidence(self):
        legacy = _service("VendorHistory", name="BMC Audit Trail")

        selected, evidence = select_log_service([legacy], {
            "purposes": ["Security"],
            "fallback_keywords": ["audit"],
        })

        self.assertIs(legacy, selected)
        self.assertEqual("VendorHistory", evidence["selected"]["id"])
        self.assertIn("keyword:audit", evidence["selected"]["matched_by"])


class SensorSemanticTests(unittest.TestCase):
    def test_default_profile_marks_platform_optional_sensors_explicitly(self):
        profile_path = (Path(__file__).parents[1] / "conf" / "bmc" / "chassis"
                        / "chassis_009_sensors_check.json")
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
        groups = profile["SensorsInfoCheck"]["RequiredSensorGroups"]

        self.assertEqual("optional", groups["硬盘温度"]["requirement"])
        self.assertEqual("optional", groups["PCIe卡温度"]["requirement"])

    def test_physical_context_matches_before_name_fallback(self):
        sensors = [{
            "name": "TEMP_42",
            "physical_context": "Memory",
            "physical_sub_context": "",
            "related_items": [],
        }]
        result = evaluate_sensor_group(sensors, {
            "requirement": "required",
            "physical_contexts": ["Memory", "MemorySubsystem"],
            "keywords": ["dimm", "memory"],
        })

        self.assertEqual("PASS", result["result"])
        self.assertIn("PhysicalContext:Memory", result["matches"][0]["matched_by"])

    def test_related_item_matches_platform_resource(self):
        sensors = [{
            "name": "TEMP_17",
            "physical_context": "",
            "physical_sub_context": "",
            "related_items": ["/redfish/v1/Systems/Self/Memory/DIMM_A1"],
        }]
        result = evaluate_sensor_group(sensors, {
            "requirement": "when_hardware_present",
            "related_item_keywords": ["/Memory/"],
            "keywords": ["dimm"],
        }, hardware_present=True)

        self.assertEqual("PASS", result["result"])
        self.assertIn("RelatedItem:/Memory/", result["matches"][0]["matched_by"])

    def test_physical_subcontext_is_semantic_evidence(self):
        sensors = [{
            "name": "TEMP_9",
            "physical_context": "PowerSupply",
            "physical_sub_context": "Input",
            "related_items": [],
        }]
        result = evaluate_sensor_group(sensors, {
            "requirement": "required",
            "physical_sub_contexts": ["Input"],
        })

        self.assertEqual("PASS", result["result"])
        self.assertIn("PhysicalSubContext:Input", result["matches"][0]["matched_by"])

    def test_name_is_a_last_resort_fallback(self):
        sensors = [{
            "name": "DIMM A1 Temp",
            "physical_context": "SystemBoard",
            "physical_sub_context": "",
            "related_items": [],
        }]
        result = evaluate_sensor_group(sensors, {
            "requirement": "required",
            "physical_contexts": ["Memory"],
            "keywords": ["dimm"],
        })

        self.assertEqual("PASS", result["result"])
        self.assertEqual(["keyword:dimm"], result["matches"][0]["matched_by"])

    def test_absent_optional_hardware_is_skipped(self):
        result = evaluate_sensor_group([], {
            "requirement": "when_hardware_present",
            "physical_contexts": ["StorageDevice"],
        }, hardware_present=False)

        self.assertEqual("SKIPPED", result["result"])
        self.assertEqual("hardware_absent", result["reason"])

    def test_present_hardware_without_reading_fails(self):
        result = evaluate_sensor_group([], {
            "requirement": "when_hardware_present",
            "physical_contexts": ["Memory"],
        }, hardware_present=True)

        self.assertEqual("FAIL", result["result"])
        self.assertEqual("missing_reading", result["reason"])


if __name__ == "__main__":
    unittest.main()
