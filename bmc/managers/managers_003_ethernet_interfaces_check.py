#!/bin/python
"""
Author: Fengmian
Date: 2026/04/27
Usage: python3 bmc/managers_003_ethernet_interfaces_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/27: 新增，BMC 网口信息检查（曾用 get_raw 绕过 SDK pydantic NameServers=null 解析 bug）
2026/06/23: 改用 redfish_sdk v1.1.0 get_manager_ethernet_interfaces() 类型化接口；
            原 pydantic 解析 bug 已随 SDK 升级修复，去除文件 _ 前缀恢复执行
2026/10/23: 修复真机验证反馈 - 原脚本未区分 Detect 口与 Sharelink(NCSI) 口，
            统一要求 InterfaceEnabled==True 导致 Sharelink 口（我方规范要求默认关闭）
            被误判 FAIL。
2026/11/16: 网口角色改用统一约定定位（func/bmc_network_function.classify_eth_roles）：
            序号最小的网口为专用口（Detect），其余为 Sharelink 口。
            原因：并非所有厂商都提供 Oem.Public.NCSIModeSelect 字段，仅靠该字段
            会把 Sharelink 口误判为专用口。单网口机型该口即为专用口。

数据来源：get_manager_ethernet_interfaces() → List[EthernetInterface]
"""

import os
import re
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.bmc_network_function import (
    classify_eth_roles, ROLE_DETECT, ROLE_SHARELINK,
)

from redfish_sdk import RedfishClient, RedfishException

MAC_RE = re.compile(r'^([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$')
IPV4_RE = re.compile(r'^\d{1,3}(\.\d{1,3}){3}$')


class Managers003EthernetInterfacesCheck(BmcTestBase):
    """BMC 网口信息检查

    用例编号：Redfish_Managers_003
    数据来源：get_manager_ethernet_interfaces()（v1.1.0 类型化 EthernetInterface 模型）

    检查项分两类（等价类：Detect 口 / Sharelink 口，标准不同）：
    - 至少存在 1 个网口（边界：0 个为 FAIL）
    - Detect 口（非 Sharelink）：
        MACAddress 格式合法、InterfaceEnabled==True、IPv4Addresses 非空且格式合法、
        SpeedMbps > 0、Status.Health=="OK"、Status.State=="Enabled"
    - Sharelink(NCSI) 口：我方规范要求默认关闭，仅做 MAC 格式基础校验；
        不要求 Enabled/IPv4/Speed/Health（关闭状态下这些字段本就可能为空/0/Disabled）；
        若意外检测到 InterfaceEnabled==True 仅记 WARNING 提示（不计入 FAIL，
        合规性判定已由 managers_026_default_config_check 覆盖，避免重复判定标准冲突）。
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_003_ethernet_interfaces_check.json"),
        )

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        eth_info = []
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # v1.1.0：类型化接口获取 BMC 网口列表（取代原 get_raw 绕过）
            interfaces = self.client.get_manager_ethernet_interfaces()
            test.print_log("INFO", f"网口数量：{len(interfaces)}")

            # 边界检查：至少 1 个网口
            ok = len(interfaces) > 0
            checks.append(("至少 1 个网口", ok))
            if not ok:
                test.print_log("ERROR", "EthernetInterfaces 集合为空")
            else:
                # 按约定定位网口角色：序号最小 = 专用口(Detect)，其余 = Sharelink
                for iface, role in classify_eth_roles(interfaces):
                    iface_id = iface.id or "iface_unknown"
                    is_sharelink = (role == ROLE_SHARELINK)
                    port_type = "Sharelink(NCSI)" if is_sharelink else "Detect"

                    # 保存查询到的网口信息
                    iface_data = {
                        "id": iface_id,
                        "PortType": port_type,
                        "MACAddress": iface.mac_address or "",
                        "InterfaceEnabled": iface.interface_enabled,
                        "IPv4Addresses": [
                            a.model_dump(by_alias=True) for a in (iface.ipv4_addresses or [])
                        ],
                        "SpeedMbps": iface.speed_mbps,
                        "Status": iface.status.model_dump(by_alias=True) if iface.status else {},
                    }
                    eth_info.append(iface_data)

                    # MAC 格式（等价类：合法 MAC，边界：格式不合法为 FAIL）——两类接口均需校验
                    mac = iface.mac_address or ""
                    test.print_log("INFO", f"  [{iface_id}|{port_type}] MACAddress: {mac}")
                    mac_ok = bool(MAC_RE.match(str(mac)))
                    checks.append((f"[{iface_id}] MAC 格式合法", mac_ok))
                    if not mac_ok:
                        test.print_log("ERROR", f"  [{iface_id}] MAC 格式异常：{mac}")

                    enabled = iface.interface_enabled
                    test.print_log("INFO", f"  [{iface_id}|{port_type}] InterfaceEnabled: {enabled}")

                    if is_sharelink:
                        # Sharelink(NCSI) 口：我方规范默认关闭，不要求 Enabled/IPv4/Speed/Health，
                        # 仅在意外 Enabled 时提示 WARNING（不计入 FAIL，合规判定交由 managers_026）
                        if enabled is True:
                            test.print_log(
                                "WARNING",
                                f"  [{iface_id}] Sharelink 口当前为 Enabled=True，"
                                f"我方规范要求默认关闭（最终合规判定见 managers_026_default_config_check）"
                            )
                        else:
                            test.print_log("INFO", f"  [{iface_id}] Sharelink 口已关闭，符合预期")
                        continue

                    # ── 以下检查项仅适用于 Detect 口 ──

                    # InterfaceEnabled（等价类：true）
                    ok = enabled is True
                    checks.append((f"[{iface_id}] InterfaceEnabled==True", ok))
                    if not ok:
                        test.print_log("ERROR", f"  [{iface_id}] InterfaceEnabled 不为 True：{enabled}")

                    # IPv4Addresses 非空，Address 格式合法（边界：空列表 FAIL）
                    ipv4_list = iface.ipv4_addresses or []
                    ok = len(ipv4_list) > 0
                    checks.append((f"[{iface_id}] IPv4Addresses 非空", ok))
                    if not ok:
                        test.print_log("ERROR", f"  [{iface_id}] IPv4Addresses 为空")
                    else:
                        addr = ipv4_list[0].address or ""
                        test.print_log("INFO", f"  [{iface_id}] IPv4: {addr}")
                        ok = bool(IPV4_RE.match(str(addr)))
                        checks.append((f"[{iface_id}] IPv4 地址格式合法", ok))
                        if not ok:
                            test.print_log("ERROR", f"  [{iface_id}] IPv4 格式异常：{addr}")

                    # SpeedMbps > 0（边界：0 为异常）
                    speed = iface.speed_mbps
                    test.print_log("INFO", f"  [{iface_id}] SpeedMbps: {speed}")
                    ok = isinstance(speed, (int, float)) and speed > 0
                    checks.append((f"[{iface_id}] SpeedMbps > 0", ok))
                    if not ok:
                        test.print_log("ERROR", f"  [{iface_id}] SpeedMbps 异常：{speed}")

                    # Status.Health == OK，Status.State == Enabled
                    health = iface.status.health if iface.status else ""
                    state = iface.status.state if iface.status else ""
                    test.print_log("INFO", f"  [{iface_id}] Status: Health={health}, State={state}")
                    ok = health == "OK"
                    checks.append((f"[{iface_id}] Health==OK", ok))
                    if not ok:
                        test.print_log("ERROR", f"  [{iface_id}] Health 异常：{health}")
                    ok = state == "Enabled"
                    checks.append((f"[{iface_id}] State==Enabled", ok))
                    if not ok:
                        test.print_log("ERROR", f"  [{iface_id}] State 异常：{state}")

            final = "PASS" if all(r for _, r in checks) else "FAIL"

            # detail.cycle 保存各检查项结果
            for label, passed in checks:
                test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                           value={"metrics": label, "value": "PASS" if passed else "FAIL"})
            # 查询到的网口信息
            if final == "PASS":
                test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                           value={"metrics": "ethernet_interfaces_info", "value": eth_info})

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish SDK 调用失败：{str(e)}")
            final = "FAIL"
        except Exception as e:
            # 含 pydantic ValidationError：若 SDK 仍解析失败，如实记 FAIL 推动修复，不再静默绕过
            test.print_log("ERROR", f"测试异常（可能为 SDK 解析失败）：{str(e)}")
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
        test_name = Managers003EthernetInterfacesCheck("Managers003EthernetInterfacesCheck")
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
