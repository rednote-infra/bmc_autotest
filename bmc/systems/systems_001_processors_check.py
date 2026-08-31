#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_001_processors_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    Manufacturer、Model、Socket、TotalCores（> 0）、TotalThreads（>= TotalCores）、
    InstructionSet、MaxSpeedMHz（> 0）、ProcessorArchitecture、SerialNumber、
    TDPWatts（> 0）、Status.Health
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    OperatingSpeedMHz、ProcessorType、MaxTDPWatts

CSV：每行一个处理器
JSON detail.cycle：每个处理器一条记录；summary：整体 PASS/FAIL
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

VALID_HEALTH = {"OK", "Warning", "Critical"}

class ProcessorsInfoCheck(BmcTestBase):
    """Systems Processors 集合资源信息检查

    用例编号：Redfish_Systems_001
    检查项：每个 CPU 的关键字段非空/合法性校验
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_001_processors_check.json"),
        )
    def _init_sdk_client(self):
        CommonFunction.print_log("DEBUG", "开始初始化 Redfish SDK 客户端")
        self.client = RedfishClient(
            host=self.BMC_IP,
            username=self.USERNAME,
            password=self.PASSWORD,
        )
        CommonFunction.print_log("DEBUG", "Redfish SDK 客户端初始化完成")

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
                CommonFunction.print_log("DEBUG", "Redfish SDK 客户端已关闭")
            except Exception as e:
                CommonFunction.print_log("WARNING", f"关闭 SDK 客户端时出错: {str(e)}")

    # ── 单颗 CPU 校验 ─────────────────────────────────────────────────────────

    def _check_processor(self, cpu) -> tuple:
        cpu_id = str(cpu.socket) if cpu.socket is not None else (cpu.id or "unknown")
        all_pass = True

        # ══ 必要字段 ══

        # 1. Manufacturer
        if not cpu.manufacturer or (isinstance(cpu.manufacturer, str) and cpu.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：Manufacturer = {cpu.manufacturer}")

        # 2. Model
        if not cpu.model or (isinstance(cpu.model, str) and cpu.model.strip() == ""):
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：Model 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：Model = {cpu.model}")

        # 3. Socket
        if cpu.socket is None or (isinstance(cpu.socket, str) and cpu.socket.strip() == ""):
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：Socket 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：Socket = {cpu.socket}")

        # 4. TotalCores > 0
        if cpu.total_cores is None:
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：TotalCores 为 None")
            all_pass = False
        elif not isinstance(cpu.total_cores, int) or cpu.total_cores <= 0:
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：TotalCores = {cpu.total_cores}，应为正整数")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：TotalCores = {cpu.total_cores}")

        # 5. TotalThreads >= TotalCores
        if cpu.total_threads is None:
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：TotalThreads 为 None")
            all_pass = False
        elif not isinstance(cpu.total_threads, int) or cpu.total_threads <= 0:
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：TotalThreads = {cpu.total_threads}，应为正整数")
            all_pass = False
        elif cpu.total_cores and cpu.total_threads < cpu.total_cores:
            CommonFunction.print_log(
                "ERROR",
                f"[CPU] {cpu_id}：TotalThreads({cpu.total_threads}) < TotalCores({cpu.total_cores})，数据异常"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：TotalThreads = {cpu.total_threads}")

        # 6. InstructionSet
        if not cpu.instruction_set or (isinstance(cpu.instruction_set, str) and cpu.instruction_set.strip() == ""):
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：InstructionSet 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：InstructionSet = {cpu.instruction_set}")

        # 7. MaxSpeedMHz > 0
        if cpu.max_speed_mhz is None:
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：MaxSpeedMHz 为 None")
            all_pass = False
        elif not isinstance(cpu.max_speed_mhz, int) or cpu.max_speed_mhz <= 0:
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：MaxSpeedMHz = {cpu.max_speed_mhz}，应为正整数")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：MaxSpeedMHz = {cpu.max_speed_mhz}")

        # 8. ProcessorArchitecture
        if not cpu.processor_architecture or (isinstance(cpu.processor_architecture, str) and cpu.processor_architecture.strip() == ""):
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：ProcessorArchitecture 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：ProcessorArchitecture = {cpu.processor_architecture}")

        # 9. SerialNumber
        if not cpu.serial_number or (isinstance(cpu.serial_number, str) and cpu.serial_number.strip() == ""):
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：SerialNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：SerialNumber = {cpu.serial_number}")

        # 10. TDPWatts > 0
        if cpu.tdp_watts is None:
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：TDPWatts 为 None")
            all_pass = False
        elif not isinstance(cpu.tdp_watts, (int, float)) or cpu.tdp_watts <= 0:
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：TDPWatts = {cpu.tdp_watts}，应为正数")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：TDPWatts = {cpu.tdp_watts} W")

        # 11. Status.Health 必须为 "OK"
        health = cpu.status.health if cpu.status else None
        if health is None:
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：Status.Health 为 None")
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log(
                "ERROR",
                f"[CPU] {cpu_id}：Status.Health = '{health}'，不在允许值 {VALID_HEALTH} 内"
            )
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log("ERROR", f"[CPU] {cpu_id}：Status.Health = '{health}'，处理器健康状态异常")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：Status.Health = {health}，健康正常")

        # ══ 非必要字段 ══

        # 12. OperatingSpeedMHz
        if cpu.operating_speed_mhz is None:
            CommonFunction.print_log("WARNING", f"[CPU] {cpu_id}：OperatingSpeedMHz 为 None")
        elif not isinstance(cpu.operating_speed_mhz, int) or cpu.operating_speed_mhz <= 0:
            CommonFunction.print_log("WARNING", f"[CPU] {cpu_id}：OperatingSpeedMHz = {cpu.operating_speed_mhz}，应为正整数")
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：OperatingSpeedMHz = {cpu.operating_speed_mhz}")

        # 13. ProcessorType
        if not cpu.processor_type or (isinstance(cpu.processor_type, str) and cpu.processor_type.strip() == ""):
            CommonFunction.print_log("WARNING", f"[CPU] {cpu_id}：ProcessorType 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：ProcessorType = {cpu.processor_type}")

        # 14. MaxTDPWatts
        if cpu.max_tdp_watts is None:
            CommonFunction.print_log("WARNING", f"[CPU] {cpu_id}：MaxTDPWatts 为 None")
        elif not isinstance(cpu.max_tdp_watts, (int, float)) or cpu.max_tdp_watts <= 0:
            CommonFunction.print_log("WARNING", f"[CPU] {cpu_id}：MaxTDPWatts = {cpu.max_tdp_watts}，应为正数")
        else:
            CommonFunction.print_log("INFO", f"[CPU] {cpu_id}：MaxTDPWatts = {cpu.max_tdp_watts} W")

        cpu_info = {
            "socket":                str(cpu.socket) if cpu.socket is not None else "",
            "manufacturer":          cpu.manufacturer or "",
            "model":                 cpu.model or "",
            "serial_number":         cpu.serial_number or "",
            "total_cores":           cpu.total_cores,
            "total_threads":         cpu.total_threads,
            "instruction_set":       cpu.instruction_set or "",
            "max_speed_mhz":         cpu.max_speed_mhz,
            "processor_architecture": cpu.processor_architecture or "",
            "tdp_watts":             cpu.tdp_watts,
            "health":                health or "",
            "check_result":          "PASS" if all_pass else "FAIL",
        }
        return all_pass, cpu_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def processors_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 Processors 数据")
        try:
            cpu_list = self.client.get_processors()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Processors 数据失败：{str(e)}")
            return "FAIL", []

        if not cpu_list:
            CommonFunction.print_log("ERROR", "Processors 列表为空，检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共获取到 {len(cpu_list)} 个处理器，开始逐一校验")
        all_results = []
        cpu_info_list = []
        for cpu in cpu_list:
            passed, cpu_info = self._check_processor(cpu)
            all_results.append(passed)
            cpu_info_list.append(cpu_info)

        final = "PASS" if all(all_results) else "FAIL"
        failed_cnt = sum(1 for r in all_results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"Processors 信息检查全部通过，共 {len(cpu_list)} 个")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"Processors 信息检查失败，{failed_cnt}/{len(cpu_list)} 个处理器存在必要字段异常"
            )
        return final, cpu_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, cpu_info_list = self.processors_info_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in cpu_info_list:
                f.write(
                    f'"{info["socket"]}",'
                    f'"{info["manufacturer"]}",'
                    f'"{info["model"]}",'
                    f'"{info["serial_number"]}",'
                    f'{info["total_cores"]},'
                    f'{info["total_threads"]},'
                    f'"{info["instruction_set"]}",'
                    f'{info["max_speed_mhz"]},'
                    f'"{info["processor_architecture"]}",'
                    f'{info["tdp_watts"]},'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in cpu_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'CPU_Socket{info["socket"]}', "value": info}
            )

        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result}
        )
        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{self.command_check_result}")

# ── 主程序 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = ProcessorsInfoCheck("ProcessorsInfoCheck")
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
