#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_003a_storage_controller_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

数据来源：get_storages() → List[Storage]，每个 Storage 嵌套 storage_controllers: List[StorageController]

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    MemberId、Name、Manufacturer、Model、SerialNumber、FirmwareVersion、SpeedGbps（> 0）、Status.Health
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    PartNumber、SupportedControllerProtocols、SupportedDeviceProtocols

CSV：每行一个 StorageController
JSON detail.cycle：每个控制器一条记录；summary：整体 PASS/FAIL
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

class StorageControllerInfoCheck(BmcTestBase):
    """Systems Storage 控制器信息检查

    用例编号：Redfish_Systems_003a
    检查项：每个 StorageController 的关键字段非空/合法性校验
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_003a_storage_controller_check.json"),
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

    # ── 单个 StorageController 校验 ───────────────────────────────────────────

    def _check_controller(self, ctrl, storage_id: str) -> tuple:
        ctrl_id = ctrl.member_id or ctrl.name or "unknown"
        label = f"{storage_id}/{ctrl_id}"
        all_pass = True

        # ══ 必要字段 ══

        # 1. MemberId
        if not ctrl.member_id or (isinstance(ctrl.member_id, str) and ctrl.member_id.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：MemberId 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：MemberId = {ctrl.member_id}")

        # 2. Name
        if not ctrl.name or (isinstance(ctrl.name, str) and ctrl.name.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：Name 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：Name = {ctrl.name}")

        # 3. Manufacturer
        if not ctrl.manufacturer or (isinstance(ctrl.manufacturer, str) and ctrl.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：Manufacturer = {ctrl.manufacturer}")

        # 4. Model
        if not ctrl.model or (isinstance(ctrl.model, str) and ctrl.model.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：Model 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：Model = {ctrl.model}")

        # 5. SerialNumber
        if not ctrl.serial_number or (isinstance(ctrl.serial_number, str) and ctrl.serial_number.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：SerialNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：SerialNumber = {ctrl.serial_number}")

        # 6. FirmwareVersion
        if not ctrl.firmware_version or (isinstance(ctrl.firmware_version, str) and ctrl.firmware_version.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：FirmwareVersion 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：FirmwareVersion = {ctrl.firmware_version}")

        # 7. SpeedGbps > 0
        if ctrl.speed_gbps is None:
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：SpeedGbps 为 None")
            all_pass = False
        elif not isinstance(ctrl.speed_gbps, (int, float)) or ctrl.speed_gbps <= 0:
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：SpeedGbps = {ctrl.speed_gbps}，应为正数")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：SpeedGbps = {ctrl.speed_gbps} Gbps")

        # 8. Status.Health 必须为 "OK"
        health = ctrl.status.health if ctrl.status else None
        if health is None:
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：Status.Health 为 None")
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log(
                "ERROR",
                f"[Ctrl] {label}：Status.Health = '{health}'，不在允许值 {VALID_HEALTH} 内"
            )
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log("ERROR", f"[Ctrl] {label}：Status.Health = '{health}'，控制器健康状态异常")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：Status.Health = {health}，健康正常")

        # ══ 非必要字段 ══

        # 9. PartNumber
        if not ctrl.part_number or (isinstance(ctrl.part_number, str) and ctrl.part_number.strip() == ""):
            CommonFunction.print_log("WARNING", f"[Ctrl] {label}：PartNumber 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：PartNumber = {ctrl.part_number}")

        # 10. SupportedControllerProtocols
        if not ctrl.supported_controller_protocols:
            CommonFunction.print_log("WARNING", f"[Ctrl] {label}：SupportedControllerProtocols 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：SupportedControllerProtocols = {ctrl.supported_controller_protocols}")

        # 11. SupportedDeviceProtocols
        if not ctrl.supported_device_protocols:
            CommonFunction.print_log("WARNING", f"[Ctrl] {label}：SupportedDeviceProtocols 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[Ctrl] {label}：SupportedDeviceProtocols = {ctrl.supported_device_protocols}")

        ctrl_info = {
            "storage_id":      storage_id,
            "member_id":       ctrl.member_id or "",
            "name":            ctrl.name or "",
            "manufacturer":    ctrl.manufacturer or "",
            "model":           ctrl.model or "",
            "serial_number":   ctrl.serial_number or "",
            "firmware_version": ctrl.firmware_version or "",
            "speed_gbps":      ctrl.speed_gbps,
            "health":          health or "",
            "check_result":    "PASS" if all_pass else "FAIL",
        }
        return all_pass, ctrl_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def storage_controller_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 Storage 数据")
        try:
            storage_list = self.client.get_storages()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Storage 数据失败：{str(e)}")
            return "FAIL", []

        if not storage_list:
            CommonFunction.print_log("ERROR", "Storage 列表为空，检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共获取到 {len(storage_list)} 个 Storage 资源，开始遍历控制器")
        all_results = []
        ctrl_info_list = []

        for storage in storage_list:
            storage_id = storage.id or "unknown"
            controllers = storage.storage_controllers or []
            if not controllers:
                CommonFunction.print_log("WARNING", f"[Storage] {storage_id}：StorageControllers 为空，跳过")
                continue
            CommonFunction.print_log("INFO", f"[Storage] {storage_id}：包含 {len(controllers)} 个控制器")
            for ctrl in controllers:
                passed, ctrl_info = self._check_controller(ctrl, storage_id)
                all_results.append(passed)
                ctrl_info_list.append(ctrl_info)

        if not all_results:
            CommonFunction.print_log("ERROR", "未找到任何 StorageController，检查失败")
            return "FAIL", []

        final = "PASS" if all(all_results) else "FAIL"
        failed_cnt = sum(1 for r in all_results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"StorageController 信息检查全部通过，共 {len(all_results)} 个")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"StorageController 信息检查失败，{failed_cnt}/{len(all_results)} 个控制器存在必要字段异常"
            )
        return final, ctrl_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, ctrl_info_list = self.storage_controller_info_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in ctrl_info_list:
                f.write(
                    f'"{info["storage_id"]}",'
                    f'"{info["member_id"]}",'
                    f'"{info["name"]}",'
                    f'"{info["manufacturer"]}",'
                    f'"{info["model"]}",'
                    f'"{info["serial_number"]}",'
                    f'"{info["firmware_version"]}",'
                    f'{info["speed_gbps"]},'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in ctrl_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'Ctrl_{info["storage_id"]}_{info["member_id"]}', "value": info}
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
        checker = StorageControllerInfoCheck("StorageControllerInfoCheck")
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
