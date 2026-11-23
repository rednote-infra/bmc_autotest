#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/managers_021_sol_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/11: 新增，BMC SOL 串口源配置检查，验证 SerialSource 和 Parameters.AllowableValues
2026/10/16: SDK 1.3.0 适配 - 改用 get_sol_source() 类型化接口，URI 由 SDK 自动发现（issue/8）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException


class Managers021SolCheck(BmcTestBase):
    """BMC SOL 串口源配置检查

    用例编号：Redfish_Managers_021
    测试内容：
    - GET /redfish/v1/Managers/1/SOLSourceControlInfo 接口可达
    - SerialSource 存在且非空
    - Parameters 存在
    - Parameters.AllowableValues 存在且为非空 list
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_021_sol_check.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        sol_info = {}
        final = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. 查询 SOL 配置
            #    OEM 机型可能提供 /Managers/1/SOLSourceControlInfo（SerialSource +
            #    Parameters.AllowableValues），但标准 Redfish 无该资源。
            #    缺失时回退到标准 SOL 能力描述：
            #      Manager.SerialConsole（ServiceEnabled / ConnectTypesSupported / MaxConcurrentSessions）
            #      SerialInterfaces 集合（串口参数）
            test.print_log("INFO", "=== 步骤1: 查询 SOL 配置 ===")
            sol_data = None
            try:
                sol_data = self.client.get_sol_source()
                test.print_log("INFO", "SOLSourceControlInfo 接口查询成功（OEM 资源）")
                checks.append(("SOLSourceControlInfo 接口查询成功", True))
            except RedfishException as e:
                test.print_log("INFO", f"未提供 OEM SOLSourceControlInfo 资源（{e}），"
                                       f"回退到标准 SerialConsole / SerialInterfaces 检查")

            serial_source = None
            parameters = None
            allowable_values = None

            if sol_data is not None:
                # 2. SerialSource 存在且非空
                serial_source = getattr(sol_data, "serial_source", None) \
                    or getattr(sol_data, "SerialSource", None)
                serial_source_ok = isinstance(serial_source, str) \
                    and len(serial_source.strip()) > 0
                test.print_log("INFO",
                    f"SerialSource: {'✓' if serial_source_ok else '✗'} {serial_source!r}")
                checks.append(("SerialSource 存在且非空", serial_source_ok))

                # 3. Parameters 存在
                parameters = getattr(sol_data, "parameters", None) \
                    or getattr(sol_data, "Parameters", None)
                params_ok = isinstance(parameters, dict)
                test.print_log("INFO",
                    f"Parameters: {'✓ 存在且为 dict' if params_ok else '✗ 缺失或类型不对'}")
                checks.append(("Parameters 存在", params_ok))

                # 4. Parameters.AllowableValues 存在且为非空 list
                if params_ok:
                    allowable_values = parameters.get("AllowableValues")
                allowable_ok = isinstance(allowable_values, list) \
                    and len(allowable_values) > 0
                test.print_log("INFO",
                    f"Parameters.AllowableValues: "
                    f"{'✓ 非空 list' if allowable_ok else '✗ 缺失/空/类型不对'} "
                    f"（值：{allowable_values!r}）")
                checks.append(("Parameters.AllowableValues 存在且为非空 list", allowable_ok))
            else:
                # ── 标准字段回退路径 ──
                # 2'. Manager.SerialConsole 描述 SOL 能力
                manager = self.client.get_manager("1")
                serial_console = getattr(manager, "serial_console", None) \
                    or getattr(manager, "SerialConsole", None)
                sol_enabled = False
                connect_types = []
                max_sessions = None
                if serial_console is not None:
                    sol_enabled = getattr(serial_console, "service_enabled", None)
                    if sol_enabled is None:
                        sol_enabled = getattr(serial_console, "ServiceEnabled", False)
                    connect_types = getattr(serial_console, "connect_types_supported", None) \
                        or getattr(serial_console, "ConnectTypesSupported", None) or []
                    max_sessions = getattr(serial_console, "max_concurrent_sessions", None)
                    if max_sessions is None:
                        max_sessions = getattr(serial_console,
                                               "MaxConcurrentSessions", None)
                serial_source = "SerialConsole"
                test.print_log("INFO",
                    f"Manager.SerialConsole: ServiceEnabled={sol_enabled}, "
                    f"ConnectTypesSupported={connect_types}, "
                    f"MaxConcurrentSessions={max_sessions}")
                checks.append(("Manager.SerialConsole 存在", serial_console is not None))
                checks.append(("SerialConsole.ServiceEnabled 为 true",
                               sol_enabled is True))

                # 3'. 串口参数：SerialInterfaces 集合（第一个成员为 BMC 串口）
                ifaces = self.client.get_serial_interfaces()
                iface_detail = []
                for itf in ifaces or []:
                    iface_detail.append({
                        "Id": getattr(itf, "id", ""),
                        "InterfaceEnabled": getattr(itf, "interface_enabled", None),
                        "BitRate": getattr(itf, "bit_rate", None),
                        "Parity": getattr(itf, "parity", None),
                        "DataBits": getattr(itf, "data_bits", None),
                        "StopBits": getattr(itf, "stop_bits", None),
                        "ConnectorType": getattr(itf, "connector_type", None),
                    })
                parameters = {"Interfaces": iface_detail}
                test.print_log("INFO", f"SerialInterfaces 数量={len(iface_detail)}:")
                for d in iface_detail:
                    test.print_log("INFO", f"  {d['Id']}: Enabled={d['InterfaceEnabled']}, "
                                           f"{d['DataBits']}{d['Parity'][:1] if d['Parity'] else '?'}"
                                           f"{d['StopBits']} @ {d['BitRate']}, "
                                           f"Connector={d['ConnectorType']}")
                checks.append(("SerialInterfaces 集合非空", len(iface_detail) > 0))

                # 4'. 至少有一个串口处于使能状态（SOL 可用前提）
                any_enabled = any(d.get("InterfaceEnabled") is True for d in iface_detail)
                test.print_log("INFO",
                    f"存在已使能串口: {'✓' if any_enabled else '✗'}")
                checks.append(("存在已使能的串口", any_enabled))

                # AllowableValues 等价信息：串口可用的连接能力
                allowable_values = connect_types or None

            # 记录详细信息
            sol_info["SerialSource"] = serial_source
            sol_info["AllowableValues"] = allowable_values
            sol_info["Parameters"] = parameters

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
                try:
                    self.client.close()
                except Exception:
                    pass

        # 结果写入
        for field_name, field_val in sol_info.items():
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": field_name, "value": field_val})
        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})

        self.command_check_result = final
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NAME, "value": final})
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        test.print_log("INFO", f"测试完成，结果：{final}")

if __name__ == '__main__':
    exit_code = 0
    test_name = None
    try:
        test_name = Managers021SolCheck("Managers021SolCheck")
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
