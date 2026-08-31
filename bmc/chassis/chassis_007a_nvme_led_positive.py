#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/chassis_007a_nvme_led_positive.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/22: 新增
2026/06/23: 改写为 redfish_sdk v1.1.0 类型化接口（set_drive_indicator_led / get_drive）

数据来源：get_drives() → List[Drive]（含 protocol/media_type/indicator_led，v1.1.0 新增字段）
          set_drive_indicator_led(<drive.odata_id>, "<value>")（写，v1.1.0）

校验策略（正向测试）：
  遍历所有 Drive，按 Protocol 分类处理：
    - NVMe SSD（Protocol=NVMe，MediaType=SSD）：必须支持 IndicatorLED，否则 FAIL
    - 其余（SATA/SAS 等）：不在本用例范围内，记 SKIP

  对 NVMe SSD，依次切换 Lit / Off 两态（部分 BMC 不支持 Blinking），
  每次 PATCH 后轮询 GET 回读验证实际值与期望值一致。
  测试结束后末态本身即 Off，无需额外回滚。

  PASS 条件：所有 NVMe SSD 切换全部成功，非 NVMe 盘跳过不影响结果
  FAIL 条件：NVMe SSD 不支持 IndicatorLED，或任意态切换/回读超时失败
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

LED_STATES = ["Lit", "Off"]  # 默认不含 Blinking，部分 BMC 不支持，由配置 LedStates 覆盖

class ChassisNVMELEDPositiveCheck(BmcTestBase):
    """Chassis NVMe 指示灯操作测试（正向）

    用例编号：Redfish_Chassis_007a
    检查项：遍历所有 Drive，NVMe SSD 必须支持 IndicatorLED 切换验证，非 NVMe 盘记 SKIP
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_007a_nvme_led_positive.json"),
        )
    def _load_extra_config(self, conf_section: dict) -> None:
        # 可选配置：支持的 LED 状态列表 / PATCH 后回读等待时间
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

    # ── 单步切换 + 回读验证 ────────────────────────────────────────────────

    def _set_and_verify(self, drive_odata_id: str, drive_name: str, target_state: str) -> dict:
        step_result = {
            "drive": drive_name,
            "state": target_state,
            "patch_success": False,
            "read_back": None,
            "match": False,
            "result": "FAIL",
        }

        # 写入（v1.1.0：set_drive_indicator_led 内部校验合法值并 PATCH）
        try:
            self.client.set_drive_indicator_led(drive_odata_id, target_state)
            step_result["patch_success"] = True
            CommonFunction.print_log("INFO", f"[{drive_name}] set_drive_indicator_led={target_state} 成功")
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"[{drive_name}] set_drive_indicator_led={target_state} 失败：{str(e)}")
            return step_result

        # 轮询回读，直到状态同步或超时（v1.1.0：get_drive().indicator_led）
        for attempt in range(1, self.MAX_RETRY + 1):
            time.sleep(self.READ_BACK_DELAY)
            try:
                actual = self.client.get_drive(drive_odata_id).indicator_led
                step_result["read_back"] = actual
                CommonFunction.print_log(
                    "INFO",
                    f"[{drive_name}] 第{attempt}次回读 IndicatorLED={actual}（期望：{target_state}）"
                )
            except RedfishException as e:
                CommonFunction.print_log("ERROR", f"[{drive_name}] 回读 Drive 失败：{str(e)}")
                return step_result

            if actual == target_state:
                step_result["match"] = True
                step_result["result"] = "PASS"
                CommonFunction.print_log("INFO", f"[{drive_name}] IndicatorLED={target_state} 验证通过 ✓")
                return step_result
            else:
                if attempt < self.MAX_RETRY:
                    CommonFunction.print_log(
                        "WARNING",
                        f"[{drive_name}] 状态未同步（期望 {target_state}，实际 {actual}），"
                        f"等待 {self.READ_BACK_DELAY}s 后重试（{attempt}/{self.MAX_RETRY}）"
                    )
                else:
                    CommonFunction.print_log(
                        "ERROR",
                        f"[{drive_name}] IndicatorLED 回读超时：期望 {target_state}，实际 {actual}，"
                        f"已重试 {self.MAX_RETRY} 次"
                    )

        return step_result

    # ── 主检查流程 ─────────────────────────────────────────────────────────

    def nvme_led_positive_check(self) -> tuple:
        result_list = []
        overall = "PASS"

        # 获取所有 Drive
        try:
            drives = self.client.get_drives()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Drive 列表失败：{str(e)}")
            return "FAIL", []

        if not drives:
            CommonFunction.print_log("ERROR", "未找到任何 Drive，无法执行测试")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共找到 {len(drives)} 块 Drive")

        tested_count = 0
        for drive in drives:
            drive_odata_id = drive.odata_id
            drive_name = drive.name or drive_odata_id or "unknown"

            # v1.1.0：Drive 模型已含 protocol/media_type/indicator_led，直接取用
            protocol   = (drive.protocol   or "").upper()    # e.g. NVMe / SATA / SAS
            media_type = (drive.media_type or "").upper()    # e.g. SSD / HDD

            # 只测 NVMe SSD，其余全部 SKIP（SATA HDD 点灯走 Systems 资源，URL/body 不同）
            is_nvme_ssd = (protocol == "NVME" and media_type == "SSD")
            if not is_nvme_ssd:
                CommonFunction.print_log(
                    "INFO",
                    f"[{drive_name}] Protocol={protocol} MediaType={media_type}，非 NVMe SSD，跳过"
                )
                result_list.append({
                    "drive": drive_name,
                    "state": "SKIP",
                    "patch_success": False,
                    "read_back": None,
                    "match": False,
                    "result": "SKIP",
                })
                continue

            # NVMe SSD：必须支持 IndicatorLED
            if drive.indicator_led is None:
                CommonFunction.print_log(
                    "ERROR",
                    f"[{drive_name}] NVMe SSD 缺少 IndicatorLED 字段"
                )
                result_list.append({
                    "drive": drive_name,
                    "state": "MISSING",
                    "patch_success": False,
                    "read_back": None,
                    "match": False,
                    "result": "FAIL",
                })
                overall = "FAIL"
                continue

            CommonFunction.print_log(
                "INFO",
                f"开始测试 [{drive_name}]（Protocol={protocol} MediaType={media_type}）"
            )
            tested_count += 1

            for state in self.LED_STATES:
                step = self._set_and_verify(drive_odata_id, drive_name, state)
                result_list.append(step)
                if step["result"] == "FAIL":
                    overall = "FAIL"
                    try:
                        self.client.set_drive_indicator_led(drive_odata_id, "Off")
                    except RedfishException:
                        pass
                    break  # 当前盘跳过剩余状态

        # 没有找到任何 NVMe SSD，测试无效
        if tested_count == 0:
            CommonFunction.print_log("ERROR", "未找到任何 NVMe SSD Drive，测试无效")
            return "FAIL", result_list

        if not result_list:
            CommonFunction.print_log("ERROR", "未找到任何 Drive，测试无效")
            return "FAIL", []

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
            check_result, result_list = self.nvme_led_positive_check()
        except Exception as e:
            CommonFunction.print_log("ERROR", f"发生未处理异常: {str(e)}")
            CommonFunction.print_log("ERROR", traceback.format_exc())
            check_result = "FAIL"
            result_list = []

        self.command_check_result = check_result
        test = CommonFunction()
        skip_list = [s["drive"] for s in result_list if s["result"] == "SKIP"]
        if skip_list:
            CommonFunction.print_log("INFO", f"以下非 NVMe SSD 盘不在本用例范围内，已跳过：{skip_list}")
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for s in result_list:
                f.write(f"{s['drive']},{s['state']},{s.get('read_back')},{s['result']}\n")
            f.write(f"total,{self.TEST_NAME},,{check_result}\n")
        for s in result_list:
            if s["result"] == "SKIP":
                continue
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f"{s['drive']} IndicatorLED={s['state']}",
                       "value": s["result"], "read_back": s.get("read_back")}
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
        checker = ChassisNVMELEDPositiveCheck("ChassisNVMELEDPositiveCheck")
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
