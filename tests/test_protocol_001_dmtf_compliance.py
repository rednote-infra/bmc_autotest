import unittest

from bmc.protocol.protocol_001_dmtf_compliance import (
    LEVEL_ERROR,
    Protocol001DmtfCompliance,
    RESULT_BLOCKED,
    RESULT_FAIL,
    RESULT_PASS,
    RESULT_SKIPPED,
)


class _Logger:
    def info(self, *_args, **_kwargs):
        pass

    def warning(self, *_args, **_kwargs):
        pass

    def error(self, *_args, **_kwargs):
        pass


def _case():
    case = Protocol001DmtfCompliance.__new__(Protocol001DmtfCompliance)
    case.findings = []
    case.log = _Logger()
    case.USERNAME = "admin"
    case.PASSWORD = "secret"
    case.systems_uri = ""
    case.chassis_uri = ""
    case.managers_uri = ""
    case.sessions_uri = ""
    case.power_uri = ""
    case.accounts_uri = ""
    case.event_svc_uri = ""
    case._last_evidence = {}
    return case


def _reply(case, method, uri, status, body, headers=None):
    headers = headers or {}
    case._capture_evidence(method, uri, status, body, headers)
    return status, body, headers


class Protocol001ComplianceTests(unittest.TestCase):
    def test_service_root_is_requested_without_authentication(self):
        case = _case()
        calls = []

        def request(method, uri, **kwargs):
            calls.append((method, uri, kwargs))
            return _reply(case, method, uri, 200, {
                "RedfishVersion": "1.20.0",
                "@odata.id": "/redfish/v1",
                "@odata.type": "#ServiceRoot.v1_20_0.ServiceRoot",
            }, {"odata-version": "4.0"})

        case._request = request
        case.check_redfish_version()

        self.assertNotIn("basic_auth", calls[0][2])
        self.assertEqual(RESULT_PASS, case.findings[0]["result"])
        self.assertEqual(200, case.findings[0]["evidence"]["status"])
        self.assertEqual("/redfish/v1", case.findings[0]["evidence"]["uri"])

    def test_authenticated_resources_are_discovered_from_advertised_links(self):
        case = _case()
        calls = []
        root = {
            "Systems": {"@odata.id": "/redfish/v1/Systems"},
            "Chassis": {"@odata.id": "/redfish/v1/Chassis"},
            "Managers": {"@odata.id": "/redfish/v1/Managers"},
            "Links": {"Sessions": {"@odata.id": "/redfish/v1/SessionService/Sessions"}},
        }
        responses = {
            "/redfish/v1": (200, root),
            "/redfish/v1/Systems": (200, {"Members": [{"@odata.id": "/redfish/v1/Systems/Self"}]}),
            "/redfish/v1/Chassis": (200, {"Members": [{"@odata.id": "/redfish/v1/Chassis/HGX"}]}),
            "/redfish/v1/Managers": (200, {"Members": [{"@odata.id": "/redfish/v1/Managers/BMC"}]}),
            "/redfish/v1/Chassis/HGX": (200, {}),
        }

        def request(method, uri, **kwargs):
            calls.append((uri, kwargs))
            status, body = responses[uri]
            return _reply(case, method, uri, status, body)

        case._request = request
        self.assertTrue(case._discover_uris())
        self.assertEqual("/redfish/v1/Systems/Self", case.systems_uri)
        self.assertFalse(any(uri.endswith("/1") for uri, _ in calls))
        member_calls = [kwargs for uri, kwargs in calls if uri in responses and uri != "/redfish/v1"]
        self.assertTrue(all(kwargs.get("basic_auth") == ("admin", "secret")
                            for kwargs in member_calls))
        self.assertNotIn("basic_auth", calls[0][1])

    def test_ambiguous_discovery_is_blocked_instead_of_selecting_first_member(self):
        case = _case()
        root = {
            "Systems": {"@odata.id": "/systems"},
            "Chassis": {"@odata.id": "/chassis"},
            "Managers": {"@odata.id": "/managers"},
            "Links": {"Sessions": {"@odata.id": "/sessions"}},
        }
        responses = {
            "/redfish/v1": (200, root),
            "/systems": (200, {"Members": [
                {"@odata.id": "/systems/HGX_Baseboard_0"},
                {"@odata.id": "/systems/Self"},
            ]}),
            "/chassis": (200, {"Members": [{"@odata.id": "/chassis/Self"}]}),
            "/managers": (200, {"Members": [{"@odata.id": "/managers/Self"}]}),
            "/chassis/Self": (200, {}),
        }

        def request(method, uri, **_kwargs):
            status, body = responses[uri]
            return _reply(case, method, uri, status, body)

        case._request = request
        self.assertFalse(case._discover_uris())
        self.assertEqual("", case.systems_uri)
        self.assertTrue(any(f["result"] == RESULT_BLOCKED for f in case.findings))

    def test_configured_member_must_match_an_advertised_uri(self):
        case = _case()
        case.systems_uri = "/systems/Self"
        root = {
            "Systems": {"@odata.id": "/systems"},
            "Chassis": {"@odata.id": "/chassis"},
            "Managers": {"@odata.id": "/managers"},
            "Links": {"Sessions": {"@odata.id": "/sessions"}},
        }
        responses = {
            "/redfish/v1": (200, root),
            "/systems": (200, {"Members": [
                {"@odata.id": "/systems/HGX_Baseboard_0"},
                {"@odata.id": "/systems/Self"},
            ]}),
            "/chassis": (200, {"Members": [{"@odata.id": "/chassis/Self"}]}),
            "/managers": (200, {"Members": [{"@odata.id": "/managers/Self"}]}),
            "/chassis/Self": (200, {}),
        }

        def request(method, uri, **_kwargs):
            status, body = responses[uri]
            return _reply(case, method, uri, status, body)

        case._request = request
        self.assertTrue(case._discover_uris())
        self.assertEqual("/systems/Self", case.systems_uri)
        self.assertFalse(any(f["result"] == RESULT_BLOCKED for f in case.findings))

    def test_missing_optional_etag_is_skipped_without_patch(self):
        case = _case()
        case.chassis_uri = "/chassis/Self"
        case.systems_uri = "/systems/Self"
        calls = []

        def request(method, uri, **_kwargs):
            calls.append((method, uri))
            return _reply(case, method, uri, 200, {"@odata.id": uri})

        case._request = request
        case.check_etags("token")

        self.assertTrue(case.findings)
        self.assertTrue(all(f["result"] == RESULT_SKIPPED for f in case.findings))
        self.assertFalse(any(method == "PATCH" for method, _ in calls))

    def test_real_service_root_failure_is_fail(self):
        case = _case()

        def request(method, uri, **_kwargs):
            return _reply(case, method, uri, 401, {"error": "Unauthorized"})

        case._request = request
        case.check_redfish_version()
        self.assertEqual(RESULT_FAIL, case.findings[0]["result"])
        self.assertEqual({"error": "Unauthorized"},
                         case.findings[0]["evidence"]["response"])

    def test_summary_has_deterministic_precedence(self):
        scenarios = [
            ([RESULT_PASS], RESULT_PASS),
            ([RESULT_SKIPPED], RESULT_SKIPPED),
            ([RESULT_PASS, RESULT_BLOCKED], RESULT_BLOCKED),
            ([RESULT_BLOCKED, RESULT_FAIL], RESULT_FAIL),
        ]
        for statuses, expected in scenarios:
            with self.subTest(statuses=statuses):
                case = _case()
                case._write_result = lambda *_args: None
                for status in statuses:
                    case._record(status, LEVEL_ERROR, status == RESULT_PASS, status,
                                 result=status, evidence={})
                case._summarize()
                self.assertEqual(expected, case.command_check_result)


if __name__ == "__main__":
    unittest.main()
