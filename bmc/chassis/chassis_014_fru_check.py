#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/chassis_014_fru_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

数据来源：get_system_fru() → Fru
  Fru.product:  FruProduct（Manufacturer/Name/PartNumber/SerialNumber/Version）
  Fru.chassis:  FruChassis（ChassisPartNumber/ChassisSerialNumber）
  Fru.board:    MainBoard（参考 chassis_005，此处仅作非必要字段存档）

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    Product.Manufacturer、Product.Name、Product.PartNumber、Product.SerialNumber、Product.Version
    Chassis.ChassisPartNumber、Chassis.ChassisSerialNumber
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    Board.ProductName、Board.PartNumber、Board.SerialNumber、Board.Manufacturer、Board.BuildDate

CSV：单行（整个 FRU 为一条记录）
JSON detail.cycle：FRU 各节信息；summary：整体 PASS/FAIL
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class FruInfoCheck(BmcTestBase):
    """Chassis FRU 集合资源信息检查

    用例编号：Redfish_Chassis_014
    检查项：FRU Product/Chassis 关键字段非空校验；Board 作为非必要字段存档
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_014_fru_check.json"),
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

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def fru_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 FRU 数据")
        try:
            fru = self.client.get_system_fru()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 FRU 数据失败：{str(e)}")
            return "FAIL", {}

        if fru is None:
            CommonFunction.print_log("ERROR", "FRU 数据为 None，当前 BMC 可能不支持该接口")
            return "FAIL", {}

        all_pass = True

        # ══ Product 必要字段 ══
        product = fru.product
        if product is None:
            CommonFunction.print_log("ERROR", "FRU.Product 节点为 None，无法校验产品信息")
            all_pass = False
            p_manufacturer = p_name = p_part_number = p_serial_number = p_version = ""
        else:
            # 1. Product.Manufacturer
            p_manufacturer = product.manufacturer or ""
            if not p_manufacturer.strip():
                CommonFunction.print_log("ERROR", "FRU.Product.Manufacturer 为空或 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"FRU.Product.Manufacturer = {p_manufacturer}")

            # 2. Product.Name
            p_name = product.name or ""
            if not p_name.strip():
                CommonFunction.print_log("ERROR", "FRU.Product.Name 为空或 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"FRU.Product.Name = {p_name}")

            # 3. Product.PartNumber
            p_part_number = product.part_number or ""
            if not p_part_number.strip():
                CommonFunction.print_log("ERROR", "FRU.Product.PartNumber 为空或 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"FRU.Product.PartNumber = {p_part_number}")

            # 4. Product.SerialNumber
            p_serial_number = product.serial_number or ""
            if not p_serial_number.strip():
                CommonFunction.print_log("ERROR", "FRU.Product.SerialNumber 为空或 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"FRU.Product.SerialNumber = {p_serial_number}")

            # 5. Product.Version
            p_version = product.version or ""
            if not p_version.strip():
                CommonFunction.print_log("ERROR", "FRU.Product.Version 为空或 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"FRU.Product.Version = {p_version}")

        # ══ Chassis 必要字段 ══
        chassis = fru.chassis
        if chassis is None:
            CommonFunction.print_log("ERROR", "FRU.Chassis 节点为 None，无法校验机箱信息")
            all_pass = False
            c_part_number = c_serial_number = ""
        else:
            # 6. Chassis.ChassisPartNumber
            c_part_number = chassis.chassis_part_number or ""
            if not c_part_number.strip():
                CommonFunction.print_log("ERROR", "FRU.Chassis.ChassisPartNumber 为空或 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"FRU.Chassis.ChassisPartNumber = {c_part_number}")

            # 7. Chassis.ChassisSerialNumber
            c_serial_number = chassis.chassis_serial_number or ""
            if not c_serial_number.strip():
                CommonFunction.print_log("ERROR", "FRU.Chassis.ChassisSerialNumber 为空或 None")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"FRU.Chassis.ChassisSerialNumber = {c_serial_number}")

        # ══ Board 非必要字段 ══
        board = fru.board
        if board is None:
            CommonFunction.print_log("WARNING", "FRU.Board（MainBoard）节点为 None")
        else:
            for fname, fval in [
                ("ProductName",  board.product_name),
                ("PartNumber",   board.part_number),
                ("SerialNumber", board.serial_number),
                ("Manufacturer", board.manufacturer),
                ("BuildDate",    board.build_date),
            ]:
                val = fval or ""
                if not (isinstance(val, str) and val.strip()):
                    CommonFunction.print_log("WARNING", f"FRU.Board.{fname} 为空或 None")
                else:
                    CommonFunction.print_log("INFO", f"FRU.Board.{fname} = {val}")

        fru_info = {
            "product_manufacturer":    p_manufacturer,
            "product_name":            p_name,
            "product_part_number":     p_part_number,
            "product_serial_number":   p_serial_number,
            "product_version":         p_version,
            "chassis_part_number":     c_part_number,
            "chassis_serial_number":   c_serial_number,
            "check_result":            "PASS" if all_pass else "FAIL",
        }

        final = "PASS" if all_pass else "FAIL"
        if final == "PASS":
            CommonFunction.print_log("INFO", "FRU 信息检查通过")
        else:
            CommonFunction.print_log("ERROR", "FRU 信息检查失败，存在必要字段异常")
        return final, fru_info

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, fru_info = self.fru_info_check()
        self.command_check_result = check_result

        if fru_info:
            with open(self.result_csv_path, "a", encoding="utf-8") as f:
                f.write(
                    f'"{fru_info["product_manufacturer"]}",'
                    f'"{fru_info["product_name"]}",'
                    f'"{fru_info["product_part_number"]}",'
                    f'"{fru_info["product_serial_number"]}",'
                    f'"{fru_info["product_version"]}",'
                    f'"{fru_info["chassis_part_number"]}",'
                    f'"{fru_info["chassis_serial_number"]}",'
                    f'{fru_info["check_result"]}\n'
                )

            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": "FRU_Info", "value": fru_info}
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
        checker = FruInfoCheck("FruInfoCheck")
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
