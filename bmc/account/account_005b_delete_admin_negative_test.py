#!/bin/python
"""
Author: Fengmian
Date: 2026/04/24
Usage: python3 bmc/account_005b_delete_admin_negative_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/24: 新增
2026/04/25: 修复反向验证逻辑缺陷 —— 原逻辑在 BMC 允许删除时因认证失效（HTTP 401）
            导致 get_accounts() 抛出 RedfishException，被误判为"删除被拒"输出 PASS。
            修复策略：
              1. 删除前先通过 get_accounts() 缓存超管账号 ID（与后续请求解耦）
              2. 改用底层 delete 请求并直接捕获 RedfishException 判断是否被拒
              3. 若删除成功（未抛异常）：等待 1s 后用新建连接验证账号是否仍存在
              4. 新建连接若失败（说明 Admin 已被删，401 无法登录）则直接判 FAIL
2026/06/09: 新增 guard 账号安全网机制 —— 在执行删除操作前建立备用超管账号。
            若 BMC 真的接受了删除请求且 Admin 账号消失，由 guard 账号执行恢复。
            策略：
              1. 优先使用账号列表中已有的冗余超管账号（不新建，不改动 BMC 状态）
              2. 无冗余超管时，临时创建 bmc_guard_tmp（Administrator 角色）
              3. 创建失败则 WARNING 提示风险，测试继续（如实反映 BMC 保护能力）
              4. 测试结束后，teardown 删除临时 guard 账号，还原 BMC 账号列表

校验策略（覆盖需求 RDSV_BMC_081 反向部分）：
  尝试通过 Redfish 删除超级管理员账号，期望 BMC 拒绝
  PASS：BMC 拒绝删除请求（抛 RedfishException / HTTP 403/405）
  FAIL：超级管理员账号被成功删除（新建连接无法登录或账号列表中不存在）

  注：正向删除测试见 account_005a_delete_test.py
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.bmc_account_function import (
    delete_account_safe,
    setup_guard_account,
    teardown_guard_account,
    restore_via_guard,
    snapshot_account,
)

from redfish_sdk import RedfishClient, RedfishException


class AccountDeleteAdminNegativeTest(BmcTestBase):
    """超级管理员不可删除反向验证

    用例编号：Redfish_Account_005b
    检查项：尝试删除超管账号，BMC 应拒绝（返回 403/405 或抛 RedfishException）

    修复说明（2026/04/25）：
        原逻辑在 BMC 允许删除时，delete_account_safe() 成功返回后尝试
        self.client.get_accounts()，但此时认证已失效（HTTP 401），SDK 抛出
        RedfishException，被 except 块误判为"删除被拒"，输出 PASS。

        修复后：删除成功（未抛异常）时，等待 1s 后新建独立连接验证账号是否仍在：
        - 新建连接失败（401/网络异常）：说明 Admin 已被删 → FAIL + guard 恢复
        - 新建连接成功但账号不在列表中 → FAIL + guard 恢复
        - 新建连接成功且账号仍在列表中 → PASS（BMC 内部有保护，删除不生效）

    Guard 安全网说明（2026/06/09）：
        测试前调用 setup_guard_account() 建立备用超管账号。
        若 Admin 被真实删除，通过 guard 账号执行 restore_via_guard("recreate")
        将 Admin 账号重建，确保后续测试不受影响。
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

    def _verify_admin_still_exists(self) -> bool:
        """新建独立连接验证超管账号是否仍存在。

        Returns:
            True：账号仍存在（BMC 保护生效）
            False：新建连接失败（认证失效，账号已被删）或账号不在列表中
        """
        verify_client = None
        try:
            CommonFunction.print_log("INFO",
                "  Step 2：新建独立连接验证超管账号是否仍存在...")
            verify_client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)
            accounts = verify_client.get_accounts()
            still_exists = any(a.user_name == self.USERNAME for a in accounts)
            if still_exists:
                CommonFunction.print_log("INFO",
                    f"  ✓ 独立连接确认：超管账号 {self.USERNAME} 仍存在（BMC 保护生效）")
            else:
                CommonFunction.print_log("ERROR",
                    f"  ✗ 独立连接确认：超管账号 {self.USERNAME} 已从账号列表消失！")
            return still_exists
        except RedfishException as e:
            # 新建连接登录失败（401），说明 Admin 已被删，凭据无效
            CommonFunction.print_log("ERROR",
                f"  ✗ 独立连接登录失败（HTTP 401 / 认证异常），Admin 已被删：{str(e)[:120]}")
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
            "测试用例名称：超级管理员不可删除反向验证，测试用例编号：Redfish_Account_005b")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"
        guard = None
        admin_snapshot = None   # 删除前的 Admin 配置快照，用于恢复时原样重建

        try:
            # ── 前置：建立 guard 安全网 ────────────────────────────────────────
            CommonFunction.print_log("INFO",
                "前置步骤：建立 guard 账号安全网（防止 Admin 被删后无法恢复）")
            guard = setup_guard_account(self.client, self.BMC_IP, self.USERNAME)
            if guard is None:
                CommonFunction.print_log("WARNING",
                    "  ✗ guard 账号建立失败（账号数已满或 BMC 权限不足），"
                    "无安全网情况下禁止执行删除超管操作，本次测试跳过 → WARNING")
                result = "WARNING"
                raise RuntimeError("guard_not_ready")   # 跳至 finally 清理，不执行删除

            # ── 前置：备份 Admin 账号配置 ──────────────────────────────────────
            CommonFunction.print_log("INFO",
                f"前置步骤：备份超管账号 {self.USERNAME} 的当前配置（用于删除后原样恢复）")
            admin_snapshot = snapshot_account(self.client, self.USERNAME)
            if admin_snapshot is None:
                CommonFunction.print_log("WARNING",
                    "  ⚠ 账号配置备份失败，恢复时将使用默认值（role=Administrator enabled=True）")

            # ── 正式测试 ───────────────────────────────────────────────────────
            CommonFunction.print_log("INFO",
                f"Step 1：尝试删除超级管理员 {self.USERNAME}（期望 BMC 拒绝）")
            try:
                delete_account_safe(self.client, self.USERNAME)
                # ── 未抛异常：BMC 接受了删除请求 ──────────────────────────────
                # 不能直接用 self.client 验证（可能 401 失效），等 1s 后新建连接确认
                CommonFunction.print_log("WARNING",
                    f"  ! delete_account_safe 未抛异常，BMC 可能已接受删除请求，等待 1s 后验证...")
                time.sleep(1)
                admin_still_exists = self._verify_admin_still_exists()
                if not admin_still_exists:
                    CommonFunction.print_log("ERROR",
                        f"  ✗ 超级管理员 {self.USERNAME} 被成功删除，违反保护规则！→ FAIL")
                    result = "FAIL"
                    # ── guard 恢复：按快照重建 Admin 账号 ──────────────────────
                    if guard and guard.password:
                        CommonFunction.print_log("WARNING",
                            "  开始通过 guard 账号按快照配置恢复 Admin...")
                        recovered = restore_via_guard(
                            guard,
                            broken_username=self.USERNAME,
                            broken_password=self.PASSWORD,
                            restore_action="recreate",
                            snapshot=admin_snapshot,   # 传入快照，None 时自动降级默认值
                        )
                        if recovered:
                            CommonFunction.print_log("WARNING",
                                f"  ✓ Admin 账号已通过 guard 恢复，后续测试不受影响")
                        else:
                            CommonFunction.print_log("ERROR",
                                f"  ✗ guard 恢复失败，Admin 账号丢失！请人工介入恢复 BMC 环境")
                    else:
                        CommonFunction.print_log("ERROR",
                            "  ✗ 无可用 guard 账号（无密码），Admin 账号丢失！请人工介入恢复")
                else:
                    CommonFunction.print_log("INFO",
                        f"  ✓ 删除请求虽被 BMC 接受，但账号仍存在（内部保护生效）→ PASS")

            except RedfishException as e:
                # BMC 直接拒绝删除（HTTP 403/405 等）→ 符合预期
                CommonFunction.print_log("INFO",
                    f"  ✓ BMC 拒绝删除超级管理员（符合预期，HTTP 错误）：{str(e)[:120]}")

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
                # guard 账号可能是我们在 BMC 上建的，需用一个有效 session 删它
                # 若 self.client 失效，尝试用 guard 自己的连接删自己
                _teardown_client = self.client
                try:
                    _teardown_client.get_accounts()   # 探测 session 是否仍有效
                except Exception:
                    # self.client 已失效，改用 guard 自己的连接
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
            f.write(f"delete_admin_negative,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "delete_admin_negative", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "超级管理员不可删除反向验证", "value": result})
        test.print_log("INFO", f"超级管理员不可删除反向验证完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountDeleteAdminNegativeTest("AccountDeleteAdminNegativeTest")
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
