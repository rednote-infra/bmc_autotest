#!/bin/python
"""
Author: Fengmian
Date: 2026/04/23
Usage: python3 bmc/account_005a_delete_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/23: 新增

校验策略（覆盖需求 RDSV_BMC_081 正向部分）：
  1. 创建测试账号
  2. delete_account 删除测试账号
  3. get_accounts 验证账号已不存在
  PASS：删除成功且账号从列表中消失
  FAIL：删除失败或账号仍在列表中

  注：超管不可删的反向验证见 account_005b_delete_admin_negative_test.py
"""

import os
import random as _random
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.models import Account
from func.bmc_account_function import delete_account_safe

class AccountDeleteTest(BmcTestBase):
    """Account 删除测试

    用例编号：Redfish_Account_005a
    检查项：删除账号后验证账号从列表中消失
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/account/account_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        ta = conf_section["TestAccount"]
        # 加两位随机数字后缀，部分 BMC 不允许下划线，只用纯字母数字以确保兼容性
        self.TEST_ACCOUNT_USER = ta["UserName"] + str(_random.randint(10, 99))
        self.TEST_ACCOUNT_PASS = ta["Password"]
        self.TEST_ACCOUNT_ROLE = ta["RoleId"]
        self.test_username     = None  # 预初始化，run_test 中实际赋值

    def close_sdk_client(self):
        if self.client:
            try: self.client.close()
            except Exception: pass

    def _cleanup(self):
        """若测试账号未被删除（失败场景），回滚清理"""
        if self.test_username and self.client:
            try:
                delete_account_safe(self.client, self.test_username)
            except Exception as e:
                CommonFunction.print_log("WARNING", f"[清理] 删除测试账号失败（可忽略）：{e}")

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：Account删除测试（正向），测试用例编号：Redfish_Account_005a")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"

        try:
            # Step 0：预检查，若测试账号已存在则先清理
            CommonFunction.print_log("INFO", "Step 0：预检查测试账号是否已存在")
            existing = self.client.get_accounts()
            if any(a.user_name == self.TEST_ACCOUNT_USER for a in existing):
                CommonFunction.print_log("WARNING",
                    f"测试账号 {self.TEST_ACCOUNT_USER} 已存在（环境遗留），先删除")
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
            CommonFunction.print_log("INFO", "账号创建成功")

            # Step 2：删除账号
            CommonFunction.print_log("INFO", f"Step 2：删除账号 {self.TEST_ACCOUNT_USER}")
            delete_account_safe(self.client, self.TEST_ACCOUNT_USER)
            CommonFunction.print_log("INFO", "删除接口调用成功")

            # Step 3：验证账号已消失
            CommonFunction.print_log("INFO", "Step 3：验证账号已从列表中消失")
            accounts = self.client.get_accounts()
            still_exists = any(a.user_name == self.TEST_ACCOUNT_USER for a in accounts)

            if still_exists:
                CommonFunction.print_log("ERROR", f"账号 {self.TEST_ACCOUNT_USER} 删除后仍存在于列表中")
                result = "FAIL"
            else:
                CommonFunction.print_log("INFO", f"  ✓ 账号 {self.TEST_ACCOUNT_USER} 已成功删除")
                self.test_username = None  # 标记已清理，无需 finally 回滚

        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"Redfish 接口异常：{e}")
            result = "FAIL"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"account_delete,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "account_delete", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account删除测试", "value": result})
        test.print_log("INFO", f"Account删除测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountDeleteTest("AccountDeleteTest")
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
