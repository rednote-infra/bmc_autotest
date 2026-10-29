import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from bmc import bmc_runner
from func import bmc_account_function as account_safety


class FakeAdminClient:
    def __init__(self, create_fails=False, password_change_required=False):
        self.create_fails = create_fails
        self.password_change_required = password_change_required
        self.accounts = [SimpleNamespace(
            id="1", user_name="admin", role_id="Administrator",
            enabled=True, password_change_required=False,
        )]
        self.mutations = []

    def get_accounts(self):
        return self.accounts

    def add_account(self, account):
        if self.create_fails:
            raise account_safety.RedfishException("create failed")
        created = SimpleNamespace(
            id="9", user_name=account.user_name, role_id=account.role_id,
            enabled=account.enabled,
            password_change_required=self.password_change_required,
        )
        self.accounts.append(created)
        self.mutations.append(("create", account.user_name))
        return created

    def update_account(self, account_id, account):
        target = next(item for item in self.accounts if item.id == account_id)
        if getattr(account, "password_change_required", None) is False:
            target.password_change_required = False
        self.mutations.append(("update", target.user_name))
        return target

    def delete_account(self, account_id):
        target = next(item for item in self.accounts if item.id == account_id)
        self.accounts.remove(target)
        self.mutations.append(("delete", target.user_name))


class FakeGuardClient:
    def __init__(self, host, username, password, usable=True):
        self.host = host
        self.username = username
        self.password = password
        self.usable = usable
        self.closed = False
        self.session = None

    def create_session(self, username, password, switch_to_token_auth=False):
        if not self.usable:
            raise account_safety.RedfishException("login failed")
        self.session = SimpleNamespace(id="session-1", x_auth_token="token")
        return self.session

    def get_accounts(self):
        if not self.usable:
            raise account_safety.RedfishException("forbidden")
        return [SimpleNamespace(
            id="9", user_name=self.username, role_id="Administrator", enabled=True,
        )]

    def delete_session(self, _session_id):
        pass

    def close(self):
        self.closed = True


class AccountSafetyTests(unittest.TestCase):
    def test_guard_creation_failure_returns_no_mutation_permit(self):
        admin = FakeAdminClient(create_fails=True)

        guard = account_safety.setup_guard_account(
            admin, "192.0.2.1", "admin",
            client_factory=lambda *args: FakeGuardClient(*args),
        )

        self.assertIsNone(guard)
        self.assertFalse(any(action == "delete" and user == "admin"
                             for action, user in admin.mutations))

    def test_password_change_required_is_completed_before_guard_is_returned(self):
        admin = FakeAdminClient(password_change_required=True)

        guard = account_safety.setup_guard_account(
            admin, "192.0.2.1", "admin",
            client_factory=lambda *args: FakeGuardClient(*args),
        )

        self.assertIsNotNone(guard)
        self.assertTrue(guard.verified)
        created = next(item for item in admin.accounts
                       if item.user_name == guard.username)
        self.assertFalse(created.password_change_required)
        self.assertIn(("update", guard.username), admin.mutations)

    def test_unusable_guard_is_removed_and_never_authorizes_mutation(self):
        admin = FakeAdminClient()

        guard = account_safety.setup_guard_account(
            admin, "192.0.2.1", "admin",
            client_factory=lambda *args: FakeGuardClient(*args, usable=False),
        )

        self.assertIsNone(guard)
        self.assertNotIn("bmcguardtmp", [item.user_name for item in admin.accounts])

    def test_partially_created_guard_is_removed_when_setup_raises(self):
        admin = FakeAdminClient()

        with patch.object(account_safety, "_complete_password_change",
                          side_effect=RuntimeError("unexpected setup failure")):
            guard = account_safety.setup_guard_account(
                admin, "192.0.2.1", "admin",
                client_factory=lambda *args: FakeGuardClient(*args),
            )

        self.assertIsNone(guard)
        self.assertNotIn("bmcguardtmp", [item.user_name for item in admin.accounts])

    def test_successful_recovery_requires_fresh_authentication(self):
        guard = account_safety.GuardAccount(
            "guard", "guard-password", "192.0.2.1", verified=True
        )
        guard_client = SimpleNamespace(
            add_account=lambda _account: None,
            close=lambda: None,
        )

        with patch.object(account_safety, "RedfishClient",
                          side_effect=[guard_client,
                                       FakeGuardClient("192.0.2.1", "admin", "old")]):
            recovered = account_safety.restore_via_guard(
                guard, "admin", "old", "recreate",
                snapshot=account_safety.AccountSnapshot("admin"),
            )

        self.assertTrue(recovered)

    def test_recovery_authentication_failure_is_reported(self):
        guard = account_safety.GuardAccount(
            "guard", "guard-password", "192.0.2.1", verified=True
        )
        guard_client = SimpleNamespace(
            update_account=lambda *_args: None,
            get_accounts=lambda: [SimpleNamespace(id="1", user_name="admin")],
            close=lambda: None,
        )

        with patch.object(account_safety, "RedfishClient",
                          side_effect=[guard_client,
                                       FakeGuardClient("192.0.2.1", "admin", "old",
                                                       usable=False)]):
            recovered = account_safety.restore_via_guard(
                guard, "admin", "old", "reenable"
            )

        self.assertFalse(recovered)

    def test_destructive_account_suite_runs_last(self):
        root = Path(__file__).parents[1]
        config = json.loads((root / "conf" / "bmc" / "bmc_runner_suites.json")
                            .read_text(encoding="utf-8"))
        destructive = config["suites"]["account_destructive"]

        self.assertEqual("account_destructive",
                         account_safety.DESTRUCTIVE_SUITE_NAME)
        self.assertNotIn("account_005b_delete_admin_negative_test",
                         config["suites"]["account"])
        self.assertEqual([
            "account_005b_delete_admin_negative_test",
            "account_008b_disable_admin_negative_test",
        ], destructive)
        self.assertEqual("account_destructive",
                         bmc_runner.DEFAULT_SUITE_ORDER[-1])
        self.assertEqual("BLOCKED", bmc_runner.EC_LABELS[bmc_runner.EC_BLOCKED])
        self.assertEqual("FAIL",
                         bmc_runner.EC_LABELS[bmc_runner.EC_RECOVERY_FAILED])


if __name__ == "__main__":
    unittest.main()
