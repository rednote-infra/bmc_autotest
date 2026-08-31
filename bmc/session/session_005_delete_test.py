#!/bin/python
"""
Author: Fengmian
Date: 2026/04/27
Usage: python3 bmc/session_005_delete_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/27: 新增，Session 删除测试（使用 redfish-sdk create_session + delete_session）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

class Session005DeleteTest(BmcTestBase):
    """Session 删除测试

    用例编号：Redfish_Session_005
    检查项：create_session 创建后，delete_session 成功删除，再次查询不存在
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/session/session_005_delete_test.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # Step 1：创建 Session
            new_session = self.client.create_session(self.USERNAME, self.PASSWORD)
            session_id = getattr(new_session, 'id', getattr(new_session, 'Id', None))

            if not session_id:
                test.print_log("ERROR", "创建 Session 成功但未获取到 ID")
                final = "FAIL"
            else:
                test.print_log("INFO", f"创建 Session 成功，ID: {session_id}")

                # Step 2：删除 Session
                self.client.delete_session(session_id)
                test.print_log("INFO", f"删除 Session 成功")

                # Step 3：验证已删除 — 查询列表确认不存在
                sessions = self.client.get_sessions()
                remaining_ids = [
                    getattr(s, 'id', getattr(s, 'Id', ''))
                    for s in (sessions or [])
                ]
                if session_id not in remaining_ids:
                    test.print_log("INFO", f"验证：Session {session_id} 已从列表中移除")
                    final = "PASS"
                else:
                    test.print_log("ERROR", f"删除后 Session {session_id} 仍在列表中")
                    final = "FAIL"

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish SDK 调用失败：{str(e)}")
            final = "FAIL"
        except Exception as e:
            test.print_log("ERROR", f"测试异常：{str(e)}")
            traceback.print_exc()
            final = "FAIL"
        finally:
            if self.client:
                self.client.close()

        self.command_check_result = final
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NUM, "value": final})
        with open(self.result_csv_path, "a") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        test.print_log("INFO", f"测试完成，结果：{final}")

if __name__ == '__main__':
    exit_code = 0
    test_name = None
    try:
        test_name = Session005DeleteTest("Session005DeleteTest")
        test_name.run_test()
        if test_name.command_check_result in ["FINISH", "PASS"]:
            exit_code = 0
        elif test_name.command_check_result == "FAIL":
            exit_code = 2
        else:
            exit_code = 1
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
            if test_name and hasattr(test_name, 'exit_code_path'):
                with open(test_name.exit_code_path, "w", encoding="utf-8") as e:
                    e.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
