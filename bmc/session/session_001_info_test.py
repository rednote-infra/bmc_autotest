#!/bin/python
"""
Author: Fengmian
Date: 2026/04/27
Usage: python3 bmc/session_001_info_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/27: 新增，SessionService 信息检查（使用 redfish-sdk）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

class Session001InfoTest(BmcTestBase):
    """SessionService 信息检查

    用例编号：Redfish_Session_001
    检查项：SessionService 资源可访问，Sessions 集合链接存在
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/session/session_001_info_test.json"),
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

            # 通过 SDK get_sessions() 间接验证 SessionService 资源可访问
            sessions = self.client.get_sessions()
            test.print_log("INFO", f"当前活跃 Session 数：{len(sessions)}")

            # 验证 Sessions 集合可访问（get_sessions 内部会请求 /redfish/v1/SessionService/Sessions）
            if sessions is not None:
                test.print_log("INFO", "SessionService/Sessions 集合可访问")
                for i, session in enumerate(sessions):
                    session_id = getattr(session, 'id', getattr(session, 'Id', 'N/A'))
                    test.print_log("DEBUG", f"  Session[{i}]: {session_id}")
                final = "PASS"
            else:
                test.print_log("ERROR", "get_sessions 返回 None")
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
        test_name = Session001InfoTest("Session001InfoTest")
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
