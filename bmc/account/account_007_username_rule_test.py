#!/bin/python
"""
Author: Fengmian
Date: 2026/04/24
Usage: python3 bmc/account_007_username_rule_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/24: 新增

校验策略（覆盖需求 RDSV_BMC_080a）：
  验证 BMC 支持以下合法用户名格式（正向边界值）：
    - 4 位全小写（边界最短）：abcd
    - 16 位全大写（边界最长）：ABCDEFGHIJKLMNOP
    - 全小写 + 数字混合：abc2024
    - 全大写 + 数字混合：ABC2024
    - 大小写混合（无数字）：TrfAdmin
    - 大小写混合 + 数字：TrF2024x

  PASS：所有合法用户名均创建成功
  FAIL：任意合法用户名创建失败

  finally 回滚：删除所有本轮创建的测试账号
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.bmc_account_function import delete_account_safe

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.models import Account

# 合法用户名测试集（正向边界值）
VALID_USERNAMES = [
    ("abcd",             "4位全小写（最短边界）"),
    ("ABCDEFGHIJKLMNOP", "16位全大写（最长边界）"),
    ("abc2024",          "全小写+数字混合"),
    ("ABC2024",          "全大写+数字混合"),
    ("TrfAdmin",         "大小写混合无数字"),
    ("TrF2024x",         "大小写混合含数字"),
]

TEST_PASS = "TestPass@88"

class AccountUsernameRuleTest(BmcTestBase):
    """Account 用户名格式规则测试（正向边界值）

    用例编号：Redfish_Account_007
    检查项：各合法用户名格式均可成功创建账号
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/account/account_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        self.created_users = []  # 预初始化，run_test 中动态追加

    def close_sdk_client(self):
        if self.client:
            try: self.client.close()
            except Exception: pass

    def _cleanup(self):
        if not self.created_users or not self.client:
            return
        CommonFunction.print_log("INFO", f"[清理] 删除 {len(self.created_users)} 个测试账号")
        for username in list(self.created_users):
            try:
                delete_account_safe(self.client, username)
            except Exception as e:
                CommonFunction.print_log("WARNING", f"[清理] 删除 {username} 失败（可忽略）：{e}")
        self.created_users.clear()

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", "测试用例名称：Account用户名格式规则测试，测试用例编号：Redfish_Account_007")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"

        try:
            CommonFunction.print_log("INFO", "=== 正向边界值验证：合法用户名格式 ===")
            for username, desc in VALID_USERNAMES:
                CommonFunction.print_log("INFO", f"  创建 [{desc}] {username!r}")
                try:
                    self.client.add_account(Account(
                        UserName=username,
                        Password=TEST_PASS,
                        RoleId="User",
                        Enabled=True
                    ))
                    self.created_users.append(username)
                    CommonFunction.print_log("INFO", f"    ✓ 创建成功")
                except RedfishException as e:
                    CommonFunction.print_log("ERROR",
                        f"    ✗ 合法用户名 {username!r}（{desc}）创建失败：{str(e)[:120]}")
                    result = "FAIL"

        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"username_rule,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "username_rule", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account用户名格式规则测试", "value": result})
        test.print_log("INFO", f"Account用户名格式规则测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountUsernameRuleTest("AccountUsernameRuleTest")
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
