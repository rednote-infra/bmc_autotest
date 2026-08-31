#!/bin/python
"""
Author: Fengmian
Date: 2026/04/23
Usage: python3 bmc/account_002_view_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/23: 新增

校验策略：
  1. get_accounts 获取账号列表
  2. 验证列表非空
  3. 验证每个账号的关键字段（user_name、role_id、enabled）均非空
  PASS：列表非空且关键字段完整
  FAIL：列表为空或存在字段缺失
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

class AccountViewTest(BmcTestBase):
    """Account 查看测试

    用例编号：Redfish_Account_002
    检查项：查看账号列表，验证列表非空且关键字段完整
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/account/account_test.json"),
        )
    def close_sdk_client(self):
        if self.client:
            try: self.client.close()
            except Exception: pass

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：Account查看测试，测试用例编号：Redfish_Account_002")
        test.print_log("INFO", "测试开始")

        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

        result = "PASS"

        try:
            CommonFunction.print_log("INFO", "Step 1：获取账号列表")
            accounts = self.client.get_accounts()

            if not accounts:
                CommonFunction.print_log("ERROR", "账号列表为空，至少应存在 Admin 账号")
                result = "FAIL"
            else:
                CommonFunction.print_log("INFO", f"共找到 {len(accounts)} 个账号")
                for a in accounts:
                    CommonFunction.print_log("INFO",
                        f"  账号：user_name={a.user_name}, role_id={a.role_id}, "
                        f"enabled={a.enabled}, locked={a.locked}")
                    # 验证关键字段非空
                    missing = [f for f, v in [("user_name", a.user_name), ("role_id", a.role_id), ("enabled", a.enabled)] if v is None]
                    if missing:
                        CommonFunction.print_log("ERROR", f"  账号 id={a.id} 缺少字段：{missing}")
                        result = "FAIL"

                if result == "PASS":
                    CommonFunction.print_log("INFO", "所有账号关键字段完整")

        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"Redfish 接口异常：{e}")
            result = "FAIL"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"未预期异常：{type(e).__name__}: {e}")
            traceback.print_exc()
            result = "FAIL"

        self.command_check_result = result
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"account_view,{result}\n")
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "account_view", "value": result})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": "Account查看测试", "value": result})
        test.print_log("INFO", f"Account查看测试完成，结果：{result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = AccountViewTest("AccountViewTest")
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
        try: checker.close_sdk_client()
        except Exception: pass
        try:
            with open(checker.exit_code_path, "w", encoding="utf-8") as f:
                f.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
