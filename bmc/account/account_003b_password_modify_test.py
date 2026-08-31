#!/bin/python
"""
Author: Fengmian
Date: 2026/04/24
Usage: python3 bmc/account_003b_password_modify_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/24: 新增（从 account_003_privilege_test.py 拆分）

校验策略（覆盖需求 RDSV_BMC_082 密码修改——正向部分）：
  密码规范：大小写字母 + 特殊字符 + 数字，长度 8~12 位

  正向边界值（应修改成功 + 新密码可登录）：
    - 8 位最短：Ab@12345
    - 12 位最长：Ab@123456789
    - 含多种特殊字符：Ab@#$1234

  PASS：所有合法密码修改成功且新密码可登录
  FAIL：任意合法密码修改失败或修改后无法登录

  注：密码负向边界值验证见 account_003c_password_negative_test.py
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

# 正向密码边界值：(password, 说明)
VALID_PASSWORDS = [
    ("Ab@12345",     "8位最短，满足大小写+特殊字符+数字"),
    ("Ab@123456789", "12位最长，满足大小写+特殊字符+数字"),
    ("Ab@#$1234",    "含多种特殊字符"),
]

class AccountPasswordModifyTest(BmcTestBase):
    """Account 密码修改正向测试

    用例编号：Redfish_Account_003b
    检查项：合法密码修改成功且新密码可登录（正向边界值）
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
        test.print_log("INFO", "测试用例名称：Account密码修改正向测试，测试用例编号：Redfish_Account_003b")
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
                RoleId="Operator",
                Enabled=True
            ))
            self.test_username = self.TEST_ACCOUNT_USER
            self.current_password = self.TEST_ACCOUNT_PASS

            # Step 2：正向边界值逐一验证
            CommonFunction.print_log("INFO", "=== Step 2：正向密码边界值验证 ===")
            for pw, desc in VALID_PASSWORDS:
                CommonFunction.print_log("INFO", f"  修改密码 → [{desc}]")
                try:
                    update_account_safe(self.client, self.test_username,
                                        Account(Password=pw))
                    if self._try_login(pw):
                        CommonFunction.print_log("INFO", "    ✓ 密码修改成功，新密码可登录")
                        self.current_password = pw
                    else:
                        CommonFunction.print_log("ERROR",
                            f"    ✗ 密码修改接口成功，但新密码无法登录（{desc}）")
                        result = "FAIL"
                except RedfishException as e:
                    CommonFunction.print_log("ERROR",
                        f"    ✗ 合法密码 [{desc}] 修改被拒：{str(e)[:100]}")
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
            f.write(f"password_modify_positive,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "password_modify_positive", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account密码修改正向测试", "value": result})
        test.print_log("INFO", f"Account密码修改正向测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountPasswordModifyTest("AccountPasswordModifyTest")
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
