#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/managers_025_syslog_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/11: 新增，BMC Syslog 服务配置查询与测试告警触发测试
2026/10/16: SDK 1.3.0 适配 - 改用 get_syslog_service() 类型化接口，URI 由 SDK 自动发现（issue/8）

测试内容：
  1. 查询 Syslog 服务配置，验证关键字段（ServerIdentitySource/TransmissionProtocol/
     AlarmSeverity/SyslogServers）
  2. 若存在已配置的 SyslogServer（ServerAddress 非空），触发 SubmitTestEvent
  3. 若无已配置 Server，记录 WARNING，仅只读校验（仍 PASS）

PASS 标准：
  - Syslog 服务查询成功，结构字段完整
  - TransmissionProtocol 值在已知合法范围 [UDP, TCP, TLS] 内
  - 若有已配置 Server：POST SubmitTestEvent 返回成功
  - 若无 Server：查询校验通过即 PASS

注意：
  被测固件 SyslogServers PATCH 存在格式校验 bug，本脚本不做 PATCH 操作。
"""

import os
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

VALID_PROTOCOLS = ["UDP", "TCP", "TLS"]

class Managers025SyslogTest(BmcTestBase):
    """BMC Syslog 服务查询与测试告警触发"""

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_025_syslog_test.json"),
        )
        self.command_check_result = "FAIL"
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        detail = {}
        final  = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP, username=self.USERNAME, password=self.PASSWORD
            )

            # ── 1. 查询 Syslog 服务配置（SDK 1.3.0 类型化接口，URI 由 SDK 自动发现）──
            syslog_data = self.client.get_syslog_service()
            # ServerIdentitySource/AuthenticateMode 为 OEM 扩展字段，经 extra 保留可属性访问
            identity_src = getattr(syslog_data, "ServerIdentitySource", None)
            protocol     = syslog_data.transmission_protocol
            alarm_sev    = syslog_data.alarm_severity
            syslog_svrs  = syslog_data.syslog_servers or []
            auth_mode    = getattr(syslog_data, "AuthenticateMode", None)

            test.print_log("INFO",
                f"Syslog 配置：IdentitySource={identity_src}, Protocol={protocol}, "
                f"AlarmSeverity={alarm_sev}, AuthMode={auth_mode}, "
                f"SyslogServers 数量={len(syslog_svrs) if isinstance(syslog_svrs, list) else 'N/A'}")

            detail["initial"] = {
                "ServerIdentitySource": identity_src,
                "TransmissionProtocol": protocol,
                "AlarmSeverity":        alarm_sev,
                "AuthenticateMode":     auth_mode,
                "SyslogServerCount":    len(syslog_svrs) if isinstance(syslog_svrs, list) else 0,
            }

            checks.append(("Syslog 服务查询成功", True))
            # TransmissionProtocol 合规性：未配置（null）时记 WARNING，不视为失败
            if protocol is None:
                test.print_log("WARNING",
                    "TransmissionProtocol 为空（Syslog 未配置传输协议），跳过合规校验")
            else:
                valid = protocol in VALID_PROTOCOLS
                checks.append((f"TransmissionProtocol 合规（{protocol}）", valid))
                if not valid:
                    test.print_log("WARNING",
                        f"TransmissionProtocol={protocol} 不在已知范围 {VALID_PROTOCOLS}，记录 WARNING")

            checks.append(("SyslogServers 字段为列表结构",
                            isinstance(syslog_svrs, list)))

            # ── 2. 触发 Syslog 测试告警 ───────────────────────────────────
            # ServerAddress 为 OEM 字段（SDK SyslogServer 模型为 Address），经 extra 保留
            active_servers = []
            if isinstance(syslog_svrs, list):
                active_servers = [
                    s for s in syslog_svrs
                    if getattr(s, "ServerAddress", "") or (s.address or "")
                ]

            if not active_servers:
                test.print_log("WARNING",
                    "未配置 SyslogServer 地址，跳过 SubmitTestEvent（无接收端）")
                detail["syslog_test"] = {"skipped": True, "reason": "no_syslog_server_configured"}
            else:
                # 对第一个有效 Server 触发测试
                # SubmitTestEvent action SDK 未封装，target 从资源 Actions 元数据动态获取
                first = active_servers[0]
                member_id = getattr(first, "MemberId", None)
                if member_id is None:
                    member_id = first.member_id if first.member_id is not None else 0
                actions = getattr(syslog_data, "Actions", None) or {}
                submit_action = actions.get("#SyslogService.SubmitTestEvent", {})
                submit_target = submit_action.get("target", "") if isinstance(submit_action, dict) else ""
                if not submit_target:
                    test.print_log("WARNING",
                        "SyslogService 无 #SyslogService.SubmitTestEvent action（该 BMC 不支持），跳过测试告警触发")
                    detail["syslog_test"] = {"skipped": True, "reason": "no_submit_test_event_action"}
                else:
                    test.print_log("INFO",
                        f"对 MemberId={member_id} 发送 Syslog 测试告警...")
                    try:
                        self.client.post(submit_target, {"MemberId": member_id})
                        test.print_log("INFO", "Syslog SubmitTestEvent POST 成功")
                        checks.append(("Syslog 测试告警触发成功", True))
                        detail["syslog_test"] = {"member_id": member_id, "result": "PASS"}
                    except RedfishException as e:
                        test.print_log("ERROR", f"Syslog SubmitTestEvent POST 失败：{e}")
                        checks.append(("Syslog 测试告警触发成功", False))
                        detail["syslog_test"] = {"member_id": member_id, "result": "FAIL", "error": str(e)}

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish 异常：{e}")
        except Exception as e:
            test.print_log("ERROR", f"异常：{e}")
            traceback.print_exc()
        finally:
            try:
                self.client.close()
            except Exception:
                pass

        final = "PASS" if checks and all(r for _, r in checks) else "FAIL"
        for label, ok in checks:
            test.print_log("INFO" if ok else "ERROR", f"{'✓' if ok else '✗'} {label}")
        test.print_log("INFO" if final == "PASS" else "ERROR", f"测试结束，结果：{final}")

        detail["checks"] = [{"label": l, "result": "PASS" if r else "FAIL"} for l, r in checks]
        test.add_key_value_to_json(self.result_json_path, "detail.cycle", value=detail)
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NAME, "value": final})
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        self.command_check_result = final

if __name__ == "__main__":
    exit_code = 0
    obj = None
    try:
        obj = Managers025SyslogTest("Managers025SyslogTest")
        obj.run_test()
        exit_code = 0 if obj.command_check_result == "PASS" else 2
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"异常：{e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        if obj and hasattr(obj, "exit_code_path"):
            with open(obj.exit_code_path, "w", encoding="utf-8") as f:
                f.write(str(exit_code))
    sys.exit(exit_code)
