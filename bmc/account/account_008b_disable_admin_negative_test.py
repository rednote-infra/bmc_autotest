#!/bin/python
"""
Author: Fengmian
Date: 2026/04/24
Usage: python3 bmc/account_008b_disable_admin_negative_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/24: 新增
2026/06/09: 修复 teardown 恢复逻辑 —— 原逻辑在 Admin 被禁用后使用已失效的
            self.client 尝试恢复，若 BMC 立刻使 session 失效则恢复必然失败。
            修复策略：新增 guard 账号安全网机制。
              1. 测试前调用 setup_guard_account() 建立备用超管账号
              2. 若 Admin 被禁用且验证确认（enabled=False 或新建连接失败）：
                 由 guard 账号登录，PATCH Enabled=True 重新启用 Admin
              3. 测试结束后，teardown 删除临时 guard 账号，还原 BMC 账号列表

校验策略（覆盖需求 RDSV_BMC_083 反向部分）：
  尝试通过 Redfish 禁用超级管理员账号，期望 BMC 拒绝
  PASS：BMC 拒绝禁用请求（抛 RedfishException）或账号 enabled 字段仍为 True
  FAIL：超级管理员账号被成功禁用

  注：正向禁用测试见 account_008a_disable_test.py
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.bmc_account_function import (
    update_account_safe,
    setup_guard_account,
    teardown_guard_account,
    restore_via_guard,
)

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.models import Account


class AccountDisableAdminNegativeTest(BmcTestBase):
    """超级管理员不可禁用反向验证

    用例编号：Redfish_Account_008b
    检查项：尝试禁用超管账号，BMC 应拒绝或 enabled 仍为 True

    Guard 安全网说明（2026/06/09）：
        测试前调用 setup_guard_account() 建立备用超管账号。
        若 Admin 被真实禁用，通过 guard 账号执行 restore_via_guard("reenable")
        重新启用 Admin，确保后续测试不受影响。
        注：验证禁用是否生效时，使用新建独立连接而非 self.client（session 可能已失效）。
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/account/account_test.json"),
        )

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

    def _verify_admin_still_enabled(self) -> bool:
        """新建独立连接验证超管账号是否仍处于启用状态。

        Returns:
            True：账号仍存在且 enabled=True（BMC 保护生效）
            False：新建连接失败（账号已被禁用）或 enabled=False
        """
        verify_client = None
        try:
            CommonFunction.print_log("INFO",
                "  Step 2：新建独立连接验证超管账号是否仍处于启用状态...")
            verify_client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)
            accounts = verify_client.get_accounts()
            admin_acc = next((a for a in accounts if a.user_name == self.USERNAME), None)
            if admin_acc is None:
                CommonFunction.print_log("ERROR",
                    f"  ✗ 账号列表中已找不到 {self.USERNAME}（异常情况）")
                return False
            still_enabled = getattr(admin_acc, "enabled", True) is True
            if still_enabled:
                CommonFunction.print_log("INFO",
                    f"  ✓ 独立连接确认：超管账号 {self.USERNAME} 仍处于启用状态（BMC 保护生效）")
            else:
                CommonFunction.print_log("ERROR",
                    f"  ✗ 独立连接确认：超管账号 {self.USERNAME} enabled=False，已被禁用！")
            return still_enabled
        except RedfishException as e:
            # 新建连接登录失败，说明账号已被禁用（凭据无效或被锁）
            CommonFunction.print_log("ERROR",
                f"  ✗ 独立连接登录失败，Admin 可能已被禁用：{str(e)[:120]}")
            return False
        except Exception as e:
            CommonFunction.print_log("ERROR",
                f"  ✗ 独立连接验证时发生未知异常：{type(e).__name__}: {e}")
            return False
        finally:
            if verify_client:
                try:
                    verify_client.close()
                except Exception:
                    pass

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO",
            "测试用例名称：超级管理员不可禁用反向验证，测试用例编号：Redfish_Account_008b")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"
        guard = None

        try:
            # ── 前置：建立 guard 安全网 ────────────────────────────────────────
            CommonFunction.print_log("INFO",
                "前置步骤：建立 guard 账号安全网（防止 Admin 被禁后无法恢复）")
            guard = setup_guard_account(self.client, self.BMC_IP, self.USERNAME)
            if guard is None:
                CommonFunction.print_log("WARNING",
                    "  ✗ guard 账号建立失败（账号数已满或 BMC 权限不足），"
                    "无安全网情况下禁止执行禁用超管操作，本次测试跳过 → WARNING")
                result = "WARNING"
                raise RuntimeError("guard_not_ready")   # 跳至 finally 清理，不执行禁用

            # ── 正式测试 ───────────────────────────────────────────────────────
            CommonFunction.print_log("INFO",
                f"Step 1：尝试禁用超级管理员 {self.USERNAME}（期望 BMC 拒绝）")
            try:
                update_account_safe(
                    self.client,
                    self.USERNAME,
                    Account(Enabled=False)
                )
                # ── 未抛异常：BMC 接受了禁用请求 ──────────────────────────────
                # 不能直接用 self.client 验证（session 可能立刻失效），等 1s 后新建连接确认
                CommonFunction.print_log("WARNING",
                    "  ! update_account_safe 未抛异常，BMC 可能已接受禁用请求，等待 1s 后验证...")
                time.sleep(1)
                admin_still_enabled = self._verify_admin_still_enabled()
                if not admin_still_enabled:
                    CommonFunction.print_log("ERROR",
                        f"  ✗ 超级管理员 {self.USERNAME} 被成功禁用，违反保护规则！→ FAIL")
                    result = "FAIL"
                    # ── guard 恢复：重新启用 Admin ─────────────────────────────
                    if guard and guard.password:
                        CommonFunction.print_log("WARNING",
                            "  开始通过 guard 账号恢复 Admin 启用状态...")
                        recovered = restore_via_guard(
                            guard,
                            broken_username=self.USERNAME,
                            broken_password=self.PASSWORD,
                            restore_action="reenable",
                        )
                        if recovered:
                            CommonFunction.print_log("WARNING",
                                f"  ✓ Admin 账号已通过 guard 重新启用，后续测试不受影响")
                        else:
                            CommonFunction.print_log("ERROR",
                                f"  ✗ guard 恢复失败，Admin 仍处于禁用状态！请人工介入恢复")
                    else:
                        CommonFunction.print_log("ERROR",
                            "  ✗ 无可用 guard 账号（无密码），Admin 仍处于禁用状态！请人工介入")
                else:
                    CommonFunction.print_log("INFO",
                        f"  ✓ 禁用请求虽被 BMC 接受，但账号仍处于启用状态（内部保护生效）→ PASS")

            except RedfishException as e:
                # BMC 直接拒绝禁用（HTTP 403/405 等）→ 符合预期
                CommonFunction.print_log("INFO",
                    f"  ✓ BMC 拒绝禁用超级管理员（符合预期）：{str(e)[:100]}")

        except RuntimeError as e:
            if str(e) == "guard_not_ready":
                pass   # 已在上方记录 WARNING，result 已置 WARNING，正常流程
            else:
                CommonFunction.print_log("ERROR", f"未预期 RuntimeError：{e}")
                traceback.print_exc()
                result = "FAIL"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        finally:
            # ── teardown：清理临时 guard 账号 ─────────────────────────────────
            if guard and guard.created_by_us:
                CommonFunction.print_log("INFO", "teardown：清理临时 guard 账号...")
                _teardown_client = self.client
                try:
                    _teardown_client.get_accounts()   # 探测 session 是否仍有效
                except Exception:
                    try:
                        _teardown_client = RedfishClient(
                            guard.bmc_ip, guard.username, guard.password)
                    except Exception:
                        _teardown_client = None
                teardown_guard_account(guard, _teardown_client)
                try:
                    if _teardown_client and _teardown_client is not self.client:
                        _teardown_client.close()
                except Exception:
                    pass

        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"disable_admin_negative,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "disable_admin_negative", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "超级管理员不可禁用反向验证", "value": result})
        test.print_log("INFO", f"超级管理员不可禁用反向验证完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountDisableAdminNegativeTest("AccountDisableAdminNegativeTest")
        checker.run_test()
        exit_code = 0 if checker.command_check_result in ("PASS", "WARNING") else \
                            2 if checker.command_check_result == "FAIL" else 1
        start_time = time.time()
        while (time.time() - start_time) < 5:
            time.sleep(1)
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "检测到键盘中断，提前终止")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"发生未处理异常: {str(e)}")
        traceback.print_exc()
        exit_code = 1
    finally:
        try: checker.close_sdk_client()
        except Exception: pass
        try:
            with open(checker.exit_code_path, "w", encoding="utf-8") as f:
                f.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
