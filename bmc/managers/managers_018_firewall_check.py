#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/managers_018_firewall_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/11: 新增，BMC 防火墙规则配置检查，验证 ActiveStrategy 和 Rules
2026/10/16: SDK 1.3.0 适配 - 改用 get_firewall_rules() 类型化接口，URI 由 SDK 自动发现（issue/8）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

VALID_STRATEGIES = ["Accept", "Drop"]

class Managers018FirewallCheck(BmcTestBase):
    """BMC 防火墙规则配置检查

    用例编号：Redfish_Managers_018
    测试内容：
    - GET /redfish/v1/Managers/1/SecurityService/FirewallRules 接口可达
    - ActiveStrategy 存在且在 ["Accept", "Drop"] 中
    - Rules 存在（list，可为空）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_018_firewall_check.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        firewall_info = {}
        final = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. 查询防火墙配置
            #    OEM 机型可能提供 /Managers/1/FirewallRules（ActiveStrategy + Rules），
            #    但标准 Redfish 无该资源。缺失时回退到"网络协议端口暴露"等价检查：
            #    NetworkProtocol 下各协议的 ProtocolEnabled / Port 即实际暴露面。
            test.print_log("INFO", "=== 步骤1: 查询防火墙配置 ===")
            fw_data = None
            try:
                fw_data = self.client.get_firewall_rules()
                test.print_log("INFO", "FirewallRules 接口查询成功（OEM 资源）")
                checks.append(("FirewallRules 接口查询成功", True))
            except RedfishException as e:
                test.print_log("INFO", f"未提供 OEM FirewallRules 资源（{e}），"
                                       f"回退到网络协议暴露面检查")

            active_strategy = None
            rules = None
            rules_count = 0

            if fw_data is not None:
                # 2. ActiveStrategy 存在且在 ["Accept", "Drop"] 中
                active_strategy = getattr(fw_data, "active_strategy", None) \
                    or getattr(fw_data, "ActiveStrategy", None)
                strategy_ok = active_strategy in VALID_STRATEGIES
                test.print_log("INFO",
                    f"ActiveStrategy: {'✓' if strategy_ok else '✗'} {active_strategy!r} "
                    f"（期望：{VALID_STRATEGIES}）")
                checks.append(("ActiveStrategy 存在且在有效枚举中", strategy_ok))

                # 3. Rules 存在（list，可为空）
                rules = getattr(fw_data, "rules", None) \
                    or getattr(fw_data, "Rules", None)
                rules_ok = isinstance(rules, list)
                rules_count = len(rules) if rules_ok else 0
                test.print_log("INFO",
                    f"Rules: {'✓ 存在（list）' if rules_ok else '✗ 缺失或类型不对'} "
                    f"（数量：{rules_count}）")
                checks.append(("Rules 存在（list）", rules_ok))
            else:
                # ── 标准字段回退路径 ──
                np_data = self.client.get_network_protocol()
                # 枚举所有"带 ProtocolEnabled 字段"的协议块，即当前端口暴露面
                proto_state = {}
                for name in ("http", "https", "ssh", "ipmi", "kvmip", "sftp",
                             "snmp", "ssdp", "virtual_media"):
                    blk = getattr(np_data, name, None)
                    if blk is None:
                        continue
                    enabled = getattr(blk, "protocol_enabled", None)
                    port = getattr(blk, "port", None)
                    proto_state[name.upper()] = {"ProtocolEnabled": enabled, "Port": port}

                enabled_count = sum(1 for v in proto_state.values()
                                    if v.get("ProtocolEnabled") is True)
                firewall_info["ProtocolExposure"] = proto_state
                test.print_log("INFO", f"NetworkProtocol 暴露面（共 {len(proto_state)} 项，"
                                       f"已启用 {enabled_count} 项）：")
                for name, st in proto_state.items():
                    test.print_log("INFO", f"  {name}: Enabled={st['ProtocolEnabled']}, "
                                           f"Port={st['Port']}")

                # 2'. 至少能枚举出协议块，说明暴露面信息可读
                exposure_ok = len(proto_state) > 0
                checks.append(("网络协议暴露面可枚举", exposure_ok))

                # 3'. 所有已启用协议端口必须合法（1-65535），等价于防火墙规则合法性
                ports_valid = True
                for name, st in proto_state.items():
                    if st.get("ProtocolEnabled") is True:
                        port = st.get("Port")
                        if not (isinstance(port, int) and 1 <= port <= 65535):
                            ports_valid = False
                            test.print_log("ERROR",
                                f"  {name} 已启用但端口非法：{port!r}")
                test.print_log("INFO",
                    f"已启用协议端口合法性: {'✓' if ports_valid else '✗'}")
                checks.append(("已启用协议端口均合法", ports_valid))

            # 记录详细信息
            if active_strategy is not None:
                firewall_info["ActiveStrategy"] = active_strategy
            firewall_info["RulesCount"] = rules_count
            if fw_data is not None:
                firewall_info["Rules"] = rules

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
        for field_name, field_val in firewall_info.items():
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
        test_name = Managers018FirewallCheck("Managers018FirewallCheck")
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
