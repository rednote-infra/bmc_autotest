#!/bin/python
"""
Author: Fengmian
Date: 2026/04/27
Usage: python3 bmc/managers_006_port_status_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/27: 新增，BMC 关键服务端口状态检查（HTTPS/SSH/IPMI/KVM 端口合法性 + 启用状态）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

# 各协议标准端口（等价类：标准端口值；边界：0 / 负值为 FAIL）
EXPECTED_PORTS = {
    "https": 443,
    "ssh": 22,
    "ipmi": 623,
    "kvmip": 7578,
}

class Managers006PortStatusCheck(BmcTestBase):
    """BMC 关键服务端口状态检查

    用例编号：Redfish_Managers_006
    检查项（等价类/边界）：
    - HTTPS port == 443（标准端口）
    - SSH port == 22（标准端口）
    - IPMI port == 623（标准端口）
    - KVM/IP port == 7578（BMC 厂商约定端口）
    - 以上各协议 protocol_enabled 字段存在
    - 各端口值为正整数（边界：0 / None 为 FAIL）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_006_port_status_check.json"),
        )

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        port_info = {}
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )
            np = self.client.get_network_protocol()

            for attr, expected_port in EXPECTED_PORTS.items():
                val_str = str(getattr(np, attr, None) or "")
                test.print_log("INFO", f"{attr.upper()}: {val_str}")

                port_val = None
                enabled_exists = False
                for part in val_str.split():
                    if part.startswith("port="):
                        try:
                            port_val = int(part.split("=", 1)[1])
                        except ValueError:
                            pass
                    if part.startswith("protocol_enabled="):
                        enabled_exists = True

                port_info[attr.upper()] = val_str

                # port == 期望值（等价类：标准端口）
                ok = port_val == expected_port
                checks.append((f"{attr.upper()} port == {expected_port}", ok))
                if not ok:
                    test.print_log("ERROR", f"{attr.upper()} port 异常，期望 {expected_port}，实际 {port_val}")

                # port 为正整数（边界）
                ok = port_val is not None and port_val > 0
                checks.append((f"{attr.upper()} port 为正整数", ok))
                if not ok:
                    test.print_log("ERROR", f"{attr.upper()} port 非正整数：{port_val}")

                # protocol_enabled 字段存在
                ok = enabled_exists
                checks.append((f"{attr.upper()} protocol_enabled 字段存在", ok))
                if not ok:
                    test.print_log("ERROR", f"{attr.upper()} protocol_enabled 字段缺失")

            final = "PASS" if all(r for _, r in checks) else "FAIL"

            # detail.cycle 保存各检查项结果
            for label, passed in checks:
                test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                           value={"metrics": label, "value": "PASS" if passed else "FAIL"})
            # 查询到的端口状态信息
            if final == "PASS":
                test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                           value={"metrics": "port_status_info", "value": port_info})

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
        test_name = Managers006PortStatusCheck("Managers006PortStatusCheck")
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
