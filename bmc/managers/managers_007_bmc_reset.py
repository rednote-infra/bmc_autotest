#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers_007_bmc_reset.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC 冷/热重启功能测试（ForceRestart），验证 BMC 重启后可正常连接
2026/05/08: 修复 - SDK 的 client.reset() 走的是 Systems（服务器）的 Reset，会重启整机！
           改用 client.post() 直接调用 Manager 的 Reset Action（BMC 管理控制器）
2026/05/08: 改善 - 新增 Redfish 恢复验证（10min内）和 IPMI mc info 验证（10min内）
           业务要求：5min 内 IP ping 通，10min 内 Redfish 和 IPMI 接口恢复
2026/10/16: SDK 1.3.0 适配 - 改用 reset_manager()（SDK 自动发现 Manager.Reset target，
           1.1.1 的 client.reset() 走 Systems 的问题已由 SDK 独立接口解决）（issue/8）
"""

import os
import subprocess
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction, ipmitool_bin, ping_once
from func.bmc_test_base import BmcTestBase
from func.bmc_reset_state import (
    ProbeResult,
    ResetRecoveryStateMachine,
    probe_redfish_service_root,
)

from redfish_sdk import RedfishClient, RedfishException

OUTAGE_TIMEOUT = 120   # Reset must cause an observable Redfish outage.
PING_TIMEOUT = 300     # Network recovery phase deadline.
REDFISH_TIMEOUT = 600  # Redfish recovery phase deadline.
IPMI_TIMEOUT = 600     # IPMI recovery phase deadline.
POLL_INTERVAL = 5

class Managers007BmcReset(BmcTestBase):
    """BMC 冷/热重启功能测试

    用例编号：Redfish_Managers_007
    测试内容：
    - 调用 #Manager.Reset (ForceRestart) 重启 BMC
    - 验证 BMC Redfish 服务确实发生中断
    - 验证 10min 内 Redfish 接口恢复（GET /redfish/v1/Systems/1 → Manufacturer）
    - 验证 10min 内 IPMI 接口恢复（ipmitool mc info）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_007_bmc_reset.json"),
        )

    def _probe_redfish(self) -> ProbeResult:
        return probe_redfish_service_root(
            RedfishClient,
            self.BMC_IP,
            self.USERNAME,
            self.PASSWORD,
        )

    def _probe_ping(self) -> ProbeResult:
        if ping_once(self.BMC_IP, timeout_sec=2):
            return ProbeResult(True, "ICMP echo reply received")
        return ProbeResult(False, "no ICMP echo reply")

    def _probe_ipmi(self) -> ProbeResult:
        try:
            result = subprocess.run(
                [ipmitool_bin(), "-I", "lanplus", "-H", self.BMC_IP,
                 "-U", self.USERNAME, "-P", self.PASSWORD, "mc", "info"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode == 0 and result.stdout.strip():
                mc_lines = [
                    line.strip()
                    for line in result.stdout.splitlines()
                    if any(key in line for key in (
                        "Firmware Revision", "Manufacturer ID", "Product Name"
                    ))
                ]
                detail = "; ".join(mc_lines) or "ipmitool mc info succeeded"
                return ProbeResult(True, detail)
            error = (result.stderr or result.stdout or "empty response").strip()
            return ProbeResult(False, f"exit={result.returncode}: {error[:160]}")
        except Exception as exc:
            return ProbeResult(False, f"{type(exc).__name__}: {str(exc)[:160]}")

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        try:
            # 1. 重启前验证 BMC 可连接
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )
            mgr = self.client.get_manager()
            pre_version = getattr(mgr, 'firmware_version', 'unknown')
            test.print_log("INFO", f"BMC 重启前 FirmwareVersion: {pre_version}")
            checks.append(("BMC 重启前可连接", True))

            # 2. 调用 Manager.Reset 重启 BMC（走 BMC 管理控制器，不是服务器整机）
            # SDK 1.3.0 reset_manager() 使用 Manager 资源自身广播的 Reset target，
            # 与 Systems 的 Reset（重启整机）完全独立
            test.print_log("INFO", "Sending BMC reset (ForceRestart) via reset_manager()")
            request_accepted = False
            request_detail = "reset_manager did not return"
            try:
                self.client.reset_manager(reset_type="ForceRestart")
                request_accepted = True
                request_detail = "reset_manager completed successfully"
            except Exception as exc:
                request_detail = f"{type(exc).__name__}: {str(exc)[:160]}"
            finally:
                try:
                    self.client.close()
                except Exception:
                    pass
                self.client = None

            state_machine = ResetRecoveryStateMachine(logger=test.print_log)
            recovery = state_machine.run(
                request_accepted=request_accepted,
                request_detail=request_detail,
                redfish_probe=self._probe_redfish,
                ipmi_probe=self._probe_ipmi,
                network_probe=self._probe_ping,
                outage_timeout=OUTAGE_TIMEOUT,
                network_timeout=PING_TIMEOUT,
                redfish_timeout=REDFISH_TIMEOUT,
                ipmi_timeout=IPMI_TIMEOUT,
                poll_interval=POLL_INTERVAL,
            )
            checks.extend([
                ("BMC Reset request accepted", recovery.request_accepted),
                ("Redfish service outage observed", recovery.outage_observed),
                ("BMC network recovered", recovery.network_recovered is True),
                ("Redfish service recovered", recovery.redfish_recovered),
                ("IPMI service recovered", recovery.ipmi_recovered),
            ])
            recovery_info = recovery.to_dict()

            final = "PASS" if all(r for _, r in checks) else "FAIL"

            # detail.cycle 保存各检查项结果
            for label, passed in checks:
                test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                           value={"metrics": label, "value": "PASS" if passed else "FAIL"})
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": "recovery_timing", "value": recovery_info})

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
        test_name = Managers007BmcReset("Managers007BmcReset")
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
