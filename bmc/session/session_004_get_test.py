#!/bin/python
"""
Author: Fengmian
Date: 2026/04/27
Usage: python3 bmc/session_004_get_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/27: 新增，单个 Session 详情查询测试（使用 redfish-sdk get_session）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

class Session004GetTest(BmcTestBase):
    """单个 Session 详情查询测试

    用例编号：Redfish_Session_004
    检查项：get_session 返回有效 Session 对象，关键字段完整
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/session/session_004_get_test.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        created_id = None
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 先创建一个 Session，确保有已知 ID 可查
            new_session = self.client.create_session(self.USERNAME, self.PASSWORD)
            created_id = getattr(new_session, 'id', getattr(new_session, 'Id', None))
            test.print_log("INFO", f"预创建 Session ID: {created_id}")

            if not created_id:
                test.print_log("ERROR", "创建 Session 成功但未获取到 ID")
                final = "FAIL"
            else:
                # 使用 SDK get_session 获取单个 Session 详情
                session = self.client.get_session(created_id)
                session_user = getattr(session, 'user_name', getattr(session, 'UserName', 'N/A'))
                test.print_log("INFO", f"Session 详情 - ID: {created_id}, UserName: {session_user}")
                final = "PASS"

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish SDK 调用失败：{str(e)}")
            final = "FAIL"
        except Exception as e:
            test.print_log("ERROR", f"测试异常：{str(e)}")
            traceback.print_exc()
            final = "FAIL"
        finally:
            # 清理：删除预创建的 Session
            if created_id and self.client:
                try:
                    self.client.delete_session(created_id)
                    test.print_log("INFO", f"清理：已删除预创建 Session {created_id}")
                except Exception:
                    pass
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
        test_name = Session004GetTest("Session004GetTest")
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
