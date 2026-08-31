#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/chassis_005_mainboard_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    ProductName、Manufacturer、SerialNumber、PartNumber
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    ChassisPartNumber、BuildDate、PrettyName
  INFO — 打印所有字段供人工存档

CSV：记录主板关键信息（单行）
JSON detail.cycle：主板信息一条记录；summary：整体 PASS/FAIL
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class MainboardInfoCheck(BmcTestBase):
    """Chassis MainBoard FRU 信息检查

    用例编号：Redfish_Chassis_005
    检查项：MainBoard FRU 关键字段非空校验
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_005_mainboard_check.json"),
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

    # ── 主板信息校验 ─────────────────────────────────────────────────────────

    def _check_mainboard(self, mb) -> tuple:
        """
        :return: (all_pass: bool, mb_info: dict)
        """
        all_pass = True

        # ══ 必要字段 ══

        # 1. ProductName
        if not mb.product_name or (isinstance(mb.product_name, str) and mb.product_name.strip() == ""):
            CommonFunction.print_log("ERROR", "[MainBoard]：ProductName 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MainBoard]：ProductName = {mb.product_name}")

        # 2. Manufacturer
        if not mb.manufacturer or (isinstance(mb.manufacturer, str) and mb.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", "[MainBoard]：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MainBoard]：Manufacturer = {mb.manufacturer}")

        # 3. SerialNumber
        if not mb.serial_number or (isinstance(mb.serial_number, str) and mb.serial_number.strip() == ""):
            CommonFunction.print_log("ERROR", "[MainBoard]：SerialNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MainBoard]：SerialNumber = {mb.serial_number}")

        # 4. PartNumber
        if not mb.part_number or (isinstance(mb.part_number, str) and mb.part_number.strip() == ""):
            CommonFunction.print_log("ERROR", "[MainBoard]：PartNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[MainBoard]：PartNumber = {mb.part_number}")

        # ══ 非必要字段（只 WARNING，不计入整体结果）══

        # 5. ChassisPartNumber
        if not mb.chassis_part_number or (isinstance(mb.chassis_part_number, str) and mb.chassis_part_number.strip() == ""):
            CommonFunction.print_log("WARNING", "[MainBoard]：ChassisPartNumber 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[MainBoard]：ChassisPartNumber = {mb.chassis_part_number}")

        # 6. BuildDate
        if not mb.build_date or (isinstance(mb.build_date, str) and mb.build_date.strip() == ""):
            CommonFunction.print_log("WARNING", "[MainBoard]：BuildDate 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[MainBoard]：BuildDate = {mb.build_date}")

        # 7. PrettyName
        if not mb.pretty_name or (isinstance(mb.pretty_name, str) and mb.pretty_name.strip() == ""):
            CommonFunction.print_log("WARNING", "[MainBoard]：PrettyName 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[MainBoard]：PrettyName = {mb.pretty_name}")

        mb_info = {
            "product_name":        mb.product_name or "",
            "manufacturer":        mb.manufacturer or "",
            "serial_number":       mb.serial_number or "",
            "part_number":         mb.part_number or "",
            "chassis_part_number": mb.chassis_part_number or "",
            "build_date":          mb.build_date or "",
            "pretty_name":         mb.pretty_name or "",
            "check_result":        "PASS" if all_pass else "FAIL",
        }
        return all_pass, mb_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def mainboard_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 MainBoard FRU 信息")
        try:
            mb = self.client.get_mainboard()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 MainBoard 信息失败：{str(e)}")
            return "FAIL", {}

        if mb is None:
            CommonFunction.print_log("ERROR", "MainBoard 信息为 None，无法获取主板 FRU 数据")
            return "FAIL", {}

        all_pass, mb_info = self._check_mainboard(mb)
        final = "PASS" if all_pass else "FAIL"
        CommonFunction.print_log(
            "INFO" if final == "PASS" else "ERROR",
            f"MainBoard 信息检查完成，结果：{final}"
        )
        return final, mb_info

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, mb_info = self.mainboard_info_check()
        self.command_check_result = check_result

        if mb_info:
            with open(self.result_csv_path, "a", encoding="utf-8") as f:
                f.write(
                    f'"{mb_info["product_name"]}",'
                    f'"{mb_info["manufacturer"]}",'
                    f'"{mb_info["serial_number"]}",'
                    f'"{mb_info["part_number"]}",'
                    f'"{mb_info["chassis_part_number"]}",'
                    f'"{mb_info["build_date"]}",'
                    f'"{mb_info["pretty_name"]}",'
                    f'{mb_info["check_result"]}\n'
                )
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": "mainboard", "value": mb_info}
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
        checker = MainboardInfoCheck("MainboardInfoCheck")
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
