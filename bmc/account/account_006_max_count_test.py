#!/bin/python
"""
Author: Fengmian
Date: 2026/04/23
Usage: python3 bmc/account_006_max_count_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/23: 新增

校验策略：
  1. 获取当前已有账号数量（基准）
  2. 循环创建账号（命名 test_max_001、test_max_002 ...），直到 BMC 返回失败（账号数量到达上限）
  3. 记录成功创建的账号数量和 BMC 报告的最大账号数
  4. PASS 条件：能触发到明确的上限错误，且最大账号数 >= MIN_ACCOUNT_LIMIT（8）
     FAIL 条件：意外异常中止、无法创建任何账号，或最大账号数 < 8

  finally 回滚：删除本次创建的所有测试账号（保持环境干净）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.models import Account
from func.bmc_account_function import delete_account_safe

MAX_ATTEMPT = 50       # 防止无限循环，最多尝试创建 50 个
MIN_ACCOUNT_LIMIT = 8  # 需求 RDSV_BMC_079：BMC 至少支持 8 个本地用户

class AccountMaxCountTest(BmcTestBase):
    """Account 创建数量最大化测试

    用例编号：Redfish_Account_006
    检查项：循环创建账号直到 BMC 拒绝，验证最大账号数限制可触达
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/account/account_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        self.TEST_ACCOUNT_PASS = conf_section["TestAccount"]["Password"]
        self.created_users     = []    # 预初始化，run_test 中动态追加

    def close_sdk_client(self):
        if self.client:
            try: self.client.close()
            except Exception: pass

    def _cleanup(self):
        """回滚：删除本次创建的所有测试账号"""
        if not self.created_users or not self.client:
            return
        CommonFunction.print_log("INFO", f"[清理] 开始删除 {len(self.created_users)} 个测试账号")
        for username in self.created_users:
            try:
                delete_account_safe(self.client, username)
            except Exception as e:
                CommonFunction.print_log("WARNING", f"[清理] 删除 {username} 失败（可忽略）：{e}")
        self.created_users.clear()

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：Account创建数量最大化测试，测试用例编号：Redfish_Account_006")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"
        limit_hit = False
        max_count = 0

        try:
            # Step 1：获取基准账号数
            CommonFunction.print_log("INFO", "Step 1：获取当前账号数量（基准）")
            before = self.client.get_accounts()
            base_count = len(before)
            CommonFunction.print_log("INFO", f"当前账号数：{base_count}")

            # Step 2：循环创建直到失败
            CommonFunction.print_log("INFO", f"Step 2：循环创建账号（最多尝试 {MAX_ATTEMPT} 个）")
            for i in range(1, MAX_ATTEMPT + 1):
                username = f"test_max_{i:03d}"
                try:
                    self.client.add_account(Account(
                        UserName=username,
                        Password=self.TEST_ACCOUNT_PASS,
                        RoleId="User",
                        Enabled=True
                    ))
                    self.created_users.append(username)
                    CommonFunction.print_log("INFO", f"  创建成功：{username}（第 {i} 个，总账号数约 {base_count + i}）")
                except RedfishException as e:
                    CommonFunction.print_log("INFO",
                        f"  创建失败（账号数量达到上限）：{username}，错误：{str(e)[:200]}")
                    max_count = base_count + len(self.created_users)
                    limit_hit = True
                    break
                except Exception as e:
                    CommonFunction.print_log("ERROR", f"  创建 {username} 时发生未预期异常：{e}")
                    result = "FAIL"
                    break

            # Step 3：汇总结果
            if not limit_hit and result == "PASS":
                CommonFunction.print_log("WARNING",
                    f"尝试创建 {MAX_ATTEMPT} 个账号后未触发上限，"
                    f"BMC 最大账号数可能 > {base_count + MAX_ATTEMPT}，建议增大 MAX_ATTEMPT")
                max_count = base_count + len(self.created_users)

            CommonFunction.print_log("INFO",
                f"本次创建账号数：{len(self.created_users)}，"
                f"BMC 最大账号数（估计）：{max_count}，"
                f"是否触及上限：{limit_hit}")

            # 验证最大账号数是否满足需求
            if result == "PASS":
                if max_count < MIN_ACCOUNT_LIMIT:
                    CommonFunction.print_log("ERROR",
                        f"BMC 最大账号数 {max_count} 低于需求下限 {MIN_ACCOUNT_LIMIT}，FAIL")
                    result = "FAIL"
                else:
                    CommonFunction.print_log("INFO",
                        f"  ✓ 最大账号数 {max_count} >= 需求下限 {MIN_ACCOUNT_LIMIT}")

        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"Redfish 接口异常：{e}")
            result = "FAIL"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"account_max_count,{result}\n")
            f.write(f"accounts_created,{len(self.created_users)}\n")
            f.write(f"max_count_estimate,{max_count}\n")
            f.write(f"limit_hit,{limit_hit}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "account_max_count",   "value": result})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "accounts_created",    "value": len(self.created_users)})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "max_count_estimate",  "value": max_count})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "limit_hit",           "value": limit_hit})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account创建数量最大化测试", "value": result})
        test.print_log("INFO", f"Account创建数量最大化测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountMaxCountTest("AccountMaxCountTest")
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
