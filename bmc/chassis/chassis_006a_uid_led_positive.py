#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/chassis_006a_uid_led_positive.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/22: 新增
2026/06/23: 改写为 redfish_sdk v1.1.0 类型化接口（set_indicator_led / get_chassis().indicator_led）

数据来源：get_chassis().indicator_led（读，v1.1.0 新增字段）
          set_indicator_led("<value>")（写，v1.1.0，内部 PATCH /redfish/v1/Chassis/1）

校验策略（正向测试）：
  依次将 UID 指示灯切换为 Lit / Blinking / Off 三态，
  每次 PATCH 后 GET 回读验证实际值与期望值一致。
  测试结束后回滚到 Off（最后一步本身即为 Off，无需额外回滚）。

  PASS 条件：三态切换全部成功，回读值与期望值一致
  FAIL 条件：任意一态切换失败，或回读值与期望值不符
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

# UID 指示灯标准合法值（正向测试依次切换）
LED_STATES = ["Lit", "Blinking", "Off"]  # 由配置 LedStates 覆盖

class ChassisUIDLEDPositiveCheck(BmcTestBase):
    """Chassis UID 指示灯操作测试（正向）

    用例编号：Redfish_Chassis_006a
    检查项：依次将 UID 指示灯切换为 Lit / Blinking / Off，验证每态回读值一致
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_006a_uid_led_positive.json"),
        )
    def _load_extra_config(self, conf_section: dict) -> None:
        self.LED_STATES      = conf_section.get("LedStates", LED_STATES)
        self.READ_BACK_DELAY = conf_section.get("ReadBackDelaySeconds", 2)
        self.MAX_RETRY       = conf_section.get("MaxRetry", 5)

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
        """获取 Chassis 资源的 odata_id"""
        chassis = self.client.get_chassis()
        return chassis.odata_id

    # ── 回读说明 ───────────────────────────────────────────────────────────
    # v1.1.0 起 SDK 提供 set_indicator_led() 写接口，且 Chassis 模型已暴露
    # indicator_led 字段，写用 set_indicator_led()、读用 get_chassis().indicator_led。

    # ── 单步切换 + 回读验证 ────────────────────────────────────────────────

    def _set_and_verify(self, target_state: str) -> dict:
        """
        将 IndicatorLED 切换到 target_state，并回读验证。
        返回单步结果 dict。
        """
        step_result = {
            "state": target_state,
            "patch_success": False,
            "read_back": None,
            "match": False,
            "result": "FAIL",
        }

        # 写入（v1.1.0：set_indicator_led 内部校验合法值并 PATCH）
        try:
            self.client.set_indicator_led(target_state)
            step_result["patch_success"] = True
            CommonFunction.print_log("INFO", f"set_indicator_led={target_state} 成功")
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"set_indicator_led={target_state} 失败：{str(e)}")
            return step_result

        # 轮询回读，直到状态同步或超时（v1.1.0：get_chassis().indicator_led）
        for attempt in range(1, self.MAX_RETRY + 1):
            time.sleep(self.READ_BACK_DELAY)
            try:
                actual = self.client.get_chassis().indicator_led
                step_result["read_back"] = actual
                CommonFunction.print_log(
                    "INFO",
                    f"第{attempt}次回读 IndicatorLED={actual}（期望：{target_state}）"
                )
            except RedfishException as e:
                CommonFunction.print_log("ERROR", f"回读 Chassis 失败：{str(e)}")
                return step_result

            if actual == target_state:
                step_result["match"] = True
                step_result["result"] = "PASS"
                CommonFunction.print_log("INFO", f"IndicatorLED={target_state} 验证通过 ✓")
                return step_result
            else:
                if attempt < self.MAX_RETRY:
                    CommonFunction.print_log(
                        "WARNING",
                        f"状态未同步（期望 {target_state}，实际 {actual}），"
                        f"等待 {self.READ_BACK_DELAY}s 后重试（{attempt}/{self.MAX_RETRY}）"
                    )
                else:
                    CommonFunction.print_log(
                        "ERROR",
                        f"IndicatorLED 回读超时：期望 {target_state}，实际 {actual}，"
                        f"已重试 {self.MAX_RETRY} 次"
                    )

        return step_result

    # ── 主检查流程 ─────────────────────────────────────────────────────────

    def uid_led_positive_check(self) -> tuple:
        result_list = []
        overall = "PASS"

        # 获取 Chassis odata_id
        try:
            chassis_odata_id = self._get_chassis_odata_id()
            CommonFunction.print_log("INFO", f"Chassis 路径：{chassis_odata_id}")
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Chassis 失败：{str(e)}")
            return "FAIL", []

        # 记录初始值（用于异常时回滚参考，v1.1.0：get_chassis().indicator_led）
        try:
            initial_state = self.client.get_chassis().indicator_led or "Off"
            CommonFunction.print_log("INFO", f"当前 IndicatorLED 初始值：{initial_state}")
        except RedfishException as e:
            CommonFunction.print_log("WARNING", f"获取初始 IndicatorLED 值失败：{str(e)}")
            initial_state = "Off"

        # 依次切换三态
        for state in self.LED_STATES:
            step = self._set_and_verify(state)
            result_list.append(step)
            if step["result"] == "FAIL":
                overall = "FAIL"

        # 末态是 Off，已完成回滚，无需额外操作
        if overall == "PASS":
            CommonFunction.print_log("INFO", "UID 指示灯三态切换全部通过，已回滚至 Off")
        else:
            # 尽力回滚
            try:
                self.client.set_indicator_led("Off")
                CommonFunction.print_log("INFO", "已尝试回滚 IndicatorLED → Off")
            except RedfishException:
                CommonFunction.print_log("WARNING", "回滚 IndicatorLED 失败，请手动检查")

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
            check_result, result_list = self.uid_led_positive_check()
        except Exception as e:
            CommonFunction.print_log("ERROR", f"发生未处理异常: {str(e)}")
            CommonFunction.print_log("ERROR", traceback.format_exc())
            check_result = "FAIL"
            result_list = []

        self.command_check_result = check_result
        test = CommonFunction()
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for s in result_list:
                f.write(f"{s['state']},{s.get('read_back')},{s['result']}\n")
            f.write(f"total,{self.TEST_NAME},{check_result}\n")
        for s in result_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f"IndicatorLED={s['state']}", "value": s["result"],
                       "read_back": s.get("read_back")}
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
        checker = ChassisUIDLEDPositiveCheck("ChassisUIDLEDPositiveCheck")
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
