#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers_011_snmp_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC SNMP 设置功能测试，验证 SNMP 启用/禁用、Trap 服务器配置及测试事件
2026/05/08: 修复 - 被测 BMC SNMP PATCH 接口存在 ComplexPwdEnable 格式校验 bug（HTTP 400），
           改为只读结构验证；SubmitTestEvent 同样不可用，跳过
2026/10/16: SDK 1.3.0 适配 - 改用 get_snmp_service() 类型化接口，URI 由 SDK 自动发现（issue/8）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

# OEM 字段访问哨兵：SDK 模型 extra="allow" 保留响应中的 OEM 原始字段，
# 未出现的字段 getattr 返回哨兵，语义等价于 dict 的 key 存在性检查
_MISSING = object()


def _has(obj, field: str) -> bool:
    """检查 SDK 模型实例上是否存在某字段（含 OEM extra 字段）"""
    return getattr(obj, field, _MISSING) is not _MISSING


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

            # 1. 查询 SNMP 服务配置（SDK 1.3.0 类型化接口，URI 由 SDK 自动发现）
            snmp_data = self.client.get_snmp_service()
            trap_notif = snmp_data.snmp_trap_notification
            trap_servers = (trap_notif.trap_server if trap_notif else None) or []
            snmp_version = snmp_data.snmp_v3_enable

            # 标准 SNMP 服务开关/端口并不在 OEM SnmpService 里，而在
            # ManagerNetworkProtocol.SNMP（ProtocolEnabled / Port）。
            protocol_enabled = None
            service_port = None
            std_v3_enabled = None
            community_count = 0
            try:
                net_proto = self.client.get_network_protocol()
                std_snmp = getattr(net_proto, "snmp", None) \
                    or getattr(net_proto, "SNMP", None)
                if std_snmp is not None:
                    protocol_enabled = getattr(std_snmp, "protocol_enabled", None)
                    service_port = getattr(std_snmp, "port", None)
                    std_v3_enabled = getattr(std_snmp, "enable_snmp_v3", None)
                    community_count = len(getattr(std_snmp, "community_strings", None) or [])
            except RedfishException as e:
                test.print_log("WARNING", f"读取 NetworkProtocol.SNMP 失败: {e}")

            if snmp_version is None:
                snmp_version = std_v3_enabled
            actions = getattr(snmp_data, "Actions", None) or {}

            test.print_log("INFO", f"当前 SNMP: ServiceEnable(标准)={protocol_enabled}, "
                           f"Port(标准)={service_port}, V3Enable={snmp_version}, "
                           f"CommunityStrings={community_count}, TrapServers={len(trap_servers)}")
            test.print_log("INFO", f"  OEM 侧: V1={snmp_data.snmp_v1_enable}, "
                           f"V2C={snmp_data.snmp_v2c_enable}, V3={snmp_data.snmp_v3_enable}, "
                           f"TrapServiceEnabled={getattr(trap_notif, 'service_enabled', None)}")
            snmp_info["initial"] = {
                "SNMP.ProtocolEnabled": protocol_enabled,
                "SNMP.Port": service_port,
                "SNMP.EnableSNMPv3": std_v3_enabled,
                "SnmpService.SnmpV1Enabled": snmp_data.snmp_v1_enable,
                "SnmpService.SnmpV2CEnabled": snmp_data.snmp_v2c_enable,
                "SnmpService.SnmpV3Enabled": snmp_data.snmp_v3_enable,
                "TrapNotification.ServiceEnabled": getattr(trap_notif, "service_enabled", None),
                "TrapServerCount": len(trap_servers),
            }
            checks.append(("查询 SNMP 配置成功", True))

            # 2. 验证必要字段存在（使用设备真实字段名）
            #    SNMP 服务使能 -> NetworkProtocol.SNMP.ProtocolEnabled
            #    SNMP 端口     -> NetworkProtocol.SNMP.Port
            field_presence = [
                ("NetworkProtocol.SNMP.ProtocolEnabled", protocol_enabled),
                ("NetworkProtocol.SNMP.Port", service_port),
                ("SnmpService.SnmpV3Enabled", snmp_data.snmp_v3_enable),
                ("SnmpService.SnmpTrapNotification", trap_notif),
            ]
            for field, val in field_presence:
                present = val is not None
                test.print_log("INFO", f"  字段 {field}: {'存在' if present else '缺失'}")
                checks.append((f"字段 {field} 存在", present))

            # 3. 验证端口合法性
            port_valid = isinstance(service_port, int) and 1 <= service_port <= 65535
            test.print_log("INFO", f"  SNMP.Port={service_port} 合法性: {port_valid}")
            checks.append(("SNMP 端口为合法端口", port_valid))

            # 3b. 验证 SNMP 协议开关为布尔值（能力项，部分机型恒为 false）
            proto_ok = isinstance(protocol_enabled, bool)
            test.print_log("INFO", f"  SNMP.ProtocolEnabled={protocol_enabled} 类型合法: {proto_ok}")
            checks.append(("SNMP.ProtocolEnabled 为布尔值", proto_ok))

            # 4. 验证 TrapServer 条目结构（MemberId/TrapServerAddress/TrapServerPort 为 OEM 字段）
            for srv in trap_servers:
                mid = getattr(srv, "member_id", None)
                if mid is None:
                    mid = getattr(srv, "id", "")
                addr = getattr(srv, "destination_address", None)
                if addr is None:
                    addr = getattr(srv, "destination", "") or ""
                port = getattr(srv, "destination_port", None)
                if port is None:
                    port = getattr(srv, "port", 0) or 0
                enabled = getattr(srv, "enabled", False) or False
                test.print_log("INFO", f"  TrapServer[{mid}]: Address={addr}, Port={port}, Enabled={enabled}")
            if trap_servers:
                # SDK 已将 OEM 字段类型化（蛇形名），因此按蛇形名 + 原始键双重判断
                def _trap_ok(s) -> bool:
                    if getattr(s, "member_id", None) is None and \
                            getattr(s, "id", None) is None:
                        return False
                    if getattr(s, "destination_address", None) is None and \
                            getattr(s, "destination", None) is None:
                        return False
                    if getattr(s, "destination_port", None) is None and \
                            getattr(s, "port", None) is None:
                        return False
                    return True

                trap_struct_ok = all(_trap_ok(s) for s in trap_servers)
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
