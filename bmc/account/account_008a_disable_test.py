#!/bin/python
"""
Author: Fengmian
Date: 2026/04/24
Usage: python3 bmc/account_008a_disable_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/24: 新增

校验策略（覆盖需求 RDSV_BMC_083 正向部分）：
  1. 创建测试账号
  2. update_account 禁用该账号（Enabled=False）
  3. 用禁用账号尝试登录 → 期望失败（无法访问）
  PASS：账号禁用成功且禁用后无法登录
  FAIL：禁用失败或禁用后仍可登录

  注：超管不可禁的反向验证见 account_008b_disable_admin_negative_test.py
  finally 回滚：删除测试账号
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.bmc_account_function import delete_account_safe, update_account_safe

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.models import Account
import random as _random

class AccountDisableTest(BmcTestBase):
    """Account 禁用测试

    用例编号：Redfish_Account_008a
    检查项：禁用账号后无法访问；超管不可被禁用
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
        self.TEST_ACCOUNT_ROLE = ta["RoleId"]
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
        test.print_log("INFO", "测试用例名称：Account禁用测试（正向），测试用例编号：Redfish_Account_008a")
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

            # Step 1：创建测试账号
            CommonFunction.print_log("INFO", f"Step 1：创建测试账号 {self.TEST_ACCOUNT_USER}")
            self.client.add_account(Account(
                UserName=self.TEST_ACCOUNT_USER,
                Password=self.TEST_ACCOUNT_PASS,
                RoleId=self.TEST_ACCOUNT_ROLE,
                Enabled=True
            ))
            self.test_username = self.TEST_ACCOUNT_USER

            # Step 2：禁用测试账号
            CommonFunction.print_log("INFO", f"Step 2：禁用账号 {self.TEST_ACCOUNT_USER}")
            update_account_safe(
                self.client,
                self.TEST_ACCOUNT_USER,
                Account(Enabled=False)
            )

            # Step 3：验证禁用后无法登录
            CommonFunction.print_log("INFO", "Step 3：用禁用账号尝试登录（期望失败）")
            try:
                disabled_client = RedfishClient(self.BMC_IP, self.TEST_ACCOUNT_USER, self.TEST_ACCOUNT_PASS)
                disabled_client.get_accounts()
                CommonFunction.print_log("ERROR", "  ✗ 禁用账号仍可登录，违反禁用规则！")
                result = "FAIL"
                try: disabled_client.close()
                except Exception: pass
            except RedfishException as e:
                CommonFunction.print_log("INFO", f"  ✓ 禁用账号登录被拒（符合预期）：{str(e)[:80]}")

        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"Redfish 接口异常：{e}")
            result = "FAIL"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"account_disable,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "account_disable", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account禁用测试", "value": result})
        test.print_log("INFO", f"Account禁用测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountDisableTest("AccountDisableTest")
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
