#!/bin/python
"""
Author: Fengmian
Date: 2026/04/27
Usage: python3 bmc/managers_001_info_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/27: 新增，BMC 基本信息检查
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

class Managers001InfoCheck(BmcTestBase):
    """BMC 基本信息检查

    用例编号：Redfish_Managers_001
    检查项：
    - manager_type 为 "BMC"（等价类：合法值）
    - firmware_version 非空
    - model 非空
    - status.state 为 "Enabled"
    - status.health 为 "OK"
    - uuid 非空且格式合法（含连字符）
    - date_time 非空（时区偏移可读）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_001_info_check.json"),
        )

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )
            m = self.client.get_manager()

            # 1. manager_type == "BMC"（等价类：合法值）
            manager_type = getattr(m, 'manager_type', None)
            test.print_log("INFO", f"manager_type: {manager_type}")
            ok = manager_type == "BMC"
            checks.append(("manager_type == 'BMC'", ok))
            if not ok:
                test.print_log("ERROR", f"manager_type 异常：{manager_type}")

            # 2. firmware_version 非空（边界：不能为 None / 空串）
            fw = getattr(m, 'firmware_version', None)
            test.print_log("INFO", f"firmware_version: {fw}")
            ok = bool(fw)
            checks.append(("firmware_version 非空", ok))
            if not ok:
                test.print_log("ERROR", "firmware_version 为空")

            # 3. model 非空
            model = getattr(m, 'model', None)
            test.print_log("INFO", f"model: {model}")
            ok = bool(model)
            checks.append(("model 非空", ok))
            if not ok:
                test.print_log("ERROR", "model 为空")

            # 4. status.state == "Enabled"
            status_str = str(getattr(m, 'status', ''))
            test.print_log("INFO", f"status: {status_str}")
            ok = "Enabled" in status_str
            checks.append(("status.state == Enabled", ok))
            if not ok:
                test.print_log("ERROR", f"status.state 不为 Enabled：{status_str}")

            # 5. status.health == "OK"
            ok = "OK" in status_str
            checks.append(("status.health == OK", ok))
            if not ok:
                test.print_log("ERROR", f"status.health 不为 OK：{status_str}")

            # 6. uuid 非空且含连字符（格式校验边界）
            uuid = getattr(m, 'uuid', None)
            test.print_log("INFO", f"uuid: {uuid}")
            ok = bool(uuid) and "-" in str(uuid)
            checks.append(("uuid 非空且格式合法", ok))
            if not ok:
                test.print_log("ERROR", f"uuid 异常：{uuid}")

            # 7. date_time 非空（时区边界：含 +/- 偏移）
            dt = getattr(m, 'date_time', None)
            test.print_log("INFO", f"date_time: {dt}")
            ok = bool(dt)
            checks.append(("date_time 非空", ok))
            if not ok:
                test.print_log("ERROR", "date_time 为空")

            final = "PASS" if all(r for _, r in checks) else "FAIL"

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

        # detail.cycle 保存各检查项结果
        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})
        # 查询到的 BMC 信息也保存到 detail.cycle
        if final == "PASS":
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": "manager_info", "value": {
                                           "manager_type": getattr(m, 'manager_type', None),
                                           "firmware_version": getattr(m, 'firmware_version', None),
                                           "model": getattr(m, 'model', None),
                                           "status": str(getattr(m, 'status', None)),
                                           "uuid": getattr(m, 'uuid', None),
                                           "date_time": getattr(m, 'date_time', None),
                                           "date_time_local_offset": getattr(m, 'date_time_local_offset', None)
                                       }})

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
        test_name = Managers001InfoCheck("Managers001InfoCheck")
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
