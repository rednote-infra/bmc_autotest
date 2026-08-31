#!/bin/python
"""
Author: Fengmian
Date: 2026/04/24
Usage: python3 bmc/account_003c_password_negative_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/24: 新增
2026/06/09: 新增 guard 账号安全网机制 —— 仅作预防性告警。
            本脚本只修改测试账号（非 Admin）的密码，Admin session 不受影响。
            guard 账号的作用：若未来代码变更意外导致 Admin 密码被改，可快速定位
            并通过 guard 账号恢复。当前版本不触发 restore 逻辑（仅记录安全网状态）。

校验策略（覆盖需求 RDSV_BMC_082 密码修改——反向部分）：
  密码规范：大小写字母 + 特殊字符 + 数字，长度 ≥8 位

  负向边界值：
    - 7 位过短：Ab@1234                     → 必须拦截，否则 FAIL
    - 纯数字（无大写/特殊字符）：12345678        → 必须拦截，否则 FAIL
    - 纯小写+数字（缺大写/特殊字符）：abcde123   → 必须拦截，否则 FAIL
    - 13 位（超文档建议上限12位）：Ab@1234567890x
        BMC接受 → PASS（位数更高=更安全）
        BMC拒绝 → 也 PASS（严格合规）

  PASS：必须拦截的密码均被 BMC 拒绝；13位不影响结果
  FAIL：7位过短 / 复杂度不足的密码被 BMC 接受且可登录

  注：密码正向边界值验证见 account_003b_password_modify_test.py
  finally 回滚：删除测试账号
"""

import os
import time
import sys
import traceback
import random as _random

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.bmc_account_function import (
    delete_account_safe,
    update_account_safe,
    setup_guard_account,
    teardown_guard_account,
)

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.models import Account

# 必须被拦截的密码（接受且可登录 → FAIL）
INVALID_PASSWORDS = [
    ("Ab@1234",  "7位过短"),
    ("12345678", "纯数字，缺少大写字母和特殊字符"),
    ("abcde123", "纯小写+数字，缺少大写字母和特殊字符"),
]

# 超长密码（>12位）：BMC接受或拒绝均为 PASS（位数更高=更安全）
OVERLENGTH_PASSWORDS = [
    ("Ab@1234567890x", "13位（超文档建议上限，位数更高更安全，PASS）"),
]

class AccountPasswordNegativeTest(BmcTestBase):
    """Account 密码修改反向测试

    用例编号：Redfish_Account_003c
    检查项：非法密码（长度/复杂度不符）应被 BMC 拒绝
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/account/account_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        ta = conf_section["TestAccount"]
        self.TEST_ACCOUNT_USER = ta["UserName"] + str(_random.randint(10, 99))
        self.TEST_ACCOUNT_PASS = ta["Password"]
        self.test_username    = None  # 预初始化，run_test 中实际赋值
        self.current_password = None  # 预初始化，run_test 中实际赋值

    def close_sdk_client(self):
        if self.client:
            try: self.client.close()
            except Exception: pass

    def _cleanup(self):
        if self.test_username and self.client:
            try:
                delete_account_safe(self.client, self.test_username)
            except Exception as e:
                CommonFunction.print_log("WARNING", f"[清理] 删除测试账号失败（可忽略）：{e}")

    def _try_login(self, password: str) -> bool:
        try:
            c = RedfishClient(self.BMC_IP, self.test_username, password)
            c.get_accounts()
            try: c.close()
            except Exception: pass
            return True
        except RedfishException:
            return False

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", "测试用例名称：Account密码修改反向测试，测试用例编号：Redfish_Account_003c")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"
        guard = None

        try:
            # ── 前置：建立 guard 安全网（预防性，本脚本只改测试账号密码）────────
            CommonFunction.print_log("INFO",
                "前置步骤：建立 guard 账号安全网（预防性，本脚本不修改 Admin 凭据）")
            guard = setup_guard_account(self.client, self.BMC_IP, self.USERNAME)
            if guard is None:
                CommonFunction.print_log("WARNING",
                    "  ✗ guard 账号建立失败（账号数已满或 BMC 权限不足），"
                    "无安全网情况下禁止执行密码反向测试，本次测试跳过 → WARNING")
                result = "WARNING"
                raise RuntimeError("guard_not_ready")   # 跳至 finally 清理，不执行测试
            else:
                CommonFunction.print_log("INFO",
                    "  guard 安全网就绪（本脚本正常不会触发恢复逻辑）")

            # Step 0：预检查
            CommonFunction.print_log("INFO", "Step 0：预检查测试账号是否已存在")
            existing = self.client.get_accounts()
            if any(a.user_name == self.TEST_ACCOUNT_USER for a in existing):
                delete_account_safe(self.client, self.TEST_ACCOUNT_USER)

            # Step 1：创建测试账号（合法密码）
            CommonFunction.print_log("INFO", f"Step 1：创建测试账号 {self.TEST_ACCOUNT_USER}")
            self.client.add_account(Account(
                UserName=self.TEST_ACCOUNT_USER,
                Password=self.TEST_ACCOUNT_PASS,
                RoleId="Operator",
                Enabled=True
            ))
            self.test_username = self.TEST_ACCOUNT_USER
            self.current_password = self.TEST_ACCOUNT_PASS

            # Step 2：必须拦截的负向密码（<8位 / 复杂度不足）
            CommonFunction.print_log("INFO", "=== Step 2：必须拦截密码验证 ===")
            for pw, desc in INVALID_PASSWORDS:
                CommonFunction.print_log("INFO", f"  尝试修改密码 → [{desc}]（期望被拒）")
                try:
                    update_account_safe(self.client, self.test_username,
                                        Account(Password=pw))
                    # 接口未报错，进一步验证非法密码是否真的可登录
                    if self._try_login(pw):
                        CommonFunction.print_log("ERROR",
                            f"    ✗ 非法密码 [{desc}] 被接受且可登录，违反密码规范！")
                        self.current_password = pw
                        result = "FAIL"
                    else:
                        CommonFunction.print_log("INFO",
                            f"    ✓ 接口未报错，但非法密码无法登录（符合预期）")
                except RedfishException as e:
                    CommonFunction.print_log("INFO",
                        f"    ✓ BMC 拒绝非法密码（符合预期）：{str(e)[:80]}")

            # Step 3：超长密码（>12位）—— BMC接受或拒绝均为 PASS（更长=更安全）
            CommonFunction.print_log("INFO", "=== Step 3：超长密码验证（不影响总结果）===")
            for pw, desc in OVERLENGTH_PASSWORDS:
                CommonFunction.print_log("INFO", f"  测试超长密码 → [{desc}]")
                try:
                    update_account_safe(self.client, self.test_username,
                                        Account(Password=pw))
                    if self._try_login(pw):
                        self.current_password = pw
                        CommonFunction.print_log("INFO",
                            f"    ✓ BMC 接受超长密码且可登录（位数更高=更安全，PASS）")
                    else:
                        CommonFunction.print_log("INFO",
                            f"    ✓ BMC 接受超长密码但无法登录（不影响结果）")
                except RedfishException as e:
                    CommonFunction.print_log("INFO",
                        f"    ✓ BMC 拒绝超长密码（严格限制上限，亦 PASS）：{str(e)[:80]}")

        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"Redfish 接口异常：{e}")
            result = "FAIL"
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
                teardown_guard_account(guard, self.client)

        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"password_modify_negative,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "password_modify_negative", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account密码修改反向测试", "value": result})
        test.print_log("INFO", f"Account密码修改反向测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountPasswordNegativeTest("AccountPasswordNegativeTest")
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
        try:
            checker._cleanup()
            checker.close_sdk_client()
        except Exception: pass
        try:
            with open(checker.exit_code_path, "w", encoding="utf-8") as f:
                f.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
