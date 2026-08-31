#!/bin/python
"""
Author: Fengmian
Date: 2026/04/24
Usage: python3 bmc/account_003a_role_modify_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/24: 新增（从 account_003_privilege_test.py 拆分）

校验策略（覆盖需求 RDSV_BMC_082 权限修改部分）：
  验证 BMC 支持通过 Redfish 修改账号 RoleId，覆盖所有合法角色边界值：
    Operator → User      （高权限 → 低权限）
    User     → Operator  （低权限 → 高权限）
    Operator → Administrator（非超管 → 最高权限）
  PASS：每次修改后 get_accounts 验证 role_id 字段与预期一致
  FAIL：任意修改失败或验证不符

  finally 回滚：删除测试账号
"""

import os
import sys
import time
import traceback
import random as _random

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.bmc_account_function import delete_account_safe, update_account_safe

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.models import Account

# 角色修改边界值用例：(from_role, to_role, 说明)
ROLE_TRANSITIONS = [
    ("Operator", "User",          "高权限 → 低权限"),
    ("User",     "Operator",      "低权限 → 高权限"),
    ("Operator", "Administrator", "非超管 → 最高权限"),
]

class AccountRoleModifyTest(BmcTestBase):
    """Account 权限（RoleId）修改测试

    用例编号：Redfish_Account_003a
    检查项：覆盖所有角色边界值的修改场景
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

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", "测试用例名称：Account权限修改测试，测试用例编号：Redfish_Account_003a")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"

        try:
            # Step 0：预检查
            CommonFunction.print_log("INFO", "Step 0：预检查测试账号是否已存在")
            existing = self.client.get_accounts()
            if any(a.user_name == self.TEST_ACCOUNT_USER for a in existing):
                delete_account_safe(self.client, self.TEST_ACCOUNT_USER)

            # Step 1：创建测试账号（初始 Operator）
            CommonFunction.print_log("INFO",
                f"Step 1：创建测试账号 {self.TEST_ACCOUNT_USER}（初始 RoleId=Operator）")
            self.client.add_account(Account(
                UserName=self.TEST_ACCOUNT_USER,
                Password=self.TEST_ACCOUNT_PASS,
                RoleId="Operator",
                Enabled=True
            ))
            self.test_username = self.TEST_ACCOUNT_USER

            # Step 2：逐一遍历角色边界值
            current_role = "Operator"
            for idx, (from_role, to_role, desc) in enumerate(ROLE_TRANSITIONS, start=2):
                # 确保当前 role 与 from_role 一致（首次已是 Operator，后续接续）
                if current_role != from_role:
                    CommonFunction.print_log("INFO",
                        f"Step {idx}：先调整至 {from_role}（过渡步骤）")
                    update_account_safe(self.client, self.TEST_ACCOUNT_USER,
                                        Account(RoleId=from_role))
                    current_role = from_role

                CommonFunction.print_log("INFO",
                    f"Step {idx}：修改权限 {from_role} → {to_role}（{desc}）")
                update_account_safe(self.client, self.TEST_ACCOUNT_USER,
                                    Account(RoleId=to_role))

                # 验证
                accounts = self.client.get_accounts()
                found = next((a for a in accounts
                              if a.user_name == self.TEST_ACCOUNT_USER), None)
                if found is None:
                    CommonFunction.print_log("ERROR", f"  账号 {self.TEST_ACCOUNT_USER} 不见了")
                    result = "FAIL"
                elif found.role_id == to_role:
                    CommonFunction.print_log("INFO", f"  ✓ role_id 已更新为 {found.role_id}")
                    current_role = to_role
                else:
                    CommonFunction.print_log("ERROR",
                        f"  ✗ role_id 期望={to_role}，实际={found.role_id}")
                    result = "FAIL"

        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"Redfish 接口异常：{e}")
            result = "FAIL"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"role_modify,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "role_modify", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account权限修改测试", "value": result})
        test.print_log("INFO", f"Account权限修改测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountRoleModifyTest("AccountRoleModifyTest")
        checker.run_test()
        exit_code = 0 if checker.command_check_result == "PASS" else \
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
