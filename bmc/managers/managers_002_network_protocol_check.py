#!/bin/python
"""
Author: Fengmian
Date: 2026/04/27
Usage: python3 bmc/managers_002_network_protocol_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/27: 新增，BMC 网络协议信息检查（HTTP/HTTPS/SSH/IPMI/SNMP/KVM 端口及启用状态）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

class Managers002NetworkProtocolCheck(BmcTestBase):
    """BMC 网络协议信息检查

    用例编号：Redfish_Managers_002
    检查项（等价类：每个协议均有 port 和 protocol_enabled 字段）：
    - http: port > 0, protocol_enabled 有值
    - https: port > 0, protocol_enabled 有值
    - ssh: port > 0, protocol_enabled 有值
    - ipmi: port > 0, protocol_enabled 有值
    - snmp: port > 0, protocol_enabled 有值
    - kvmip: port > 0, protocol_enabled 有值
    - host_name 非空
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_002_network_protocol_check.json"),
        )

    def _parse_protocol(self, val_str):
        """解析协议字段，返回 (port, protocol_enabled_exists)"""
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
        return port_val, enabled_exists

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        proto_info = {}
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )
            np = self.client.get_network_protocol()

            # host_name 非空
            host_name = getattr(np, 'host_name', None)
            test.print_log("INFO", f"host_name: {host_name}")
            ok = bool(host_name)
            checks.append(("host_name 非空", ok))
            if not ok:
                test.print_log("ERROR", "host_name 为空")

            proto_attrs = [
                ("http", "HTTP"),
                ("https", "HTTPS"),
                ("ssh", "SSH"),
                ("ipmi", "IPMI"),
                ("snmp", "SNMP"),
                ("kvmip", "KVM/IP"),
            ]
            for attr, label in proto_attrs:
                val_str = str(getattr(np, attr, None) or "")
                port_val, enabled_exists = self._parse_protocol(val_str)
                proto_info[label] = val_str
                test.print_log("INFO", f"{label}: {val_str}")

                ok = port_val is not None and port_val > 0
                checks.append((f"{label} port 为正整数", ok))
                if not ok:
                    test.print_log("ERROR", f"{label} port 异常：{val_str}")

                ok = enabled_exists
                checks.append((f"{label} protocol_enabled 字段存在", ok))
                if not ok:
                    test.print_log("ERROR", f"{label} protocol_enabled 字段缺失：{val_str}")

            final = "PASS" if all(r for _, r in checks) else "FAIL"

            # detail.cycle 保存各检查项结果
            for label, passed in checks:
                test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                           value={"metrics": label, "value": "PASS" if passed else "FAIL"})
            # 查询到的网络协议信息
            if final == "PASS":
                test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                           value={"metrics": "network_protocol_info", "value": proto_info})

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
        test_name = Managers002NetworkProtocolCheck("Managers002NetworkProtocolCheck")
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
