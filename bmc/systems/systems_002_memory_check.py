#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_002_memory_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    Manufacturer、SerialNumber、PartNumber、CapacityMiB（> 0）、MemoryDeviceType、
    BaseModuleType、RankCount（> 0）、DataWidthBits（> 0）、
    MemoryLocation（Socket/Slot/Channel 均不为 None）、Status.Health
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    OperatingSpeedMhz、ErrorCorrection、MemoryType

CSV：每行一条内存模块
JSON detail.cycle：每条内存一条记录；summary：整体 PASS/FAIL
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

class MemoryInfoCheck(BmcTestBase):
    """Systems Memory 集合资源信息检查

    用例编号：Redfish_Systems_002
    检查项：每条内存模块的关键字段非空/合法性校验
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_002_memory_check.json"),
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

    # ── 单条内存校验 ──────────────────────────────────────────────────────────

    def _check_memory(self, mem) -> tuple:
        mem_id = mem.device_locator or mem.id or "unknown"
        all_pass = True

        # ══ 必要字段 ══

        # 1. Manufacturer
        if not mem.manufacturer or (isinstance(mem.manufacturer, str) and mem.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：Manufacturer = {mem.manufacturer}")

        # 2. SerialNumber
        if not mem.serial_number or (isinstance(mem.serial_number, str) and mem.serial_number.strip() == ""):
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：SerialNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：SerialNumber = {mem.serial_number}")

        # 3. PartNumber
        if not mem.part_number or (isinstance(mem.part_number, str) and mem.part_number.strip() == ""):
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：PartNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：PartNumber = {mem.part_number}")

        # 4. CapacityMiB > 0
        if mem.capacity_mib is None:
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：CapacityMiB 为 None")
            all_pass = False
        elif not isinstance(mem.capacity_mib, int) or mem.capacity_mib <= 0:
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：CapacityMiB = {mem.capacity_mib}，应为正整数")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：CapacityMiB = {mem.capacity_mib} MiB")

        # 5. MemoryDeviceType
        if not mem.memory_device_type or (isinstance(mem.memory_device_type, str) and mem.memory_device_type.strip() == ""):
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：MemoryDeviceType 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：MemoryDeviceType = {mem.memory_device_type}")

        # 6. BaseModuleType
        if not mem.base_module_type or (isinstance(mem.base_module_type, str) and mem.base_module_type.strip() == ""):
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：BaseModuleType 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：BaseModuleType = {mem.base_module_type}")

        # 7. RankCount > 0
        if mem.rank_count is None:
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：RankCount 为 None")
            all_pass = False
        elif not isinstance(mem.rank_count, int) or mem.rank_count <= 0:
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：RankCount = {mem.rank_count}，应为正整数")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：RankCount = {mem.rank_count}")

        # 8. DataWidthBits > 0
        if mem.data_width_bits is None:
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：DataWidthBits 为 None")
            all_pass = False
        elif not isinstance(mem.data_width_bits, int) or mem.data_width_bits <= 0:
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：DataWidthBits = {mem.data_width_bits}，应为正整数")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：DataWidthBits = {mem.data_width_bits} bits")

        # 9. MemoryLocation（Socket / Slot / Channel 均不为 None）
        loc = mem.memory_location
        loc_socket = loc_slot = loc_channel = None
        if loc is None:
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：MemoryLocation 为 None")
            all_pass = False
        else:
            loc_socket  = loc.socket
            loc_slot    = loc.slot
            loc_channel = loc.channel
            if loc_socket is None:
                CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：MemoryLocation.Socket 为 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"[MEM] {mem_id}：MemoryLocation.Socket = {loc_socket}")
            if loc_slot is None:
                CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：MemoryLocation.Slot 为 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"[MEM] {mem_id}：MemoryLocation.Slot = {loc_slot}")
            if loc_channel is None:
                CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：MemoryLocation.Channel 为 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"[MEM] {mem_id}：MemoryLocation.Channel = {loc_channel}")

        # 10. Status.Health 必须为 "OK"
        health = mem.status.health if mem.status else None
        if health is None:
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：Status.Health 为 None")
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log(
                "ERROR",
                f"[MEM] {mem_id}：Status.Health = '{health}'，不在允许值 {VALID_HEALTH} 内"
            )
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log("ERROR", f"[MEM] {mem_id}：Status.Health = '{health}'，内存健康状态异常")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：Status.Health = {health}，健康正常")

        # ══ 非必要字段 ══

        # 11. OperatingSpeedMhz
        if mem.operating_speed_mhz is None:
            CommonFunction.print_log("WARNING", f"[MEM] {mem_id}：OperatingSpeedMhz 为 None")
        elif not isinstance(mem.operating_speed_mhz, int) or mem.operating_speed_mhz <= 0:
            CommonFunction.print_log("WARNING", f"[MEM] {mem_id}：OperatingSpeedMhz = {mem.operating_speed_mhz}，应为正整数")
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：OperatingSpeedMhz = {mem.operating_speed_mhz} MHz")

        # 12. ErrorCorrection
        if not mem.error_correction or (isinstance(mem.error_correction, str) and mem.error_correction.strip() == ""):
            CommonFunction.print_log("WARNING", f"[MEM] {mem_id}：ErrorCorrection 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：ErrorCorrection = {mem.error_correction}")

        # 13. MemoryType
        if not mem.memory_type or (isinstance(mem.memory_type, str) and mem.memory_type.strip() == ""):
            CommonFunction.print_log("WARNING", f"[MEM] {mem_id}：MemoryType 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[MEM] {mem_id}：MemoryType = {mem.memory_type}")

        mem_info = {
            "device_locator":    mem.device_locator or "",
            "manufacturer":      mem.manufacturer or "",
            "serial_number":     mem.serial_number or "",
            "part_number":       mem.part_number or "",
            "capacity_mib":      mem.capacity_mib,
            "memory_device_type": mem.memory_device_type or "",
            "base_module_type":  mem.base_module_type or "",
            "rank_count":        mem.rank_count,
            "data_width_bits":   mem.data_width_bits,
            "location_socket":   loc_socket,
            "location_slot":     loc_slot,
            "location_channel":  loc_channel,
            "health":            health or "",
            "check_result":      "PASS" if all_pass else "FAIL",
        }
        return all_pass, mem_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def memory_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 Memory 数据")
        try:
            mem_list = self.client.get_memory()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Memory 数据失败：{str(e)}")
            return "FAIL", []

        if not mem_list:
            CommonFunction.print_log("ERROR", "Memory 列表为空，检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共获取到 {len(mem_list)} 条内存，开始逐一校验")
        all_results = []
        mem_info_list = []
        for mem in mem_list:
            passed, mem_info = self._check_memory(mem)
            all_results.append(passed)
            mem_info_list.append(mem_info)

        final = "PASS" if all(all_results) else "FAIL"
        failed_cnt = sum(1 for r in all_results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"Memory 信息检查全部通过，共 {len(mem_list)} 条")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"Memory 信息检查失败，{failed_cnt}/{len(mem_list)} 条内存存在必要字段异常"
            )
        return final, mem_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, mem_info_list = self.memory_info_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in mem_info_list:
                f.write(
                    f'"{info["device_locator"]}",'
                    f'"{info["manufacturer"]}",'
                    f'"{info["serial_number"]}",'
                    f'"{info["part_number"]}",'
                    f'{info["capacity_mib"]},'
                    f'"{info["memory_device_type"]}",'
                    f'"{info["base_module_type"]}",'
                    f'{info["rank_count"]},'
                    f'{info["data_width_bits"]},'
                    f'{info["location_socket"]},'
                    f'{info["location_slot"]},'
                    f'{info["location_channel"]},'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in mem_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'MEM_{info["device_locator"]}', "value": info}
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
        checker = MemoryInfoCheck("MemoryInfoCheck")
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
