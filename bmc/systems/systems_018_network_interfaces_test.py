#!/bin/python
"""
Author: Fengmian
Date: 2026/10/28
Usage: python3 bmc/systems_018_network_interfaces_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/10/28: 新增，带内网卡（NetworkInterfaces/NetworkPorts）测试
  （issue/19，SDK 1.3.0 接口覆盖计划 B 档）
  覆盖 SDK 接口：get_network_interfaces() / get_network_ports() / get_network_port()

测试内容：
  1. 【NetworkInterfaces】枚举 System 广告的带内网卡（可选组件，未广告 WARNING 跳过）
  2. 【NetworkPorts】逐网卡读取端口列表（含 LinkStatus/SupportedLinkCapabilities）
  3. 【抽验】单端口按 Id 回读一致
  4. 汇总：网卡数 / 端口数 / Link 状态分布

判定原则：
  - NetworkInterfaces 为可选组件（System 未广告 → 空列表）→ WARNING 跳过不计 FAIL
  - 端口 LinkStatus 为 Down 不判 FAIL（网线未接属正常部署状态），仅存档
  - 抽验回读 Id 不一致 → FAIL
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException


class NetworkInterfacesTest(BmcTestBase):
    """Systems 带内网卡测试（NetworkInterfaces / NetworkPorts）

    用例编号：Redfish_Systems_018
    检查项：
    - NetworkInterfaces 枚举（可选组件，未广告 WARNING 跳过）
    - 逐网卡 NetworkPorts 读取（LinkStatus 存档）
    - 单端口抽验回读一致
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_018_network_interfaces_test.json"),
        )

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []   # (label, passed: bool|None)  None=跳过
        nic_info = {"interfaces": [], "total_ports": 0, "link_status_dist": {}}
        final = None

        try:
            self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)
            if not self.prepare_system_client():
                return

            # ── 1. NetworkInterfaces 枚举（可选组件）────────────────────────
            test.print_log("INFO", "Step 1：枚举 NetworkInterfaces")
            try:
                nics = self.client.get_network_interfaces()
            except RedfishException as e:
                test.print_log("WARNING",
                    f"NetworkInterfaces 枚举失败（{str(e)[:100]}），"
                    f"该 BMC 可能未广告 NetworkInterfaces（可选组件），跳过测试（WARNING 不 FAIL）")
                checks.append(("NetworkInterfaces 枚举", None))
                nic_info["existence"] = "not_advertised"
                self._write_result("WARNING", checks, nic_info)
                return

            if not nics:
                test.print_log("WARNING",
                    "  NetworkInterfaces 为空（System 未广告或无带内网卡），"
                    "跳过测试（WARNING 不 FAIL）")
                checks.append(("NetworkInterfaces 枚举", None))
                nic_info["existence"] = "empty"
                self._write_result("WARNING", checks, nic_info)
                return

            checks.append(("NetworkInterfaces 枚举", True))
            test.print_log("INFO", f"  ✓ 共 {len(nics)} 个 NetworkInterface")

            # ── 2. 逐网卡读取 NetworkPorts ──────────────────────────────────
            test.print_log("INFO", "Step 2：逐网卡读取 NetworkPorts")
            total_ports = 0
            link_dist = {}
            sample_port_id = None
            sample_nic_id = None

            for idx, nic in enumerate(nics):
                nic_id = nic.id or f"nic_{idx}"
                try:
                    ports = self.client.get_network_ports(nic_id)
                except RedfishException as e:
                    test.print_log("WARNING",
                        f"  [{nic_id}] NetworkPorts 读取失败：{str(e)[:100]}")
                    ports = []

                port_entries = []
                for p in (ports or []):
                    pid = p.id or "unknown"
                    link = p.link_status or "Unknown"
                    link_dist[link] = link_dist.get(link, 0) + 1
                    caps = getattr(p, "supported_link_capabilities", None) or []
                    cap_str = ",".join(
                        str(getattr(c, "speed_mbps", "?")) for c in caps
                    ) if caps else ""
                    port_entries.append({
                        "port_id": pid,
                        "link_status": link,
                        "supported_speeds_mbps": cap_str,
                    })
                    if sample_port_id is None:
                        sample_port_id = pid
                        sample_nic_id = nic_id
                    total_ports += 1

                test.print_log("INFO",
                    f"  [{nic_id}] {len(port_entries)} 个端口"
                    f"（{', '.join(e['port_id'] + '=' + e['link_status'] for e in port_entries) or '无端口'}）")
                nic_info["interfaces"].append({
                    "nic_id": nic_id,
                    "odata_id": nic.odata_id,
                    "ports": port_entries,
                })

            checks.append(("NetworkPorts 读取", True))
            nic_info["total_ports"] = total_ports
            nic_info["link_status_dist"] = link_dist
            test.print_log("INFO",
                f"  端口汇总：共 {total_ports} 个，Link 状态分布 {link_dist}")

            # ── 3. 单端口抽验（回读一致）────────────────────────────────────
            if sample_port_id and sample_nic_id:
                test.print_log("INFO",
                    f"Step 3：单端口抽验（nic={sample_nic_id}, port={sample_port_id}）")
                try:
                    back = self.client.get_network_port(sample_port_id, sample_nic_id)
                    ok = (back.id == sample_port_id)
                    checks.append(("NetworkPort 抽验回读一致", ok))
                    if not ok:
                        test.print_log("ERROR",
                            f"  ✗ 抽验不一致：期望 {sample_port_id!r}，实际 {back.id!r}")
                    else:
                        test.print_log("INFO", f"  ✓ 抽验一致：{back.id!r}")
                except RedfishException as e:
                    test.print_log("WARNING",
                        f"  抽验失败（{str(e)[:100]}），记 WARNING 不 FAIL")
                    checks.append(("NetworkPort 抽验回读一致", None))
            else:
                checks.append(("NetworkPort 抽验回读一致", None))  # 无端口可抽验

            # ── 汇总（None=跳过项不计入）────────────────────────────────────
            active = [(n, r) for n, r in checks if r is not None]
            final = "PASS" if all(r for _, r in active) else "FAIL"

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

        self._write_result(final, checks, nic_info)

    def _write_result(self, final, checks, nic_info):
        test = CommonFunction()
        for label, passed in checks:
            value = "SKIP" if passed is None else ("PASS" if passed else "FAIL")
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": value})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "network_interfaces_info", "value": nic_info})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NUM, "value": final})
        with open(self.result_csv_path, "a") as f:
            for label, passed in checks:
                value = "SKIP" if passed is None else ("PASS" if passed else "FAIL")
                f.write(f"{label},{value}\n")
            f.write(f"{self.TEST_NUM},{final}\n")

        self.command_check_result = final
        test.print_log("INFO", f"测试完成，结果：{final}")


if __name__ == '__main__':
    exit_code = 0
    test_name = None
    try:
        test_name = NetworkInterfacesTest("NetworkInterfacesTest")
        test_name.run_test()
        if test_name.command_check_result in ["FINISH", "PASS", "WARNING"]:
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
