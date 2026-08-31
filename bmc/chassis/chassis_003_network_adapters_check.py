#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/chassis_003_network_adapters_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    Manufacturer、Model、SerialNumber、Status.Health、
    Controllers[].FirmwarePackageVersion
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    PartNumber、Controllers[].ControllerCapabilities.NetworkPortCount、
    Controllers[].ControllerCapabilities.NetworkDeviceFunctionCount
  INFO — 打印 Manufacturer / Model / SerialNumber / PartNumber 供人工存档

CSV：每行一个网卡的关键信息
JSON detail.cycle：每个网卡一条记录；summary：整体 PASS/FAIL
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

class NetworkAdaptersInfoCheck(BmcTestBase):
    """Chassis NetworkAdapters 集合资源信息检查

    用例编号：Redfish_Chassis_003
    检查项：
      1. NetworkAdapters 集合非空（数量 > 0）
      2. 每块网卡的字段校验（必要字段 / 非必要字段分层）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_003_network_adapters_check.json"),
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

    # ── 单块网卡校验 ─────────────────────────────────────────────────────────

    def _check_single_adapter(self, adapter) -> tuple:
        """
        :return: (all_pass: bool, adapter_info: dict)
        """
        adapter_name = adapter.name or adapter.odata_id or "unknown"
        all_pass = True

        # ══ 必要字段 ══

        # 1. Manufacturer
        if not adapter.manufacturer or (isinstance(adapter.manufacturer, str) and adapter.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", f"[NIC] {adapter_name}：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[NIC] {adapter_name}：Manufacturer = {adapter.manufacturer}")

        # 2. Model
        if not adapter.model or (isinstance(adapter.model, str) and adapter.model.strip() == ""):
            CommonFunction.print_log("ERROR", f"[NIC] {adapter_name}：Model 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[NIC] {adapter_name}：Model = {adapter.model}")

        # 3. SerialNumber
        if not adapter.serial_number or (isinstance(adapter.serial_number, str) and adapter.serial_number.strip() == ""):
            CommonFunction.print_log("ERROR", f"[NIC] {adapter_name}：SerialNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[NIC] {adapter_name}：SerialNumber = {adapter.serial_number}")

        # 4. Status.Health 必须为 "OK"
        health = adapter.status.health if adapter.status else None
        if health is None:
            CommonFunction.print_log("ERROR", f"[NIC] {adapter_name}：Status.Health 为 None")
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log(
                "ERROR",
                f"[NIC] {adapter_name}：Status.Health = '{health}'，不在允许值 {VALID_HEALTH} 内"
            )
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log(
                "ERROR", f"[NIC] {adapter_name}：Status.Health = '{health}'，网卡健康状态异常"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[NIC] {adapter_name}：Status.Health = {health}，健康正常")

        # 5. Controllers[].FirmwarePackageVersion（必要）
        if not adapter.controllers or len(adapter.controllers) == 0:
            CommonFunction.print_log("ERROR", f"[NIC] {adapter_name}：Controllers 为空，无法获取 FirmwarePackageVersion")
            all_pass = False
        else:
            for idx, ctrl in enumerate(adapter.controllers):
                ctrl_label = f"Controllers[{idx}]"
                fw = ctrl.firmware_package_version
                if not fw or (isinstance(fw, str) and fw.strip() == ""):
                    CommonFunction.print_log(
                        "ERROR", f"[NIC] {adapter_name}：{ctrl_label}.FirmwarePackageVersion 为空或 None"
                    )
                    all_pass = False
                else:
                    CommonFunction.print_log(
                        "INFO", f"[NIC] {adapter_name}：{ctrl_label}.FirmwarePackageVersion = {fw}"
                    )

        # ══ 非必要字段（只 WARNING，不计入整体结果）══

        # 6. PartNumber
        if not adapter.part_number or (isinstance(adapter.part_number, str) and adapter.part_number.strip() == ""):
            CommonFunction.print_log("WARNING", f"[NIC] {adapter_name}：PartNumber 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[NIC] {adapter_name}：PartNumber = {adapter.part_number}")

        # 7. Controllers[].ControllerCapabilities
        if adapter.controllers:
            for idx, ctrl in enumerate(adapter.controllers):
                ctrl_label = f"Controllers[{idx}]"
                caps = ctrl.controller_capabilities if ctrl.controller_capabilities else None
                if caps is None:
                    CommonFunction.print_log(
                        "WARNING", f"[NIC] {adapter_name}：{ctrl_label}.ControllerCapabilities 为 None"
                    )
                else:
                    # NetworkPortCount
                    port_cnt = caps.network_port_count
                    if port_cnt is None:
                        CommonFunction.print_log(
                            "WARNING",
                            f"[NIC] {adapter_name}：{ctrl_label}.ControllerCapabilities.NetworkPortCount 为 None"
                        )
                    elif not isinstance(port_cnt, int) or port_cnt <= 0:
                        CommonFunction.print_log(
                            "WARNING",
                            f"[NIC] {adapter_name}：{ctrl_label}.ControllerCapabilities.NetworkPortCount = {port_cnt}，应为正整数"
                        )
                    else:
                        CommonFunction.print_log(
                            "INFO",
                            f"[NIC] {adapter_name}：{ctrl_label}.ControllerCapabilities.NetworkPortCount = {port_cnt}"
                        )

                    # NetworkDeviceFunctionCount
                    func_cnt = caps.network_device_function_count
                    if func_cnt is None:
                        CommonFunction.print_log(
                            "WARNING",
                            f"[NIC] {adapter_name}：{ctrl_label}.ControllerCapabilities.NetworkDeviceFunctionCount 为 None"
                        )
                    elif not isinstance(func_cnt, int) or func_cnt <= 0:
                        CommonFunction.print_log(
                            "WARNING",
                            f"[NIC] {adapter_name}：{ctrl_label}.ControllerCapabilities.NetworkDeviceFunctionCount = {func_cnt}，应为正整数"
                        )
                    else:
                        CommonFunction.print_log(
                            "INFO",
                            f"[NIC] {adapter_name}：{ctrl_label}.ControllerCapabilities.NetworkDeviceFunctionCount = {func_cnt}"
                        )

        adapter_info = {
            "name":          adapter_name,
            "manufacturer":  adapter.manufacturer or "",
            "model":         adapter.model or "",
            "serial_number": adapter.serial_number or "",
            "part_number":   adapter.part_number or "",
            "health":        health or "",
            "check_result":  "PASS" if all_pass else "FAIL",
        }
        return all_pass, adapter_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def network_adapters_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 Chassis NetworkAdapters 集合资源")
        try:
            adapter_list = self.client.get_network_adapters()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 NetworkAdapters 集合失败：{str(e)}")
            return "FAIL", []

        if not adapter_list:
            CommonFunction.print_log("ERROR", "NetworkAdapters 集合为空（网卡数量为 0），检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共获取到 {len(adapter_list)} 块网卡，开始逐一校验")
        results, adapter_info_list = [], []
        for adapter in adapter_list:
            passed, adapter_info = self._check_single_adapter(adapter)
            results.append(passed)
            adapter_info_list.append(adapter_info)

        final = "PASS" if all(results) else "FAIL"
        failed_cnt = sum(1 for r in results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"NetworkAdapters 集合信息检查全部通过，共 {len(adapter_list)} 块网卡")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"NetworkAdapters 集合信息检查失败，{failed_cnt}/{len(adapter_list)} 块网卡必要字段存在异常"
            )
        return final, adapter_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, adapter_info_list = self.network_adapters_info_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in adapter_info_list:
                f.write(
                    f'"{info["name"]}",'
                    f'"{info["manufacturer"]}",'
                    f'"{info["model"]}",'
                    f'"{info["serial_number"]}",'
                    f'"{info["part_number"]}",'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in adapter_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": info["name"], "value": info}
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
        checker = NetworkAdaptersInfoCheck("NetworkAdaptersInfoCheck")
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
