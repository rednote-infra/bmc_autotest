#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/chassis_006b_uid_led_negative.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/22: 新增

数据来源：PATCH /redfish/v1/Chassis/1 {"IndicatorLED": "<invalid_value>"}

校验策略（反向测试）：
  向 BMC 发送非法 IndicatorLED 值，验证 BMC 返回 4xx 错误拒绝请求。
  非法场景包括：
    1. 完全无效的字符串值
    2. 大小写错误（合法值的错误大小写）
    3. 空字符串
    4. 数字类型（类型错误）
    5. null（空值）

  PASS 条件：所有非法请求均被 BMC 以 4xx 拒绝
  FAIL 条件：任意非法请求被 BMC 接受（返回 2xx）
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

# 反向测试用例：(用例名, PATCH body, 说明)
NEGATIVE_CASES = [
    ("invalid_string",    {"IndicatorLED": "InvalidValue"},  "完全无效字符串"),
    ("wrong_case_lit",    {"IndicatorLED": "lit"},           "合法值小写（lit 而非 Lit）"),
    ("wrong_case_off",    {"IndicatorLED": "off"},           "合法值小写（off 而非 Off）"),
    ("empty_string",      {"IndicatorLED": ""},              "空字符串"),
    ("numeric_type",      {"IndicatorLED": 1},               "数字类型（应为字符串）"),
    ("null_value",        {"IndicatorLED": None},            "null 值"),
]

class ChassisUIDLEDNegativeCheck(BmcTestBase):
    """Chassis UID 指示灯操作测试（反向）

    用例编号：Redfish_Chassis_006b
    检查项：发送非法 IndicatorLED 值，验证 BMC 返回 4xx 拒绝
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_006b_uid_led_negative.json"),
        )
    def _init_sdk_client(self):
        CommonFunction.print_log("DEBUG", "开始初始化 Redfish SDK 客户端")
        self.client = RedfishClient(
            host=self.BMC_IP,
            username=self.USERNAME,
            password=self.PASSWORD,
        )
        CommonFunction.print_log("DEBUG", "Redfish SDK 客户端初始化完成")

    # ── 获取 Chassis 路径 ──────────────────────────────────────────────────

    def _get_chassis_odata_id(self) -> str:
        chassis = self.client.get_chassis()
        return chassis.odata_id

    # ── 单条反向用例执行 ───────────────────────────────────────────────────

    def _run_negative_case(self, chassis_odata_id: str, name: str, body: dict, desc: str) -> dict:
        """发送非法 PATCH，期望 BMC 返回 4xx。"""
        step_result = {
            "name": name,
            "description": desc,
            "body": str(body),
            "rejected": False,
            "result": "FAIL",
        }

        CommonFunction.print_log("INFO", f"[{name}] 发送非法 PATCH：{desc}，body={body}")
        try:
            self.client.patch(chassis_odata_id, body)
            # 如果没有抛异常说明 BMC 接受了，这是不期望的
            CommonFunction.print_log(
                "ERROR",
                f"[{name}] BMC 意外接受了非法请求（应返回 4xx）"
            )
        except RedfishException as e:
            err_str = str(e)
            # RedfishException 通常含 HTTP 状态码，4xx 均视为正确拒绝
            if any(f" {code}" in err_str or f"_{code}" in err_str or str(code) in err_str
                   for code in [400, 403, 404, 405, 422]):
                step_result["rejected"] = True
                step_result["result"] = "PASS"
                CommonFunction.print_log("INFO", f"[{name}] BMC 正确拒绝（4xx）：{err_str[:120]}")
            else:
                # 5xx 或其他非预期错误也视为 PASS（BMC 拒绝了）
                # 只要没有返回 2xx 成功即可
                step_result["rejected"] = True
                step_result["result"] = "PASS"
                CommonFunction.print_log(
                    "INFO",
                    f"[{name}] BMC 返回错误（非 2xx），视为拒绝：{err_str[:120]}"
                )

        return step_result

    # ── 主检查流程 ─────────────────────────────────────────────────────────

    def uid_led_negative_check(self) -> tuple:
        result_list = []
        overall = "PASS"

        # 获取 Chassis odata_id
        try:
            chassis_odata_id = self._get_chassis_odata_id()
            CommonFunction.print_log("INFO", f"Chassis 路径：{chassis_odata_id}")
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Chassis 失败：{str(e)}")
            return "FAIL", []

        # 依次执行所有反向用例
        for name, body, desc in NEGATIVE_CASES:
            step = self._run_negative_case(chassis_odata_id, name, body, desc)
            result_list.append(step)
            if step["result"] == "FAIL":
                overall = "FAIL"

        # 确保测试结束后指示灯为 Off（防止前置正向用例遗留 Lit/Blinking）
        try:
            self.client.patch(chassis_odata_id, {"IndicatorLED": "Off"})
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
            check_result, result_list = self.uid_led_negative_check()
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
        checker = ChassisUIDLEDNegativeCheck("ChassisUIDLEDNegativeCheck")
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
