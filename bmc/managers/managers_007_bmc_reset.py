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
"""

import os
import subprocess
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

PING_TIMEOUT = 300    # 业务要求：5min 内 IP ping 通（秒）
REDFISH_TIMEOUT = 600 # 业务要求：10min 内 Redfish 恢复（秒）
IPMI_TIMEOUT = 600    # 业务要求：10min 内 IPMI 接口恢复（秒）
POLL_INTERVAL = 10    # 轮询间隔（秒）

# BMC Manager Reset Action URI（注意：不是 Systems 的 Reset，Systems Reset 会重启服务器整机）
MANAGER_RESET_URI = "/redfish/v1/Managers/1/Actions/Manager.Reset"

class Managers007BmcReset(BmcTestBase):
    """BMC 冷/热重启功能测试

    用例编号：Redfish_Managers_007
    测试内容：
    - 调用 #Manager.Reset (ForceRestart) 重启 BMC
    - 验证 5min 内 BMC IP ping 通
    - 验证 10min 内 Redfish 接口恢复（GET /redfish/v1/Systems/1 → Manufacturer）
    - 验证 10min 内 IPMI 接口恢复（ipmitool mc info）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_007_bmc_reset.json"),
        )

    def _wait_ping(self, test: CommonFunction) -> tuple:
        """等待 BMC IP ping 通，返回 (success, elapsed_seconds)"""
        test.print_log("INFO", f"等待 BMC IP ping 通（超时 {PING_TIMEOUT}s）...")
        time.sleep(10)  # 刚发出 reset，稍等再开始 ping
        start_time = time.time()
        while time.time() - start_time < PING_TIMEOUT:
            ret = subprocess.call(
                ["ping", "-c", "1", "-W", "2", self.BMC_IP],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            elapsed = int(time.time() - start_time)
            if ret == 0:
                test.print_log("INFO", f"BMC IP ping 通，耗时 {elapsed}s")
                return True, elapsed
            time.sleep(POLL_INTERVAL)
        elapsed = int(time.time() - start_time)
        test.print_log("ERROR", f"BMC IP ping 超时（{elapsed}s 内未 ping 通，要求 {PING_TIMEOUT}s）")
        return False, elapsed

    def _wait_redfish(self, test: CommonFunction, start_time: float) -> tuple:
        """等待 Redfish 恢复，返回 (success, elapsed_seconds, manufacturer)"""
        test.print_log("INFO", f"等待 Redfish 恢复（超时 {REDFISH_TIMEOUT}s）...")
        while time.time() - start_time < REDFISH_TIMEOUT:
            try:
                client = RedfishClient(
                    host=self.BMC_IP,
                    username=self.USERNAME,
                    password=self.PASSWORD
                )
                manufacturer = client.get_manufacturer()
                client.close()
                elapsed = int(time.time() - start_time)
                test.print_log("INFO", f"Redfish 恢复，耗时 {elapsed}s，Manufacturer={manufacturer}")
                return True, elapsed, manufacturer
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)
        elapsed = int(time.time() - start_time)
        test.print_log("ERROR", f"Redfish 超时（{elapsed}s 内未恢复，要求 {REDFISH_TIMEOUT}s）")
        return False, elapsed, None

    def _wait_ipmi(self, test: CommonFunction, start_time: float) -> tuple:
        """等待 IPMI 接口恢复，返回 (success, elapsed_seconds, mc_info)"""
        test.print_log("INFO", f"等待 IPMI 接口恢复（超时 {IPMI_TIMEOUT}s）...")
        while time.time() - start_time < IPMI_TIMEOUT:
            try:
                result = subprocess.run(
                    ["ipmitool", "-I", "lanplus", "-H", self.BMC_IP,
                     "-U", self.USERNAME, "-P", self.PASSWORD, "mc", "info"],
                    capture_output=True, text=True, timeout=15
                )
                elapsed = int(time.time() - start_time)
                if result.returncode == 0 and result.stdout.strip():
                    # 提取关键信息（Firmware Revision / Manufacturer ID）
                    mc_lines = [l.strip() for l in result.stdout.splitlines()
                                if any(k in l for k in ["Firmware Revision", "Manufacturer ID", "Product Name"])]
                    test.print_log("INFO", f"IPMI mc info 恢复，耗时 {elapsed}s")
                    for line in mc_lines:
                        test.print_log("INFO", f"  {line}")
                    return True, elapsed, mc_lines
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)
        elapsed = int(time.time() - start_time)
        test.print_log("ERROR", f"IPMI 超时（{elapsed}s 内未恢复，要求 {IPMI_TIMEOUT}s）")
        return False, elapsed, None

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
            # ⚠️ 注意：SDK 的 client.reset() 走的是 Systems（服务器）Reset，不能用！
            # 必须直接 POST Manager 的 Action URI
            test.print_log("INFO", f"发起 BMC 重启（ForceRestart）-> POST {MANAGER_RESET_URI}")
            self.client.post(MANAGER_RESET_URI, {"ResetType": "ForceRestart"})
            self.client.close()
            self.client = None
            test.print_log("INFO", "BMC 重启命令已发送（仅重启 BMC 管理控制器，不影响服务器）")
            checks.append(("BMC Reset 命令发送成功", True))

            # 3. 记录重置时间起点（从发出 Reset 命令开始计时）
            reset_start = time.time()
            recovery_info = {}

            # 4. 验证 5min 内 IP ping 通
            ping_ok, ping_elapsed = self._wait_ping(test)
            checks.append((f"BMC IP ping 通（要求≤{PING_TIMEOUT}s，实际{ping_elapsed}s）", ping_ok))
            recovery_info["ping_elapsed_s"] = ping_elapsed
            recovery_info["ping_ok"] = ping_ok

            # 5. 验证 10min 内 Redfish 恢复（用 get_manufacturer() 验证）
            redfish_ok, redfish_elapsed, manufacturer = self._wait_redfish(test, reset_start)
            checks.append((f"Redfish 恢复（要求≤{REDFISH_TIMEOUT}s，实际{redfish_elapsed}s）", redfish_ok))
            recovery_info["redfish_elapsed_s"] = redfish_elapsed
            recovery_info["redfish_ok"] = redfish_ok
            recovery_info["manufacturer"] = manufacturer

            # 6. 验证 10min 内 IPMI 恢复（ipmitool mc info）
            ipmi_ok, ipmi_elapsed, mc_info = self._wait_ipmi(test, reset_start)
            checks.append((f"IPMI 恢复（要求≤{IPMI_TIMEOUT}s，实际{ipmi_elapsed}s）", ipmi_ok))
            recovery_info["ipmi_elapsed_s"] = ipmi_elapsed
            recovery_info["ipmi_ok"] = ipmi_ok
            recovery_info["mc_info"] = mc_info

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
