#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/chassis_002_fans_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

Validation strategy:
  - Standalone Fan: Id, a usable SpeedPercent reading, and Status.Health.
  - Legacy embedded Fan: MemberId, Reading, ReadingUnits, MaxReadingRange,
    and Status.Health.
  - Optional schema-specific fields produce warnings only.

CSV：每行一个风扇的关键信息
JSON detail.cycle：每个风扇一条记录；summary：整体 PASS/FAIL
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

VALID_HEALTH       = {"OK", "Warning", "Critical"}
VALID_READING_UNITS = {"RPM", "Percent"}

class FansInfoCheck(BmcTestBase):
    """Chassis Fans 集合资源信息检查

    用例编号：Redfish_Chassis_002
    检查项：
      1. Fans 集合非空（数量 > 0）
      2. 每个风扇的字段校验（必要字段 / 非必要字段分层）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_002_fans_check.json"),
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

    # ── 单个风扇校验 ─────────────────────────────────────────────────────────

    def _check_single_fan(self, fan) -> tuple:
        """Dispatch validation according to the Fan resource schema."""
        if hasattr(fan, "speed_percent") and not hasattr(fan, "member_id"):
            return self._check_standalone_fan(fan)
        return self._check_legacy_fan(fan)

    def _check_standalone_fan(self, fan) -> tuple:
        """Validate a standalone Fan from ``ThermalSubsystem.Fans``."""
        resource_id = getattr(fan, "id", None)
        fan_id = (
            resource_id
            or getattr(fan, "name", None)
            or getattr(fan, "odata_id", None)
            or "unknown"
        )
        all_pass = True

        if not isinstance(resource_id, str) or not resource_id.strip():
            CommonFunction.print_log(
                "ERROR", f"[Fan] {fan_id}: Id is missing or empty"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}: Id = {resource_id}")

        speed = getattr(fan, "speed_percent", None)
        raw_percent = getattr(speed, "reading", None) if speed else None
        speed_rpm = getattr(speed, "speed_rpm", None) if speed else None
        valid_rpm = (
            isinstance(speed_rpm, (int, float))
            and not isinstance(speed_rpm, bool)
            and speed_rpm >= 0
        )
        valid_percent = (
            isinstance(raw_percent, (int, float))
            and not isinstance(raw_percent, bool)
            and 0 <= raw_percent <= 100
        )

        if valid_rpm:
            CommonFunction.print_log(
                "INFO", f"[Fan] {fan_id}: SpeedPercent.SpeedRPM = {speed_rpm}"
            )
        if valid_percent:
            CommonFunction.print_log(
                "INFO", f"[Fan] {fan_id}: SpeedPercent.Reading = {raw_percent}%"
            )
        elif raw_percent is not None and valid_rpm:
            CommonFunction.print_log(
                "WARNING",
                f"[Fan] {fan_id}: SpeedPercent.Reading = {raw_percent} is "
                "outside 0-100; using the advertised RPM reading",
            )

        if not valid_rpm and not valid_percent:
            CommonFunction.print_log(
                "ERROR",
                f"[Fan] {fan_id}: no usable SpeedRPM or SpeedPercent reading",
            )
            all_pass = False

        rated_speed_rpm = getattr(fan, "rated_speed_rpm", None)
        if rated_speed_rpm is None:
            CommonFunction.print_log(
                "WARNING", f"[Fan] {fan_id}: RatedSpeedRPM is not advertised"
            )
        elif not isinstance(rated_speed_rpm, (int, float)) or rated_speed_rpm <= 0:
            CommonFunction.print_log(
                "WARNING",
                f"[Fan] {fan_id}: RatedSpeedRPM = {rated_speed_rpm} is not positive",
            )
        else:
            CommonFunction.print_log(
                "INFO", f"[Fan] {fan_id}: RatedSpeedRPM = {rated_speed_rpm}"
            )

        health = getattr(getattr(fan, "status", None), "health", None)
        if health is None:
            CommonFunction.print_log(
                "ERROR", f"[Fan] {fan_id}: Status.Health is missing"
            )
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log(
                "ERROR", f"[Fan] {fan_id}: unsupported Status.Health = {health!r}"
            )
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log(
                "ERROR", f"[Fan] {fan_id}: unhealthy Status.Health = {health!r}"
            )
            all_pass = False
        else:
            CommonFunction.print_log(
                "INFO", f"[Fan] {fan_id}: Status.Health = OK"
            )

        physical_context = getattr(fan, "physical_context", None)
        if not physical_context:
            CommonFunction.print_log(
                "WARNING", f"[Fan] {fan_id}: PhysicalContext is not advertised"
            )
        else:
            CommonFunction.print_log(
                "INFO", f"[Fan] {fan_id}: PhysicalContext = {physical_context}"
            )

        reading = speed_rpm if valid_rpm else raw_percent
        reading_units = "RPM" if valid_rpm else "Percent"
        return all_pass, {
            "schema": "standalone",
            "fan_id": str(fan_id),
            "member_id": resource_id or "",
            "physical_context": physical_context or "",
            "reading": reading,
            "reading_units": reading_units,
            "max_reading_range": rated_speed_rpm,
            "speed_rpm": speed_rpm if valid_rpm else None,
            "speed_percent": raw_percent if valid_percent else None,
            "rated_speed_rpm": rated_speed_rpm,
            "health": health or "",
            "check_result": "PASS" if all_pass else "FAIL",
        }

    def _check_legacy_fan(self, fan) -> tuple:
        """
        :return: (all_pass: bool, fan_info: dict)
        """
        fan_id = fan.member_id or fan.name or fan.odata_id or "unknown"
        all_pass = True

        # ══ 必要字段 ══

        # 1. MemberId
        if not fan.member_id or (isinstance(fan.member_id, str) and fan.member_id.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Fan] {fan_id}：MemberId 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：MemberId = {fan.member_id}")

        # 2. Reading >= 0
        if fan.reading is None:
            CommonFunction.print_log("ERROR", f"[Fan] {fan_id}：Reading 为 None")
            all_pass = False
        elif not isinstance(fan.reading, (int, float)) or fan.reading < 0:
            CommonFunction.print_log(
                "ERROR", f"[Fan] {fan_id}：Reading = {fan.reading}，应为非负数"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：Reading = {fan.reading}")

        # 3. ReadingUnits
        if not fan.reading_units or (isinstance(fan.reading_units, str) and fan.reading_units.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Fan] {fan_id}：ReadingUnits 为空或 None")
            all_pass = False
        elif fan.reading_units not in VALID_READING_UNITS:
            CommonFunction.print_log(
                "ERROR",
                f"[Fan] {fan_id}：ReadingUnits = '{fan.reading_units}'，不在允许值 {VALID_READING_UNITS} 内"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：ReadingUnits = {fan.reading_units}")

        # 4. MaxReadingRange > 0
        if fan.max_reading_range is None:
            CommonFunction.print_log("ERROR", f"[Fan] {fan_id}：MaxReadingRange 为 None")
            all_pass = False
        elif not isinstance(fan.max_reading_range, (int, float)) or fan.max_reading_range <= 0:
            CommonFunction.print_log(
                "ERROR", f"[Fan] {fan_id}：MaxReadingRange = {fan.max_reading_range}，应为正数"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：MaxReadingRange = {fan.max_reading_range}")

        # 5. Status.Health 必须为 "OK"
        health = fan.status.health if fan.status else None
        if health is None:
            CommonFunction.print_log("ERROR", f"[Fan] {fan_id}：Status.Health 为 None")
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log(
                "ERROR",
                f"[Fan] {fan_id}：Status.Health = '{health}'，不在允许值 {VALID_HEALTH} 内"
            )
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log(
                "ERROR", f"[Fan] {fan_id}：Status.Health = '{health}'，风扇健康状态异常"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：Status.Health = {health}，健康正常")

        # ══ 非必要字段（只 WARNING，不计入整体结果）══

        # 6. PhysicalContext
        if not fan.physical_context or (isinstance(fan.physical_context, str) and fan.physical_context.strip() == ""):
            CommonFunction.print_log("WARNING", f"[Fan] {fan_id}：PhysicalContext 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：PhysicalContext = {fan.physical_context}")

        # 7. MinReadingRange
        if fan.min_reading_range is None:
            CommonFunction.print_log("WARNING", f"[Fan] {fan_id}：MinReadingRange 为 None")
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：MinReadingRange = {fan.min_reading_range}")

        # 8. UpperThresholdCritical
        if fan.upper_threshold_critical is None:
            CommonFunction.print_log("WARNING", f"[Fan] {fan_id}：UpperThresholdCritical 为 None")
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：UpperThresholdCritical = {fan.upper_threshold_critical}")

        # 9. UpperThresholdFatal
        if fan.upper_threshold_fatal is None:
            CommonFunction.print_log("WARNING", f"[Fan] {fan_id}：UpperThresholdFatal 为 None")
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：UpperThresholdFatal = {fan.upper_threshold_fatal}")

        # 10. LowerThresholdCritical
        if fan.lower_threshold_critical is None:
            CommonFunction.print_log("WARNING", f"[Fan] {fan_id}：LowerThresholdCritical 为 None")
        else:
            CommonFunction.print_log("INFO", f"[Fan] {fan_id}：LowerThresholdCritical = {fan.lower_threshold_critical}")

        fan_info = {
            "schema":           "legacy",
            "fan_id":           fan.member_id or fan.name or fan.odata_id or "unknown",
            "member_id":        fan.member_id or "",
            "physical_context": fan.physical_context or "",
            "reading":          fan.reading,
            "reading_units":    fan.reading_units or "",
            "max_reading_range": fan.max_reading_range,
            "speed_rpm":        fan.reading if fan.reading_units == "RPM" else None,
            "speed_percent":    fan.reading if fan.reading_units == "Percent" else None,
            "rated_speed_rpm":  fan.max_reading_range,
            "health":           health or "",
            "check_result":     "PASS" if all_pass else "FAIL",
        }
        return all_pass, fan_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def fans_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 Chassis Fans 集合资源")
        try:
            fan_list = self.client.get_fan()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Fans 集合失败：{str(e)}")
            return "FAIL", []

        if not fan_list:
            CommonFunction.print_log("ERROR", "Fans 集合为空（风扇数量为 0），检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共获取到 {len(fan_list)} 个风扇，开始逐一校验")
        results, fan_info_list = [], []
        for fan in fan_list:
            passed, fan_info = self._check_single_fan(fan)
            results.append(passed)
            fan_info_list.append(fan_info)

        final = "PASS" if all(results) else "FAIL"
        failed_cnt = sum(1 for r in results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"Fans 集合信息检查全部通过，共 {len(fan_list)} 个风扇")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"Fans 集合信息检查失败，{failed_cnt}/{len(fan_list)} 个风扇必要字段存在异常"
            )
        return final, fan_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        fan_check_result, fan_info_list = self.fans_info_check()
        self.command_check_result = fan_check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in fan_info_list:
                f.write(
                    f'"{info["member_id"]}",'
                    f'"{info["physical_context"]}",'
                    f'{info["reading"]},'
                    f'"{info["reading_units"]}",'
                    f'{info["max_reading_range"]},'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in fan_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": info["member_id"] or "fan", "value": info}
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
        checker = FansInfoCheck("FansInfoCheck")
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
