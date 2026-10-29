"""
BMC Account 操作公共工具函数。

背景：部分厂商 BMC（如 ZTE）删除账号 API 路径用数字 ID，而非 username。
SDK delete_account(username) 会拼成 DELETE /Accounts/{username} 导致 404。
这里封装 delete_account_safe()，先查 id 再删。

Usage:
    from func.bmc_account_function import delete_account_safe, setup_guard_account
    delete_account_safe(client, "test_redfish")
"""

from dataclasses import dataclass, field
import secrets
from func.common_function import CommonFunction
try:
    from redfish_sdk import RedfishClient, RedfishException
    from redfish_sdk.models import Account
except ImportError:  # Allow offline safety tests without device dependencies.
    RedfishClient = None

    class RedfishException(Exception):
        pass

    class Account:
        def __init__(self, **values):
            aliases = {
                "UserName": "user_name", "Password": "password",
                "RoleId": "role_id", "Enabled": "enabled",
                "PasswordChangeRequired": "password_change_required",
            }
            for key, value in values.items():
                setattr(self, aliases.get(key, key), value)


DESTRUCTIVE_SUITE_NAME = "account_destructive"

# ── 角色名归一化（issue/19）───────────────────────────────────────────────────
#
# DMTF DSP0266 标准 RoleId 枚举：Administrator / Operator / ReadOnlyUser（及
# 常见别名 ReadOnly）/ None。"User" 不是标准 RoleId（IPMI 传统 USER 权限级在
# 部分厂商 Redfish 映射时遗留的非标准名），严格按标准实现的 BMC 会拒绝创建。
#
# resolve_role_id() 按优先级归一化：标准名优先，宽松实现回落，保证脚本在
# 严格标准与宽松实现的 BMC 上都能运行。

# 只读角色的候选名（按标准优先级排序）
READONLY_ROLE_CANDIDATES = ["ReadOnly", "ReadOnlyUser", "User"]


def resolve_role_id(client: RedfishClient, preferred: str = "ReadOnly") -> str:
    """探测 BMC 实际支持的 RoleId，归一化返回。

    探测方式：get_roles() 读取 BMC 已定义的角色列表（预定义角色必然存在），
    按以下优先级匹配：
      1. preferred（默认 ReadOnly，标准名）
      2. READONLY_ROLE_CANDIDATES 中的其他只读别名（ReadOnlyUser / User）
      3. 兜底返回 preferred（BMC 角色列表不可读时，交由创建接口实际裁决）

    Returns:
        BMC 实际支持的 RoleId 字符串。
    """
    try:
        roles = client.get_roles()
        supported = {r.id for r in roles if r.id}
    except Exception as e:
        CommonFunction.print_log("WARNING",
            f"resolve_role_id: 读取角色列表失败（{str(e)[:80]}），"
            f"兜底使用 {preferred!r}，由创建接口实际裁决")
        return preferred

    if preferred in supported:
        return preferred
    for candidate in READONLY_ROLE_CANDIDATES:
        if candidate in supported:
            if candidate != preferred:
                CommonFunction.print_log("WARNING",
                    f"resolve_role_id: BMC 未定义标准角色 {preferred!r}，"
                    f"回落到该 BMC 支持的等价角色 {candidate!r}")
            return candidate
    CommonFunction.print_log("WARNING",
        f"resolve_role_id: BMC 角色列表 {sorted(supported)} 中无只读角色"
        f"（{READONLY_ROLE_CANDIDATES}），兜底使用 {preferred!r}")
    return preferred


def update_account_safe(client: RedfishClient, username: str, account) -> bool:
    """通过 username 安全更新账号（自动解析为数字 ID 再 PATCH）。

    Returns:
        True：更新成功
        False：账号不存在
    Raises:
        RedfishException：PATCH 请求失败
    """
    accounts = client.get_accounts()
    found = next((a for a in accounts if a.user_name == username), None)

    if found is None:
        CommonFunction.print_log("WARNING", f"update_account_safe: 账号 {username!r} 不存在，跳过更新")
        return False

    account_id = found.id
    CommonFunction.print_log("INFO", f"update_account_safe: 账号 {username!r} -> id={account_id!r}，执行更新")
    client.update_account(account_id, account)   # 传 id，URL 变为 /Accounts/3
    CommonFunction.print_log("INFO", f"update_account_safe: 账号 {username!r} 更新成功")
    return True

def delete_account_safe(client: RedfishClient, username: str,
                        protected_usernames: list | None = None) -> bool:
    """通过 username 安全删除账号（自动解析为数字 ID 再删）。

    Parameters:
        client: RedfishClient 实例
        username: 待删除的账号用户名
        protected_usernames: 受保护的账号列表，命中则直接抛出 ValueError 拒绝删除。
            默认为 None（无保护）。

    Returns:
        True：删除成功
        False：账号不存在，跳过删除
    Raises:
        ValueError：目标账号在 protected_usernames 中，拒绝执行删除
        RedfishException：删除请求失败（非 404）
    """
    if protected_usernames and username in protected_usernames:
        raise ValueError(
            f"delete_account_safe: 账号 {username!r} 在受保护列表中，禁止删除"
        )

    accounts = client.get_accounts()
    account = next((a for a in accounts if a.user_name == username), None)

    if account is None:
        CommonFunction.print_log("WARNING", f"delete_account_safe: 账号 {username!r} 不存在，跳过删除")
        return False

    account_id = account.id
    CommonFunction.print_log("INFO", f"delete_account_safe: 账号 {username!r} -> id={account_id!r}，执行删除")
    client.delete_account(account_id)   # 传 id（数字字符串），URL 变为 /Accounts/3
    CommonFunction.print_log("INFO", f"delete_account_safe: 账号 {username!r} 删除成功")
    return True


# ── 账号配置快照 ────────────────────────────────────────────────────────────────
#
# 高危反向测试（如删除超管）在执行前先调用 snapshot_account() 备份目标账号的
# 可读字段（UserName、RoleId、Enabled、Locked）。
# 注：Password 永远不在 GET 响应中返回，由调用方自行传入已知密码用于重建。
#
# restore_via_guard("recreate", snapshot=snap) 时优先使用快照中的 RoleId、
# Enabled 值，确保重建后配置与删除前一致。
# ────────────────────────────────────────────────────────────────────────────────

@dataclass
class AccountSnapshot:
    """账号可读配置的快照，用于删除后原样重建。"""
    username: str
    role_id: str  = "Administrator"
    enabled:  bool = True
    locked:   bool = False


def snapshot_account(client: RedfishClient, username: str) -> "AccountSnapshot | None":
    """读取账号当前可读配置并返回快照。

    Parameters:
        client:   RedfishClient 实例
        username: 目标账号用户名

    Returns:
        AccountSnapshot：快照对象；None：账号不存在或读取失败
    """
    try:
        accounts = client.get_accounts()
        acc = next((a for a in accounts if a.user_name == username), None)
        if acc is None:
            CommonFunction.print_log("WARNING",
                f"[Snapshot] 账号 {username!r} 不存在，无法创建快照")
            return None
        snap = AccountSnapshot(
            username=acc.user_name,
            role_id=getattr(acc, "role_id", "Administrator") or "Administrator",
            enabled=getattr(acc, "enabled", True) is True,
            locked=getattr(acc, "locked", False) is True,
        )
        CommonFunction.print_log("INFO",
            f"[Snapshot] 已备份账号 {username!r} 配置："
            f"role={snap.role_id!r} enabled={snap.enabled} locked={snap.locked}")
        return snap
    except RedfishException as e:
        CommonFunction.print_log("WARNING",
            f"[Snapshot] 读取账号 {username!r} 配置失败：{str(e)[:120]}")
        return None
    except Exception as e:
        CommonFunction.print_log("WARNING",
            f"[Snapshot] 读取账号 {username!r} 配置时发生未知异常：{type(e).__name__}: {e}")
        return None


# ── Guard 账号机制 ──────────────────────────────────────────────────────────────
#
# 高危反向测试（删除/禁用/改密超管）在执行前先调用 setup_guard_account()：
#   1. 优先使用已有的冗余超管账号（不创建新账号，不改动 BMC 状态）
#   2. 若无冗余超管，尝试临时创建一个（测试结束后 teardown 删除）
#   3. 若创建也失败（账号数已满等），仅打 WARNING 并返回 None，测试继续
#
# teardown 时调用 teardown_guard_account()：
#   - 若 guard 账号是我们创建的，则删除
#   - 若是预先存在的，则不删除（不改动原始环境）
# ────────────────────────────────────────────────────────────────────────────────

# issue/9：用户名须符合规范（4~16位全英文字母），原 "bmc_guard_tmp" 含下划线，改为纯字母
_GUARD_USERNAME = "bmcguardtmp"


def _new_guard_password() -> str:
    """Generate a unique high-entropy guard password for this run."""
    return f"Gd!{secrets.token_urlsafe(18)}"


@dataclass
class GuardAccount:
    """Guard 账号信息，由 setup_guard_account() 返回。"""
    username: str
    password: str
    bmc_ip: str
    created_by_us: bool = False          # True = 我们创建的，teardown 时需删除
    verified: bool = False
    existing_admins: list = field(default_factory=list)  # 发现时的全量超管列表（不含被测账号）


def _verify_admin_session(bmc_ip: str, username: str, password: str,
                          client_factory=None) -> bool:
    """Verify credentials with a separate token session and an admin read."""
    factory = client_factory or RedfishClient
    if factory is None:
        return False
    verify_client = None
    session = None
    try:
        verify_client = factory(bmc_ip, username, password)
        session = verify_client.create_session(
            username, password, switch_to_token_auth=True
        )
        if not getattr(session, "x_auth_token", None):
            CommonFunction.print_log(
                "ERROR", f"[Guard] Session for {username!r} returned no token"
            )
            return False
        accounts = verify_client.get_accounts()
        account = next((item for item in accounts
                        if getattr(item, "user_name", None) == username), None)
        return bool(
            account
            and getattr(account, "enabled", True) is True
            and str(getattr(account, "role_id", "")).lower()
            in ("administrator", "admin")
        )
    except Exception as exc:
        CommonFunction.print_log(
            "ERROR", f"[Guard] Independent session verification failed: {exc}"
        )
        return False
    finally:
        if verify_client and session and getattr(session, "id", None):
            try:
                verify_client.delete_session(session.id)
            except Exception:
                pass
        if verify_client:
            try:
                verify_client.close()
            except Exception:
                pass


def _complete_password_change(client, username: str, current_password: str) -> str | None:
    """Clear PasswordChangeRequired and return the verified password candidate."""
    accounts = client.get_accounts()
    account = next((item for item in accounts
                    if getattr(item, "user_name", None) == username), None)
    if account is None:
        return None
    if getattr(account, "password_change_required", False) is not True:
        return current_password

    replacement = _new_guard_password()
    updated = update_account_safe(
        client,
        username,
        Account(Password=replacement, PasswordChangeRequired=False),
    )
    if not updated:
        return None
    refreshed = next((item for item in client.get_accounts()
                      if getattr(item, "user_name", None) == username), None)
    if refreshed is None or getattr(refreshed, "password_change_required", False) is True:
        return None
    CommonFunction.print_log(
        "INFO", f"[Guard] Completed PasswordChangeRequired for {username!r}"
    )
    return replacement


def setup_guard_account(client: RedfishClient, bmc_ip: str,
                         protected_username: str,
                         client_factory=None) -> "GuardAccount | None":
    """在高危反向测试前建立 guard 账号（安全网）。

    策略：
      1. 先查账号列表，找已有的冗余超管（enabled=True，非 protected_username）
         → 直接返回，created_by_us=False，teardown 时不删除
      2. 无冗余超管 → 尝试创建临时账号 bmc_guard_tmp
         → 返回，created_by_us=True，teardown 时删除
      3. 创建失败（账号数已满/权限不足）→ WARNING，返回 None

    Parameters:
        client:             RedfishClient 实例（以当前超管身份连接）
        bmc_ip:             BMC IP（teardown 时新建连接用）
        protected_username: 本次测试中将被"攻击"的账号名，需要排除在外

    Returns:
        GuardAccount | None
    """
    try:
        accounts = client.get_accounts()
    except RedfishException as e:
        CommonFunction.print_log("WARNING",
            f"[Guard] 获取账号列表失败，无法建立 guard 账号：{e}")
        return None

    # 收集已有的冗余超管（排除被测账号本身）
    existing_admins = [
        a for a in accounts
        if a.user_name != protected_username
        and getattr(a, "role_id", "").lower() in ("administrator", "admin")
        and getattr(a, "enabled", True) is True
    ]

    if existing_admins:
        CommonFunction.print_log("INFO",
            f"[Guard] 发现冗余超管账号：{[a.user_name for a in existing_admins]}，"
            f"但密码未知，仍需创建临时 guard 账号以确保恢复能力")

    # 无论是否有冗余超管，都创建一个我们掌握密码的临时 guard 账号
    # 这是确保 restore_via_guard() 能真正登录执行恢复的唯一可靠手段
    initial_password = _new_guard_password()
    guard_created = False
    CommonFunction.print_log("INFO",
        f"[Guard] 尝试创建临时 guard 账号 {_GUARD_USERNAME!r}（Administrator 角色）...")
    try:
        # 先清理残留（若上次测试异常退出未删除）
        _cleanup_stale_guard(client)
        client.add_account(Account(
            UserName=_GUARD_USERNAME,
            Password=initial_password,
            RoleId="Administrator",
            Enabled=True,
        ))
        guard_created = True
        verified_password = _complete_password_change(
            client, _GUARD_USERNAME, initial_password
        )
        verified = bool(
            verified_password
            and _verify_admin_session(
                bmc_ip, _GUARD_USERNAME, verified_password, client_factory
            )
        )
        if not verified:
            CommonFunction.print_log(
                "ERROR", "[Guard] Guard account is not independently usable; mutation denied"
            )
            delete_account_safe(client, _GUARD_USERNAME)
            return None
        CommonFunction.print_log("INFO",
            f"[Guard] 临时 guard 账号 {_GUARD_USERNAME!r} 创建成功，"
            f"独立 Session 验证通过，测试结束后将自动删除")
        return GuardAccount(
            username=_GUARD_USERNAME,
            password=verified_password,
            bmc_ip=bmc_ip,
            created_by_us=True,
            verified=True,
            existing_admins=[a.user_name for a in existing_admins],
        )
    except Exception as e:
        if guard_created:
            try:
                delete_account_safe(client, _GUARD_USERNAME)
            except Exception as cleanup_error:
                CommonFunction.print_log(
                    "ERROR",
                    f"[Guard] 无法清理未验证的临时账号：{cleanup_error}",
                )
        # 创建失败（账号数已满等）—— 若有已知冗余超管则降级提示，否则 WARNING
        if existing_admins:
            CommonFunction.print_log("WARNING",
                f"[Guard] 创建临时 guard 账号失败（{str(e)[:80]}），"
                f"账号数可能已满。已有冗余超管 {[a.user_name for a in existing_admins]}，"
                f"但密码未知，若 Admin 被破坏需手动恢复！")
        else:
            CommonFunction.print_log("WARNING",
                f"[Guard] 创建临时 guard 账号失败，且无冗余超管：{str(e)[:120]}\n"
                f"  ⚠ 若本次测试造成 Admin 账号破坏，需手动恢复！")
        return None


def teardown_guard_account(guard: "GuardAccount | None", client: RedfishClient) -> None:
    """高危反向测试结束后，清理 guard 账号（若为我们创建的）。

    Parameters:
        guard:  setup_guard_account() 返回的 GuardAccount，None 时跳过
        client: 任意有效的 RedfishClient（可以是 guard 账号的连接）
    """
    if guard is None or not guard.created_by_us:
        return
    try:
        delete_account_safe(client, _GUARD_USERNAME)
        CommonFunction.print_log("INFO",
            f"[Guard] 临时 guard 账号 {_GUARD_USERNAME!r} 已删除，环境已还原")
    except Exception as e:
        CommonFunction.print_log("WARNING",
            f"[Guard] 删除临时 guard 账号失败（可能已被提前删除）：{e}")


def restore_via_guard(guard: "GuardAccount", broken_username: str,
                      broken_password: str, restore_action: str,
                      **restore_kwargs) -> bool:
    """使用 guard 账号连接 BMC，执行指定的恢复操作。

    Parameters:
        guard:            GuardAccount（must have password，即 created_by_us=True）
        broken_username:  被破坏的账号名（Admin）
        broken_password:  被破坏账号的原始密码（用于重建/重置）
        restore_action:   恢复类型，"recreate" | "reenable" | "reset_password"
        **restore_kwargs: 附加参数（如 role_id, new_password）

    Returns:
        True：恢复成功；False：恢复失败
    """
    if not guard.password or not guard.verified:
        CommonFunction.print_log("WARNING",
            f"[Guard] guard 账号 {guard.username!r} 密码未知，无法登录执行恢复")
        return False

    guard_client = None
    verification_password = broken_password
    try:
        CommonFunction.print_log("INFO",
            f"[Guard] 使用 guard 账号 {guard.username!r} 登录 BMC 执行恢复...")
        guard_client = RedfishClient(guard.bmc_ip, guard.username, guard.password)

        if restore_action == "recreate":
            # 重新创建被删除的账号，优先使用快照中的配置
            snap: "AccountSnapshot | None" = restore_kwargs.get("snapshot")
            if snap:
                role_id = snap.role_id
                enabled = snap.enabled   # 通常为 True，但尊重原始快照
                CommonFunction.print_log("INFO",
                    f"[Guard] 使用账号快照重建：role={role_id!r} enabled={enabled}")
            else:
                role_id = restore_kwargs.get("role_id", "Administrator")
                enabled = True
                CommonFunction.print_log("WARNING",
                    f"[Guard] 无账号快照，以默认值重建：role={role_id!r} enabled={enabled}")
            guard_client.add_account(Account(
                UserName=broken_username,
                Password=broken_password,
                RoleId=role_id,
                Enabled=enabled,
                PasswordChangeRequired=False,
            ))
            CommonFunction.print_log("INFO",
                f"[Guard] 已重新创建账号 {broken_username!r}（role={role_id} enabled={enabled}）")

        elif restore_action == "reenable":
            # 重新启用被禁用的账号
            if not update_account_safe(
                guard_client, broken_username, Account(Enabled=True)
            ):
                return False
            CommonFunction.print_log("INFO",
                f"[Guard] 已重新启用账号 {broken_username!r}")

        elif restore_action == "reset_password":
            # 重置账号密码为原始值
            new_password = restore_kwargs.get("new_password", broken_password)
            verification_password = new_password
            if not update_account_safe(
                guard_client, broken_username, Account(Password=new_password)
            ):
                return False
            CommonFunction.print_log("INFO",
                f"[Guard] 已重置账号 {broken_username!r} 密码")

        else:
            CommonFunction.print_log("WARNING",
                f"[Guard] 未知恢复类型 {restore_action!r}，跳过")
            return False

        if not _verify_admin_session(
            guard.bmc_ip, broken_username, verification_password
        ):
            CommonFunction.print_log(
                "ERROR", f"[Guard] Recovery verification failed for {broken_username!r}"
            )
            return False
        CommonFunction.print_log(
            "INFO", f"[Guard] Recovery verified with fresh credentials for {broken_username!r}"
        )
        return True

    except RedfishException as e:
        CommonFunction.print_log("ERROR",
            f"[Guard] 恢复操作失败（RedfishException）：{str(e)[:120]}")
        return False
    except Exception as e:
        CommonFunction.print_log("ERROR",
            f"[Guard] 恢复操作失败（{type(e).__name__}）：{e}")
        return False
    finally:
        if guard_client:
            try:
                guard_client.close()
            except Exception:
                pass


def _cleanup_stale_guard(client: RedfishClient) -> None:
    """清理上次测试可能残留的 guard 账号（静默执行）。"""
    try:
        accounts = client.get_accounts()
        stale = next((a for a in accounts if a.user_name == _GUARD_USERNAME), None)
        if stale:
            client.delete_account(stale.id)
            CommonFunction.print_log("INFO",
                f"[Guard] 清理残留 guard 账号 {_GUARD_USERNAME!r}")
    except Exception:
        pass  # 静默忽略，不影响主流程
