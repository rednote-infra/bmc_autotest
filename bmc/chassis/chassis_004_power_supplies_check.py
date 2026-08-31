#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/chassis_004_power_supplies_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    MemberId、Manufacturer、Model、SerialNumber、FirmwareVersion、PowerCapacityWatts（> 0）、Status.Health
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    PowerSupplyType、LineInputVoltageType、
    PowerOutputWatts（>= 0）、LastPowerOutputWatts（>= 0）
  INFO — 打印 Manufacturer / Model / SerialNumber / FirmwareVersion 供人工存档

CSV：每行一个 PSU 的关键信息
JSON detail.cycle：每个 PSU 一条记录；summary：整体 PASS/FAIL
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

VALID_HEALTH             = {"OK", "Warning", "Critical"}
VALID_POWER_SUPPLY_TYPE  = {"AC", "DC", "ACorDC", "DCRegulated"}
VALID_LINE_INPUT_VOLTAGE_TYPE = {
    "Unknown", "ACLowLine", "ACMidLine", "ACHighLine",
    "ACWideRange", "AC277V", "DCNeg48V", "DC380V", "DC480V",
}

class PowerSuppliesInfoCheck(BmcTestBase):
    """Chassis PowerSupplies 集合资源信息检查

    用例编号：Redfish_Chassis_004
    检查项：
      1. PowerSupplies 集合非空（数量 > 0）
      2. 每个 PSU 的字段校验（必要字段 / 非必要字段分层）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_004_power_supplies_check.json"),
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

    # ── 单个 PSU 校验 ─────────────────────────────────────────────────────────

    def _check_single_psu(self, psu) -> tuple:
        """
        :return: (all_pass: bool, psu_info: dict)
        """
        psu_id = psu.member_id or psu.name or psu.odata_id or "unknown"
        all_pass = True

        # ══ 必要字段 ══

        # 1. MemberId
        if not psu.member_id or (isinstance(psu.member_id, str) and psu.member_id.strip() == ""):
            CommonFunction.print_log("ERROR", f"[PSU] {psu_id}：MemberId 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：MemberId = {psu.member_id}")

        # 2. Manufacturer
        if not psu.manufacturer or (isinstance(psu.manufacturer, str) and psu.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", f"[PSU] {psu_id}：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：Manufacturer = {psu.manufacturer}")

        # 3. Model
        if not psu.model or (isinstance(psu.model, str) and psu.model.strip() == ""):
            CommonFunction.print_log("ERROR", f"[PSU] {psu_id}：Model 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：Model = {psu.model}")

        # 4. SerialNumber
        if not psu.serial_number or (isinstance(psu.serial_number, str) and psu.serial_number.strip() == ""):
            CommonFunction.print_log("ERROR", f"[PSU] {psu_id}：SerialNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：SerialNumber = {psu.serial_number}")

        # 5. FirmwareVersion
        if not psu.firmware_version or (isinstance(psu.firmware_version, str) and psu.firmware_version.strip() == ""):
            CommonFunction.print_log("ERROR", f"[PSU] {psu_id}：FirmwareVersion 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：FirmwareVersion = {psu.firmware_version}")

        # 6. PowerCapacityWatts > 0
        if psu.power_capacity_watts is None:
            CommonFunction.print_log("ERROR", f"[PSU] {psu_id}：PowerCapacityWatts 为 None")
            all_pass = False
        elif not isinstance(psu.power_capacity_watts, (int, float)) or psu.power_capacity_watts <= 0:
            CommonFunction.print_log(
                "ERROR",
                f"[PSU] {psu_id}：PowerCapacityWatts = {psu.power_capacity_watts}，应为正数"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：PowerCapacityWatts = {psu.power_capacity_watts} W")

        # 7. Status.Health 必须为 "OK"
        health = psu.status.health if psu.status else None
        if health is None:
            CommonFunction.print_log("ERROR", f"[PSU] {psu_id}：Status.Health 为 None")
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log(
                "ERROR",
                f"[PSU] {psu_id}：Status.Health = '{health}'，不在允许值 {VALID_HEALTH} 内"
            )
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log(
                "ERROR", f"[PSU] {psu_id}：Status.Health = '{health}'，PSU 健康状态异常"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：Status.Health = {health}，健康正常")

        # ══ 非必要字段（只 WARNING，不计入整体结果）══

        # 8. PowerSupplyType（枚举白名单）
        if not psu.power_supply_type or (isinstance(psu.power_supply_type, str) and psu.power_supply_type.strip() == ""):
            CommonFunction.print_log("WARNING", f"[PSU] {psu_id}：PowerSupplyType 为空或 None")
        elif psu.power_supply_type not in VALID_POWER_SUPPLY_TYPE:
            CommonFunction.print_log(
                "WARNING",
                f"[PSU] {psu_id}：PowerSupplyType = '{psu.power_supply_type}'，不在允许值 {VALID_POWER_SUPPLY_TYPE} 内"
            )
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：PowerSupplyType = {psu.power_supply_type}")

        # 9. LineInputVoltageType（枚举白名单）
        if not psu.line_input_voltage_type or (isinstance(psu.line_input_voltage_type, str) and psu.line_input_voltage_type.strip() == ""):
            CommonFunction.print_log("WARNING", f"[PSU] {psu_id}：LineInputVoltageType 为空或 None")
        elif psu.line_input_voltage_type not in VALID_LINE_INPUT_VOLTAGE_TYPE:
            CommonFunction.print_log(
                "WARNING",
                f"[PSU] {psu_id}：LineInputVoltageType = '{psu.line_input_voltage_type}'，不在允许值 {VALID_LINE_INPUT_VOLTAGE_TYPE} 内"
            )
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：LineInputVoltageType = {psu.line_input_voltage_type}")

        # 10. PowerOutputWatts >= 0
        if psu.power_output_watts is None:
            CommonFunction.print_log("WARNING", f"[PSU] {psu_id}：PowerOutputWatts 为 None")
        elif not isinstance(psu.power_output_watts, (int, float)) or psu.power_output_watts < 0:
            CommonFunction.print_log(
                "WARNING",
                f"[PSU] {psu_id}：PowerOutputWatts = {psu.power_output_watts}，应为非负数"
            )
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：PowerOutputWatts = {psu.power_output_watts} W")

        # 11. LastPowerOutputWatts >= 0
        if psu.last_power_output_watts is None:
            CommonFunction.print_log("WARNING", f"[PSU] {psu_id}：LastPowerOutputWatts 为 None")
        elif not isinstance(psu.last_power_output_watts, (int, float)) or psu.last_power_output_watts < 0:
            CommonFunction.print_log(
                "WARNING",
                f"[PSU] {psu_id}：LastPowerOutputWatts = {psu.last_power_output_watts}，应为非负数"
            )
        else:
            CommonFunction.print_log("INFO", f"[PSU] {psu_id}：LastPowerOutputWatts = {psu.last_power_output_watts} W")

        psu_info = {
            "member_id":           psu.member_id or "",
            "manufacturer":        psu.manufacturer or "",
            "model":               psu.model or "",
            "serial_number":       psu.serial_number or "",
            "firmware_version":    psu.firmware_version or "",
            "power_capacity_watts": psu.power_capacity_watts,
            "power_supply_type":   psu.power_supply_type or "",
            "health":              health or "",
            "check_result":        "PASS" if all_pass else "FAIL",
        }
        return all_pass, psu_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def power_supplies_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 Chassis PowerSupplies 集合资源")
        try:
            psu_list = self.client.get_power_supplies()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 PowerSupplies 集合失败：{str(e)}")
            return "FAIL", []

        if not psu_list:
            CommonFunction.print_log("ERROR", "PowerSupplies 集合为空（PSU 数量为 0），检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共获取到 {len(psu_list)} 个 PSU，开始逐一校验")
        results, psu_info_list = [], []
        for psu in psu_list:
            passed, psu_info = self._check_single_psu(psu)
            results.append(passed)
            psu_info_list.append(psu_info)

        final = "PASS" if all(results) else "FAIL"
        failed_cnt = sum(1 for r in results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"PowerSupplies 集合信息检查全部通过，共 {len(psu_list)} 个 PSU")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"PowerSupplies 集合信息检查失败，{failed_cnt}/{len(psu_list)} 个 PSU 必要字段存在异常"
            )
        return final, psu_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, psu_info_list = self.power_supplies_info_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in psu_info_list:
                f.write(
                    f'"{info["member_id"]}",'
                    f'"{info["manufacturer"]}",'
                    f'"{info["model"]}",'
                    f'"{info["serial_number"]}",'
                    f'"{info["firmware_version"]}",'
                    f'{info["power_capacity_watts"]},'
                    f'"{info["power_supply_type"]}",'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in psu_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": info["member_id"] or "psu", "value": info}
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
        checker = PowerSuppliesInfoCheck("PowerSuppliesInfoCheck")
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
