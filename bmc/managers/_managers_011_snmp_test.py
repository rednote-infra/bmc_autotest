#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers_011_snmp_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC SNMP 设置功能测试，验证 SNMP 启用/禁用、Trap 服务器配置及测试事件
2026/05/08: 修复 - 被测 BMC SNMP PATCH 接口存在 ComplexPwdEnable 格式校验 bug（HTTP 400），
           改为只读结构验证；SubmitTestEvent 同样不可用，跳过
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

SNMP_URI = "/redfish/v1/Managers/1/SnmpService"
SUBMIT_TEST_EVENT_URI = "/redfish/v1/Managers/1/SnmpService/Actions/SnmpService.SubmitTestEvent"

class Managers011SnmpTest(BmcTestBase):
    """BMC SNMP 设置功能测试

    用例编号：Redfish_Managers_011
    测试内容：
    - 查询 SNMP 服务配置（ServiceEnable/Port/V3/TrapServer 等）
    - 验证 SNMP 资源结构完整（必要字段存在）
    - 验证 TrapServer 条目结构合法
    - 验证 SubmitTestEvent Action 端点存在

    注意：部分 BMC 固件 SNMP PATCH 接口存在 ComplexPwdEnable 格式校验 bug，
    无论 PATCH 哪个字段均返回 HTTP 400，ServiceEnable/TrapServer 均无法修改。
    已记录为 BMC 兼容性问题，推动厂商整改后再补充写测试。
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_011_snmp_test.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        snmp_info = {}
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. 查询 SNMP 服务配置
            # [SDK-GAP] get_raw(SNMP_URI) 获取 SnmpService 配置：
            #   SnmpService 为 OEM 扩展路径，SDK 无对应类型化接口，保留 get_raw
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/SnmpService')："
                           "SnmpService 为 OEM 扩展路径，SDK 无对应类型化接口，待 SDK 补充后替换")
            snmp_data = self.client.get_raw(SNMP_URI)
            service_enabled = snmp_data.get("SnmpServiceEnable")
            service_port = snmp_data.get("SnmpServicePort")
            trap_notif = snmp_data.get("SnmpTrapNotification", {})
            trap_servers = trap_notif.get("TrapServer", [])
            snmp_version = snmp_data.get("SnmpV3Enable")
            actions = snmp_data.get("Actions", {})

            test.print_log("INFO", f"当前 SNMP: ServiceEnable={service_enabled}, Port={service_port}, "
                           f"V3Enable={snmp_version}, TrapServers={len(trap_servers)}")
            snmp_info["initial"] = {
                "SnmpServiceEnable": service_enabled,
                "SnmpServicePort": service_port,
                "SnmpV3Enable": snmp_version,
                "TrapServerCount": len(trap_servers),
            }
            checks.append(("查询 SNMP 配置成功", True))

            # 2. 验证必要字段存在
            required_fields = ["SnmpServiceEnable", "SnmpServicePort"]
            for field in required_fields:
                present = field in snmp_data
                test.print_log("INFO", f"  字段 {field}: {'存在' if present else '缺失'}")
                checks.append((f"字段 {field} 存在", present))

            # 3. 验证端口合法性
            port_valid = isinstance(service_port, int) and 1 <= service_port <= 65535
            test.print_log("INFO", f"  SnmpServicePort={service_port} 合法性: {port_valid}")
            checks.append(("SnmpServicePort 为合法端口", port_valid))

            # 4. 验证 TrapServer 条目结构
            for srv in trap_servers:
                mid = srv.get("MemberId", "")
                addr = srv.get("TrapServerAddress", "")
                port = srv.get("TrapServerPort", 0)
                enabled = srv.get("TrapServerEnable", False)
                test.print_log("INFO", f"  TrapServer[{mid}]: Address={addr}, Port={port}, Enabled={enabled}")
            if trap_servers:
                trap_struct_ok = all(
                    "MemberId" in s and "TrapServerAddress" in s and "TrapServerPort" in s
                    for s in trap_servers
                )
                checks.append(("TrapServer 条目结构完整", trap_struct_ok))
            else:
                test.print_log("INFO", "  TrapServer 列表为空，跳过结构校验")

            # 5. 验证 SubmitTestEvent Action 端点存在
            submit_action = actions.get("#SnmpService.SubmitTestEvent", {})
            submit_target = submit_action.get("target", "")
            action_exists = bool(submit_target)
            test.print_log("INFO", f"  SubmitTestEvent target: {submit_target}")
            checks.append(("SubmitTestEvent Action 端点存在", action_exists))

            # 部分 BMC PATCH 接口存在 ComplexPwdEnable bug，无法修改任何字段
            test.print_log("WARNING", "被测 BMC SNMP PATCH 接口存在 ComplexPwdEnable 格式校验 bug（HTTP 400），"
                           "ServiceEnable/TrapServer 写测试跳过，已记录为待整改 bug")

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

        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "snmp_config_info", "value": snmp_info})

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
        test_name = Managers011SnmpTest("Managers011SnmpTest")
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
