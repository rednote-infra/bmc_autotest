#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/systems_005_power_control_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/22: 新增

校验策略：
  前置条件：确保服务器处于开机状态（若已关机则先 On，等 OS 就绪）

  测试序列（由配置 TestSequence 驱动，默认 7 步）：
    ForceOff        → 期望 PowerState=Off
    On              → 期望 PowerState=On + OS 就绪（ping 带内 IP）
    GracefulShutdown→ 期望 PowerState=Off
    On              → 期望 PowerState=On + OS 就绪
    ForceRestart    → 期望完整 Off→On + OS 就绪
    ForcePowerCycle → 期望完整 Off→On + OS 就绪
    PowerCycle      → 期望完整 Off→On + OS 就绪

  任一步 FAIL 后发 ForceOff 使服务器回到已知状态，继续执行后续步骤。
  测试结束后服务器保持开机状态。

  PASS 条件：所有测试步骤通过
  FAIL 条件：任意步骤电源状态或 OS 就绪校验失败
"""

import os
import time
import subprocess
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class PowerControlTest(BmcTestBase):
    """Systems PowerControl 操作测试

    用例编号：Redfish_Systems_005
    检查项：覆盖关机/开机/重启全类 ResetType，验证电源状态变化和 OS 就绪
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_005_power_control_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        self._config_inband_ip          = conf_section.get("InbandIP", "")
        self.POWER_STATE_TIMEOUT        = conf_section.get("PowerStateTimeoutSeconds",     120)
        self.POWER_STATE_POLL_INTERVAL  = conf_section.get("PowerStatePollIntervalSeconds", 5)
        self.OS_READY_TIMEOUT           = conf_section.get("OsReadyTimeoutSeconds",        600)
        self.OS_READY_POLL_INTERVAL     = conf_section.get("OsReadyPollIntervalSeconds",    10)
        self.TEST_SEQUENCE              = conf_section.get("TestSequence", [
            ["ForceOff",         "强制关机"],
            ["On",               "开机"],
            ["GracefulShutdown", "优雅关机"],
            ["On",               "开机"],
            ["ForceRestart",     "强制重启"],
            ["ForcePowerCycle",  "强制掉电重启"],
            ["PowerCycle",       "掉电重启"],
        ])

    def _init_inband_ip(self):
        """配置文件指定优先，否则从 BMC IP 末位去掉首字符 '1' 推导"""
        if self._config_inband_ip:
            self.INBAND_IP = self._config_inband_ip
            CommonFunction.print_log("DEBUG", f"带内 IP 来自配置：{self.INBAND_IP}")
            return
        try:
            parts = self.BMC_IP.split(".")
            last = parts[-1]
            if not last.startswith("1"):
                CommonFunction.print_log("WARNING", f"BMC IP 末位 {last} 不以 '1' 开头，带内 IP 推导可能有误")
            parts[-1] = last[1:]
            self.INBAND_IP = ".".join(parts)
            CommonFunction.print_log("DEBUG", f"带内 IP 推导为：{self.INBAND_IP}")
        except Exception as e:
            CommonFunction.print_log("WARNING", f"带内 IP 推导失败，OS 就绪检查将跳过 ping：{str(e)}")
            self.INBAND_IP = None

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

    # ── 电源状态工具 ──────────────────────────────────────────────────────────

    def _init_client(self):
        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

    def _get_power_state(self):
        """读取当前电源状态：'On' / 'Off' / 'NULL'"""
        try:
            system = self.client.get_system()
            state = getattr(system, "power_state", None)
            CommonFunction.print_log("DEBUG", f"当前电源状态：{state}")
            return state or "NULL"
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"读取电源状态失败：{str(e)}")
            return "NULL"

    def _ping_inband(self):
        """ping 带内 IP，True=通 / False=不通"""
        if not self.INBAND_IP:
            return False
        try:
            ret = subprocess.call(
                ["ping", "-c", "1", "-W", "3", self.INBAND_IP],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return ret == 0
        except Exception:
            return False

    def _wait_for_power_state(self, expected):
        """轮询等待 PowerState == expected，超时返回 False"""
        max_polls = self.POWER_STATE_TIMEOUT // self.POWER_STATE_POLL_INTERVAL
        CommonFunction.print_log(
            "INFO",
            f"等待 PowerState={expected}（超时 {self.POWER_STATE_TIMEOUT}s，"
            f"间隔 {self.POWER_STATE_POLL_INTERVAL}s）"
        )
        for poll in range(int(max_polls)):
            state = self._get_power_state()
            if state == expected:
                CommonFunction.print_log("INFO", f"PowerState 已变为 {expected}，耗时约 {poll * self.POWER_STATE_POLL_INTERVAL}s")
                return True
            time.sleep(self.POWER_STATE_POLL_INTERVAL)
        CommonFunction.print_log("ERROR", f"等待 PowerState={expected} 超时（{self.POWER_STATE_TIMEOUT}s）")
        return False

    def _wait_for_os_ready(self):
        """等待 OS 就绪：ping 带内 IP 通则返回 True"""
        if not self.INBAND_IP:
            CommonFunction.print_log("WARNING", "带内 IP 未配置，跳过 OS 就绪检查，等待 30s 替代")
            time.sleep(30)
            return True
        CommonFunction.print_log("INFO", f"等待 OS 就绪（ping {self.INBAND_IP}，超时 {self.OS_READY_TIMEOUT}s）")
        elapsed = 0
        while elapsed < self.OS_READY_TIMEOUT:
            if self._ping_inband():
                CommonFunction.print_log("INFO", f"ping {self.INBAND_IP} 通，OS 已就绪，耗时约 {elapsed}s")
                return True
            time.sleep(self.OS_READY_POLL_INTERVAL)
            elapsed += self.OS_READY_POLL_INTERVAL
        CommonFunction.print_log("ERROR", f"等待 OS 就绪超时（{self.OS_READY_TIMEOUT}s）")
        return False

    def _wait_for_power_cycle(self):
        """等待完整重启过程：先检测 Off 或 ping 不通，再等 On + ping 通"""
        CommonFunction.print_log("INFO", "等待服务器开始重启（PowerState=Off 或带内 IP 不通）")
        elapsed = 0
        while elapsed < self.POWER_STATE_TIMEOUT:
            state = self._get_power_state()
            if state == "Off" or not self._ping_inband():
                CommonFunction.print_log("INFO", f"检测到重启开始（state={state}），耗时约 {elapsed}s")
                break
            time.sleep(self.POWER_STATE_POLL_INTERVAL)
            elapsed += self.POWER_STATE_POLL_INTERVAL
        else:
            CommonFunction.print_log("ERROR", f"等待重启开始超时（{self.POWER_STATE_TIMEOUT}s）")
            return False

        CommonFunction.print_log("INFO", "等待服务器完成重启（PowerState=On 且带内 IP 通）")
        elapsed = 0
        while elapsed < self.POWER_STATE_TIMEOUT:
            state = self._get_power_state()
            if state == "On" and self._ping_inband():
                CommonFunction.print_log("INFO", f"重启完成，耗时约 {elapsed}s")
                return True
            time.sleep(self.POWER_STATE_POLL_INTERVAL)
            elapsed += self.POWER_STATE_POLL_INTERVAL
        CommonFunction.print_log("ERROR", f"等待重启完成超时（{self.POWER_STATE_TIMEOUT}s）")
        return False

    # ── 单步测试 ──────────────────────────────────────────────────────────────

    def _do_reset(self, reset_type):
        """发送 ResetType，返回 True/False"""
        CommonFunction.print_log("INFO", f"发送电源操作：{reset_type}")
        try:
            self.client.reset(reset_type)
            CommonFunction.print_log("INFO", f"POST {reset_type} 成功")
            return True
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"POST {reset_type} 失败：{str(e)}")
            return False

    def _test_reset_type(self, reset_type):
        """测试单个 ResetType：POST + 状态校验

        :return: "PASS" / "FAIL"
        """
        CommonFunction.print_log("INFO", f"开始测试：{reset_type}")
        try:
            if not self._do_reset(reset_type):
                return "FAIL"

            if reset_type == "ForceOff":
                ok = self._wait_for_power_state("Off")

            elif reset_type == "On":
                ok = self._wait_for_power_state("On")
                if ok:
                    ok = self._wait_for_os_ready()

            elif reset_type == "GracefulShutdown":
                ok = self._wait_for_power_state("Off")

            elif reset_type in ("ForceRestart", "ForcePowerCycle", "PowerCycle"):
                ok = self._wait_for_power_cycle()
                if ok:
                    ok = self._wait_for_os_ready()

            else:
                CommonFunction.print_log("ERROR", f"未知 ResetType：{reset_type}，跳过")
                return "FAIL"

            result = "PASS" if ok else "FAIL"
            CommonFunction.print_log("INFO" if ok else "ERROR", f"{reset_type} 测试结果：{result}")
            return result

        except Exception as e:
            CommonFunction.print_log("ERROR", f"{reset_type} 测试异常：{str(e)}")
            return "FAIL"

    def _ensure_server_on(self):
        """确保服务器处于开机且 OS 就绪状态"""
        state = self._get_power_state()
        if state == "On" and self._ping_inband():
            CommonFunction.print_log("INFO", "服务器已开机且 OS 就绪，无需操作")
            return True
        if state != "On":
            CommonFunction.print_log("INFO", f"服务器当前 {state}，发送 On 开机")
            if not self._do_reset("On"):
                return False
            if not self._wait_for_power_state("On"):
                return False
        return self._wait_for_os_ready()

    # ── 测试主体 ───────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        self._init_client()
        self._init_inband_ip()

        # 前置条件：确保开机
        CommonFunction.print_log("INFO", "检查服务器初始状态")
        if not self._ensure_server_on():
            CommonFunction.print_log("ERROR", "无法确认服务器处于开机状态，测试中止")
            self.command_check_result = "FAIL"
            test.add_key_value_to_json(
                self.result_json_path, "summary",
                value={"metrics": self.TEST_NAME, "value": "FAIL"}
            )
            return

        result_list = []
        overall = "PASS"

        for i, (reset_type, description) in enumerate(self.TEST_SEQUENCE, 1):
            CommonFunction.print_log("INFO", f"电源操作测试第 {i} 步：{description}（{reset_type}）")
            result = self._test_reset_type(reset_type)
            result_list.append((reset_type, result))

            with open(self.result_csv_path, "a", encoding="utf-8") as f:
                f.write(f"{reset_type},{result}\n")
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": reset_type, "value": result}
            )

            if result == "FAIL":
                overall = "FAIL"
                # FAIL 后强制关机使服务器回到已知状态，避免下一步指令叠加
                current = self._get_power_state()
                if current != "Off":
                    CommonFunction.print_log("WARNING", f"第 {i} 步 FAIL 后服务器仍为 {current}，发送 ForceOff 复位")
                    self._do_reset("ForceOff")
                    self._wait_for_power_state("Off")

        self.command_check_result = overall
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"test_result,{overall}\n")
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": overall}
        )

        if overall == "PASS":
            CommonFunction.print_log("INFO", f"所有电源操作测试通过（共 {len(result_list)} 步）")
        else:
            failed = [rt for rt, r in result_list if r == "FAIL"]
            CommonFunction.print_log("ERROR", f"以下电源操作测试失败：{failed}")

        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{overall}")

# ── 主程序 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = PowerControlTest("PowerControlTest")
        checker.run_test()
        if checker.command_check_result == "PASS":
            exit_code = 0
        elif checker.command_check_result == "FAIL":
            exit_code = 2
        else:
            exit_code = 1
        start_time = time.time()
        while (time.time() - start_time) < 5:
            time.sleep(1)
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "检测到键盘中断(Ctrl+C)，提前终止程序")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"发生未处理异常: {str(e)}")
        traceback.print_exc()
        exit_code = 1
    finally:
        try:
            checker.close_sdk_client()
        except Exception:
            pass
        try:
            with open(checker.exit_code_path, "w", encoding="utf-8") as f:
                f.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
