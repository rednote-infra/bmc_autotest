#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/chassis_007b_nvme_led_negative.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/22: 新增
2026/06/23: 读取 Protocol/MediaType 改用 v1.1.0 get_drives() 模型字段；
            反向写入仍用 client.patch() 直达 BMC（绕过 SDK 本地校验，确保真正测到 BMC 服务端拒绝能力）

数据来源：get_drives() → List[Drive]（含 protocol/media_type/indicator_led，v1.1.0 新增字段）
          PATCH <drive.odata_id> {"IndicatorLED": "<invalid_value>"}（反向：必须直达 BMC）

校验策略（反向测试）：
  取第一块支持 IndicatorLED 的 Drive，发送各类非法值，
  验证 BMC 全部返回 4xx 拒绝请求。

  非法场景：
    1. 完全无效字符串
    2. 大小写错误（lit / off 而非 Lit / Off）
    3. 空字符串
    4. 数字类型
    5. null 值

  PASS 条件：所有非法请求均被 BMC 以非 2xx 拒绝
  FAIL 条件：任意非法请求被 BMC 接受（返回 2xx）
"""

import os
import time
import sys
import traceback
from pydantic import ValidationError

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

NEGATIVE_CASES = [
    ("invalid_string",  {"IndicatorLED": "InvalidValue"},  "完全无效字符串"),
    ("wrong_case_lit",  {"IndicatorLED": "lit"},           "合法值小写（lit 而非 Lit）"),
    ("wrong_case_off",  {"IndicatorLED": "off"},           "合法值小写（off 而非 Off）"),
    ("empty_string",    {"IndicatorLED": ""},              "空字符串"),
    ("numeric_type",    {"IndicatorLED": 1},               "数字类型（应为字符串）"),
    ("null_value",      {"IndicatorLED": None},            "null 值"),
]

class ChassisNVMELEDNegativeCheck(BmcTestBase):
    """Chassis NVMe 指示灯操作测试（反向）

    用例编号：Redfish_Chassis_007b
    检查项：对第一块支持 IndicatorLED 的 Drive 发送非法值，验证 BMC 全部返回非 2xx
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_007b_nvme_led_negative.json"),
        )
    def _init_sdk_client(self):
        CommonFunction.print_log("DEBUG", "开始初始化 Redfish SDK 客户端")
        self.client = RedfishClient(
            host=self.BMC_IP,
            username=self.USERNAME,
            password=self.PASSWORD,
        )
        CommonFunction.print_log("DEBUG", "Redfish SDK 客户端初始化完成")

    # ── 找第一块支持 IndicatorLED 的 Drive ───────────────────────────────────

    def _find_target_drive(self):
        """
        返回第一块 NVMe SSD 且支持 IndicatorLED 的 Drive 的 (odata_id, name)。
        非 NVMe SSD 盘不在本用例范围，跳过。
        找不到返回 (None, None)。
        """
        try:
            drives = self.client.get_drives()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Drive 列表失败（Redfish 接口异常）：{str(e)}")
            return None, None
        except ValidationError as e:
            # BMC 端 Bug：返回数据不符合 Redfish 规范（如 Id 为整数而非字符串），
            # SDK Pydantic 模型校验失败，无法解析响应，如实记录 FAIL。
            CommonFunction.print_log(
                "ERROR",
                f"获取 Drive 列表失败（BMC 返回数据不符合 Redfish 规范，SDK 解析错误）：{str(e)}"
            )
            return None, None
        except Exception as e:
            CommonFunction.print_log(
                "ERROR",
                f"获取 Drive 列表时发生未预期异常：{type(e).__name__}: {str(e)}"
            )
            return None, None

        if not drives:
            CommonFunction.print_log("ERROR", "未找到任何 Drive")
            return None, None

        for drive in drives:
            drive_odata_id = drive.odata_id
            drive_name = drive.name or drive_odata_id or "unknown"
            # v1.1.0：Drive 模型已含 protocol/media_type/indicator_led，直接取用
            protocol   = (drive.protocol   or "").upper()
            media_type = (drive.media_type or "").upper()

            # 只选 NVMe SSD（其余不在本用例范围）
            if not (protocol == "NVME" and media_type == "SSD"):
                CommonFunction.print_log(
                    "INFO",
                    f"[{drive_name}] Protocol={protocol} MediaType={media_type}，非 NVMe SSD，跳过"
                )
                continue

            if drive.indicator_led is None:
                CommonFunction.print_log(
                    "ERROR",
                    f"[{drive_name}] NVMe SSD 缺少 IndicatorLED 字段，功能缺陷，返回 FAIL"
                )
                return "MISSING", drive_name

            CommonFunction.print_log(
                "INFO",
                f"选用 Drive：{drive_name}（Protocol={protocol} MediaType={media_type}，{drive_odata_id}）"
            )
            return drive_odata_id, drive_name

        CommonFunction.print_log("ERROR", "未找到任何 NVMe SSD Drive")
        return None, None

    # ── 单条反向用例执行 ───────────────────────────────────────────────────

    def _run_negative_case(self, drive_odata_id: str, drive_name: str,
                           name: str, body: dict, desc: str) -> dict:
        step_result = {
            "name": name,
            "description": desc,
            "body": str(body),
            "rejected": False,
            "result": "FAIL",
        }

        # 注意：反向测试必须验证 BMC 服务端对非法值的拒绝能力，因此刻意使用
        # client.patch() 将原始非法 body 直达 BMC；不可改用 set_drive_indicator_led()，
        # 否则非法值会被 SDK 本地校验拦截（抛 RedfishValidationError），根本到不了 BMC，
        # 造成假 PASS、掩盖 BMC 缺陷。
        CommonFunction.print_log("INFO", f"[{name}] 发送非法 PATCH 到 {drive_name}：{desc}，body={body}")
        try:
            self.client.patch(drive_odata_id, body)
            CommonFunction.print_log(
                "ERROR",
                f"[{name}] BMC 意外接受了非法请求（应返回 4xx）"
            )
        except RedfishException as e:
            step_result["rejected"] = True
            step_result["result"] = "PASS"
            CommonFunction.print_log("INFO", f"[{name}] BMC 正确拒绝：{str(e)[:120]}")

        return step_result

    # ── 主检查流程 ─────────────────────────────────────────────────────────

    def nvme_led_negative_check(self) -> tuple:
        result_list = []
        overall = "PASS"

        drive_odata_id, drive_name = self._find_target_drive()
        if drive_odata_id is None:
            CommonFunction.print_log("ERROR", "未找到任何 NVMe SSD Drive，测试无效")
            return "FAIL", []
        if drive_odata_id == "MISSING":
            CommonFunction.print_log("ERROR", f"[{drive_name}] NVMe SSD 缺少 IndicatorLED 字段，功能缺陷")
            return "FAIL", [{"metrics": drive_name, "value": "FAIL", "description": "NVMe SSD 缺少 IndicatorLED 字段"}]

        for name, body, desc in NEGATIVE_CASES:
            step = self._run_negative_case(drive_odata_id, drive_name, name, body, desc)
            result_list.append(step)
            if step["result"] == "FAIL":
                overall = "FAIL"

        # 确保测试结束后指示灯为 Off（合法值，用 v1.1.0 类型化写接口）
        try:
            self.client.set_drive_indicator_led(drive_odata_id, "Off")
            CommonFunction.print_log("INFO", "测试结束，已将 IndicatorLED 重置为 Off")
        except RedfishException as e:
            CommonFunction.print_log("WARNING", f"重置 IndicatorLED 失败：{str(e)}")

        return overall, result_list

    # ── 结果写入 ───────────────────────────────────────────────────────────

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
                CommonFunction.print_log("DEBUG", "Redfish SDK 客户端已关闭")
            except Exception:
                pass
            self.client = None

    def run_test(self):
        CommonFunction.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        CommonFunction.print_log("INFO", "测试开始")
        try:
            check_result, result_list = self.nvme_led_negative_check()
        except Exception as e:
            CommonFunction.print_log("ERROR", f"发生未处理异常: {str(e)}")
            CommonFunction.print_log("ERROR", traceback.format_exc())
            check_result = "FAIL"
            result_list = []

        self.command_check_result = check_result
        test = CommonFunction()
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for s in result_list:
                f.write(f"{s['name']},{s['description']},{s['rejected']},{s['result']}\n")
            f.write(f"total,{self.TEST_NAME},,{check_result}\n")
        for s in result_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": s["name"], "value": s["result"],
                       "description": s["description"], "rejected": s["rejected"]}
            )
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": check_result}
        )
        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{check_result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = ChassisNVMELEDNegativeCheck("ChassisNVMELEDNegativeCheck")
        checker.run_test()
        if checker.command_check_result == "PASS":
            exit_code = 0
        elif checker.command_check_result == "FAIL":
            exit_code = 2
        else:
            exit_code = 1
        _start = time.time()
        while (time.time() - _start) < 5:
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
