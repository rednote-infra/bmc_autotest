import unittest
from types import SimpleNamespace

from func.bmc_prerequisite import (
    PrerequisiteFailure,
    blocked_findings,
    require_object_fields,
    subscription_member_uris,
    validate_sol_configuration,
)


class PrerequisiteTests(unittest.TestCase):
    def assert_single_root_cause(self, failure):
        findings = blocked_findings(failure)
        roots = [item for item in findings if "root_cause" in item]
        self.assertEqual(1, len(roots))
        self.assertTrue(all(item["result"] == "BLOCKED" for item in findings))
        self.assertEqual(failure.chain, roots[0]["prerequisite_chain"])

    def test_missing_drive_fields_block_dependent_validation(self):
        drive = SimpleNamespace(id="Drive0", model="Example")

        with self.assertRaises(PrerequisiteFailure) as raised:
            require_object_fields(
                drive,
                ("id", "model", "serial_number", "status"),
                "Drive resource discovery",
                ("Drive field validation", "Drive result serialization"),
            )

        self.assertIn("serial_number", raised.exception.reason)
        self.assert_single_root_cause(raised.exception)

    def test_missing_sol_configuration_blocks_capture(self):
        sol_data = SimpleNamespace(SerialSource=None)

        with self.assertRaises(PrerequisiteFailure) as raised:
            validate_sol_configuration(sol_data)

        self.assertIn("Parameters", raised.exception.reason)
        self.assertIn("SOL log capture", raised.exception.dependents)
        self.assert_single_root_cause(raised.exception)

    def test_failed_subscription_discovery_blocks_mutation(self):
        members = [{"@odata.id": "/redfish/v1/EventService/Subscriptions/1"}, {}]

        with self.assertRaises(PrerequisiteFailure) as raised:
            subscription_member_uris(
                members,
                "Event subscription discovery",
                ("Create subscription", "Verify subscription"),
            )

        self.assertIn("member 1", raised.exception.reason)
        self.assert_single_root_cause(raised.exception)

    def test_valid_prerequisites_return_normalized_values(self):
        sol = SimpleNamespace(
            SerialSource="System",
            Parameters={"AllowableValues": ["System", "BMC"]},
        )
        self.assertEqual(
            {"SerialSource": "System", "AllowableValues": ["System", "BMC"]},
            validate_sol_configuration(sol),
        )
        self.assertEqual(
            {"/subscriptions/1"},
            subscription_member_uris(
                [{"@odata.id": "/subscriptions/1"}],
                "Event subscription discovery",
            ),
        )


if __name__ == "__main__":
    unittest.main()
