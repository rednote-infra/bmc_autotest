#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/chassis_013_pcie_devices_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

数据来源：
  get_pcie_devices(chassis_id)  → 获取 PCIeDevices 集合（List[PCIeDevice]），遍历所有设备
  get_pcie_device(odata_id)     → 按 @odata.id 获取单个 PCIeDevice 详情（本脚本通过集合已覆盖所有字段，
                                   如需深入单设备扩展信息可按需调用）

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    Manufacturer、Model、SerialNumber、FirmwareVersion、Status.Health
    PCIeInterface.LanesInUse（> 0）、PCIeInterface.MaxLanes（> 0）
    PCIeInterface.PCIeType、PCIeInterface.MaxPCIeType
    LanesInUse 必须严格等于 MaxLanes（降 lane 视为故障）
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    PartNumber、CardModel

CSV：每行一个 PCIeDevice
JSON detail.cycle：每个设备一条记录；summary：整体 PASS/FAIL
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

class PCIeDevicesInfoCheck(BmcTestBase):
    """Chassis PCIeDevices 集合资源信息检查

    用例编号：Redfish_Chassis_013
    检查项：PCIeDevice 关键字段非空/合法性校验，PCIeInterface 链路速率校验
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_013_pcie_devices_check.json"),
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

    # ── 单个 PCIeDevice 校验 ─────────────────────────────────────────────────

    def _check_device(self, device) -> tuple:
        device_id = device.id or device.name or "unknown"
        label = f"PCIeDevice/{device_id}"
        all_pass = True

        # ══ 必要字段 ══

        # 1. Manufacturer
        if not device.manufacturer or (isinstance(device.manufacturer, str) and device.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", f"[{label}]：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：Manufacturer = {device.manufacturer}")

        # 2. Model
        if not device.model or (isinstance(device.model, str) and device.model.strip() == ""):
            CommonFunction.print_log("ERROR", f"[{label}]：Model 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：Model = {device.model}")

        # 3. SerialNumber
        if not device.serial_number or (isinstance(device.serial_number, str) and device.serial_number.strip() == ""):
            CommonFunction.print_log("ERROR", f"[{label}]：SerialNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：SerialNumber = {device.serial_number}")

        # 4. FirmwareVersion
        if not device.firmware_version or (isinstance(device.firmware_version, str) and device.firmware_version.strip() == ""):
            CommonFunction.print_log("ERROR", f"[{label}]：FirmwareVersion 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：FirmwareVersion = {device.firmware_version}")

        # 5. Status.Health 必须为 "OK"
        health = device.status.health if device.status else None
        if health is None:
            CommonFunction.print_log("ERROR", f"[{label}]：Status.Health 为 None")
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log("ERROR", f"[{label}]：Status.Health = '{health}'，不在允许值 {VALID_HEALTH} 内")
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log("ERROR", f"[{label}]：Status.Health = '{health}'，设备健康状态异常")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：Status.Health = {health}，健康正常")

        # 6~10. PCIeInterface 字段
        pcie_iface = device.pcie_interface
        lanes_in_use = None
        max_lanes = None
        pcie_type = None
        max_pcie_type = None

        if pcie_iface is None:
            CommonFunction.print_log("ERROR", f"[{label}]：PCIeInterface 为 None，无法校验链路参数")
            all_pass = False
        else:
            lanes_in_use = pcie_iface.lanes_in_use
            max_lanes = pcie_iface.max_lanes
            pcie_type = pcie_iface.pcie_type
            max_pcie_type = pcie_iface.max_pcie_type

            # 6. LanesInUse > 0
            if lanes_in_use is None:
                CommonFunction.print_log("ERROR", f"[{label}]：PCIeInterface.LanesInUse 为 None")
                all_pass = False
            elif not isinstance(lanes_in_use, int) or lanes_in_use <= 0:
                CommonFunction.print_log("ERROR", f"[{label}]：PCIeInterface.LanesInUse = {lanes_in_use}，应为正整数")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"[{label}]：PCIeInterface.LanesInUse = {lanes_in_use}")

            # 7. MaxLanes > 0
            if max_lanes is None:
                CommonFunction.print_log("ERROR", f"[{label}]：PCIeInterface.MaxLanes 为 None")
                all_pass = False
            elif not isinstance(max_lanes, int) or max_lanes <= 0:
                CommonFunction.print_log("ERROR", f"[{label}]：PCIeInterface.MaxLanes = {max_lanes}，应为正整数")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"[{label}]：PCIeInterface.MaxLanes = {max_lanes}")

            # 8. LanesInUse 必须严格等于 MaxLanes（降 lane 视为故障）
            if lanes_in_use is not None and max_lanes is not None:
                if isinstance(lanes_in_use, int) and isinstance(max_lanes, int):
                    if lanes_in_use != max_lanes:
                        CommonFunction.print_log(
                            "ERROR",
                            f"[{label}]：PCIeInterface.LanesInUse({lanes_in_use}) != MaxLanes({max_lanes})，"
                            f"PCIe 链路降 lane，疑似故障"
                        )
                        all_pass = False
                    else:
                        CommonFunction.print_log("INFO", f"[{label}]：PCIe 链路 lane 数正常（{lanes_in_use}x）")

            # 9. PCIeType
            if not pcie_type or (isinstance(pcie_type, str) and pcie_type.strip() == ""):
                CommonFunction.print_log("ERROR", f"[{label}]：PCIeInterface.PCIeType 为空或 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"[{label}]：PCIeInterface.PCIeType = {pcie_type}")

            # 10. MaxPCIeType
            if not max_pcie_type or (isinstance(max_pcie_type, str) and max_pcie_type.strip() == ""):
                CommonFunction.print_log("ERROR", f"[{label}]：PCIeInterface.MaxPCIeType 为空或 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"[{label}]：PCIeInterface.MaxPCIeType = {max_pcie_type}")

        # ══ 非必要字段 ══

        # 11. PartNumber
        if not device.part_number or (isinstance(device.part_number, str) and device.part_number.strip() == ""):
            CommonFunction.print_log("WARNING", f"[{label}]：PartNumber 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[{label}]：PartNumber = {device.part_number}")

        # 12. CardModel
        if not device.card_model or (isinstance(device.card_model, str) and device.card_model.strip() == ""):
            CommonFunction.print_log("WARNING", f"[{label}]：CardModel 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[{label}]：CardModel = {device.card_model}")

        device_info = {
            "device_id":       device_id,
            "manufacturer":    device.manufacturer or "",
            "model":           device.model or "",
            "serial_number":   device.serial_number or "",
            "firmware_version": device.firmware_version or "",
            "lanes_in_use":    lanes_in_use,
            "max_lanes":       max_lanes,
            "pcie_type":       pcie_type or "",
            "max_pcie_type":   max_pcie_type or "",
            "health":          health or "",
            "check_result":    "PASS" if all_pass else "FAIL",
        }
        return all_pass, device_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def pcie_devices_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 PCIeDevices 数据")
        try:
            device_list = self.client.get_pcie_devices()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 PCIeDevices 数据失败：{str(e)}")
            return "FAIL", []

        if not device_list:
            CommonFunction.print_log("ERROR", "PCIeDevices 列表为空，检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共获取到 {len(device_list)} 个 PCIeDevice，开始逐一校验")
        all_results = []
        device_info_list = []

        for device in device_list:
            passed, device_info = self._check_device(device)
            all_results.append(passed)
            device_info_list.append(device_info)

        final = "PASS" if all(all_results) else "FAIL"
        failed_cnt = sum(1 for r in all_results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"PCIeDevices 信息检查全部通过，共 {len(all_results)} 个")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"PCIeDevices 信息检查失败，{failed_cnt}/{len(all_results)} 个设备存在必要字段异常"
            )
        return final, device_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, device_info_list = self.pcie_devices_info_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in device_info_list:
                f.write(
                    f'"{info["device_id"]}",'
                    f'"{info["manufacturer"]}",'
                    f'"{info["model"]}",'
                    f'"{info["serial_number"]}",'
                    f'"{info["firmware_version"]}",'
                    f'{info["lanes_in_use"]},'
                    f'{info["max_lanes"]},'
                    f'"{info["pcie_type"]}",'
                    f'"{info["max_pcie_type"]}",'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in device_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'PCIeDevice_{info["device_id"]}', "value": info}
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
        checker = PCIeDevicesInfoCheck("PCIeDevicesInfoCheck")
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
