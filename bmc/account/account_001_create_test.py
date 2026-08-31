#!/bin/python
"""
Author: Fengmian
Date: 2026/04/23
Usage: python3 bmc/account_001_create_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/23: 新增

校验策略：
  1. 获取当前账号列表（记录基准）
  2. 创建测试账号（TestAccount）
  3. get_accounts 验证新账号存在、字段与预期一致
  PASS：账号创建成功且字段正确
  FAIL：创建失败或验证字段不符

  finally 回滚：无论结果如何，删除测试账号（保持环境干净）
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

class AccountCreateTest(BmcTestBase):
    """Account 创建测试

    用例编号：Redfish_Account_001
    检查项：创建账号后验证账号存在且字段与预期一致
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/account/account_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        ta = conf_section["TestAccount"]
        # 加两位随机数字后缀，避免 BMC 账号软删除延迟导致 Duplicate username 报错
        # 注意：部分 BMC 不允许用户名含下划线，只用纯字母数字以确保兼容性
        self.TEST_ACCOUNT_USER    = ta["UserName"] + str(_random.randint(10, 99))
        self.TEST_ACCOUNT_PASS    = ta["Password"]
        self.TEST_ACCOUNT_ROLE    = ta["RoleId"]
        self.TEST_ACCOUNT_ENABLED = ta["Enabled"]
        self.test_username    = None  # 预初始化，run_test 中实际赋值

    def close_sdk_client(self):
        if self.client:
            try: self.client.close()
            except Exception: pass

    def _cleanup(self):
        """回滚：删除测试账号"""
        if self.test_username and self.client:
            try:
                delete_account_safe(self.client, self.test_username)
            except Exception as e:
                CommonFunction.print_log("WARNING", f"[清理] 删除测试账号失败（可忽略）：{e}")

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：Account创建测试，测试用例编号：Redfish_Account_001")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"

        try:
            # Step 0：预检查，若测试账号已存在则先清理（防止环境脏数据）
            CommonFunction.print_log("INFO", "Step 0：预检查测试账号是否已存在")
            existing = self.client.get_accounts()
            if any(a.user_name == self.TEST_ACCOUNT_USER for a in existing):
                CommonFunction.print_log("WARNING",
                    f"测试账号 {self.TEST_ACCOUNT_USER} 已存在（环境遗留），先删除")
                delete_account_safe(self.client, self.TEST_ACCOUNT_USER)

            # Step 1：获取基准账号列表
            CommonFunction.print_log("INFO", "Step 1：获取当前账号列表（基准）")
            before = {a.user_name for a in self.client.get_accounts()}
            CommonFunction.print_log("INFO", f"当前账号：{before}")

            # Step 2：创建测试账号
            CommonFunction.print_log("INFO", f"Step 2：创建测试账号 {self.TEST_ACCOUNT_USER}（RoleId={self.TEST_ACCOUNT_ROLE}）")
            new_account = Account(
                UserName=self.TEST_ACCOUNT_USER,
                Password=self.TEST_ACCOUNT_PASS,
                RoleId=self.TEST_ACCOUNT_ROLE,
                Enabled=self.TEST_ACCOUNT_ENABLED
            )
            created = self.client.add_account(new_account)
            self.test_username = self.TEST_ACCOUNT_USER
            CommonFunction.print_log("INFO", f"账号创建成功：{created}")

            # Step 3：验证账号存在且字段正确
            CommonFunction.print_log("INFO", "Step 3：验证账号存在及字段")
            after = self.client.get_accounts()
            found = next((a for a in after if a.user_name == self.TEST_ACCOUNT_USER), None)

            if found is None:
                CommonFunction.print_log("ERROR", f"账号 {self.TEST_ACCOUNT_USER} 创建后未在列表中找到")
                result = "FAIL"
            else:
                checks = [
                    ("user_name", found.user_name, self.TEST_ACCOUNT_USER),
                    ("role_id",   found.role_id,   self.TEST_ACCOUNT_ROLE),
                    ("enabled",   found.enabled,   self.TEST_ACCOUNT_ENABLED),
                ]
                for field, actual, expected in checks:
                    if actual == expected:
                        CommonFunction.print_log("INFO", f"  ✓ {field}={actual}")
                    else:
                        CommonFunction.print_log("ERROR", f"  ✗ {field} 期望={expected}，实际={actual}")
                        result = "FAIL"

        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"Redfish 接口异常：{e}")
            result = "FAIL"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        # 写结果
        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"account_create,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "account_create", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account创建测试", "value": result})
        test.print_log("INFO", f"Account创建测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountCreateTest("AccountCreateTest")
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
