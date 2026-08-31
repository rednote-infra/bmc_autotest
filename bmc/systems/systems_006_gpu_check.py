#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_006_gpu_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    Manufacturer、Model、Version、PowerWatts
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    Oem.SerialNumber

CSV：每行一个 GPU
JSON detail.cycle：每个 GPU 一条记录；summary：整体 PASS/FAIL
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class GpuInfoCheck(BmcTestBase):
    """Systems GPU 集合资源信息检查

    用例编号：Redfish_Systems_006
    检查项：每个 GPU 的关键字段非空/合法性校验
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_006_gpu_check.json"),
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

    # ── 单 GPU 校验 ───────────────────────────────────────────────────────────

    def _check_gpu(self, gpu, idx: int) -> tuple:
        gpu_id = gpu.id or str(idx)
        all_pass = True

        # ══ 必要字段 ══

        # 1. Manufacturer
        if not gpu.manufacturer or (isinstance(gpu.manufacturer, str) and gpu.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", f"[GPU] {gpu_id}：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[GPU] {gpu_id}：Manufacturer = {gpu.manufacturer}")

        # 2. Model
        if not gpu.model or (isinstance(gpu.model, str) and gpu.model.strip() == ""):
            CommonFunction.print_log("ERROR", f"[GPU] {gpu_id}：Model 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[GPU] {gpu_id}：Model = {gpu.model}")

        # 3. Version
        if not gpu.version or (isinstance(gpu.version, str) and gpu.version.strip() == ""):
            CommonFunction.print_log("ERROR", f"[GPU] {gpu_id}：Version 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[GPU] {gpu_id}：Version = {gpu.version}")

        # 4. PowerWatts（非空字符串，内容合法）
        if not gpu.power_watts or (isinstance(gpu.power_watts, str) and gpu.power_watts.strip() == ""):
            CommonFunction.print_log("ERROR", f"[GPU] {gpu_id}：PowerWatts 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[GPU] {gpu_id}：PowerWatts = {gpu.power_watts}")

        # ══ 非必要字段 ══

        # 5. Oem.SerialNumber
        oem_sn = gpu.oem.serial_number if gpu.oem else None
        if not oem_sn or (isinstance(oem_sn, str) and oem_sn.strip() == ""):
            CommonFunction.print_log("WARNING", f"[GPU] {gpu_id}：Oem.SerialNumber 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[GPU] {gpu_id}：Oem.SerialNumber = {oem_sn}")

        gpu_info = {
            "gpu_id":          gpu_id,
            "manufacturer":    gpu.manufacturer or "",
            "model":           gpu.model or "",
            "version":         gpu.version or "",
            "power_watts":     gpu.power_watts or "",
            "oem_serial_number": oem_sn or "",
            "check_result":    "PASS" if all_pass else "FAIL",
        }
        return all_pass, gpu_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def gpu_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 GPU 数据")
        try:
            gpu_list = self.client.get_gpus()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 GPU 数据失败：{str(e)}")
            return "FAIL", []

        if not gpu_list:
            CommonFunction.print_log("ERROR", "GPU 列表为空，检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共获取到 {len(gpu_list)} 个 GPU，开始逐一校验")
        all_results = []
        gpu_info_list = []
        for idx, gpu in enumerate(gpu_list):
            passed, gpu_info = self._check_gpu(gpu, idx)
            all_results.append(passed)
            gpu_info_list.append(gpu_info)

        final = "PASS" if all(all_results) else "FAIL"
        failed_cnt = sum(1 for r in all_results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"GPU 信息检查全部通过，共 {len(gpu_list)} 个")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"GPU 信息检查失败，{failed_cnt}/{len(gpu_list)} 个 GPU 存在必要字段异常"
            )
        return final, gpu_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, gpu_info_list = self.gpu_info_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in gpu_info_list:
                f.write(
                    f'"{info["gpu_id"]}",'
                    f'"{info["manufacturer"]}",'
                    f'"{info["model"]}",'
                    f'"{info["version"]}",'
                    f'"{info["power_watts"]}",'
                    f'"{info["oem_serial_number"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in gpu_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'GPU_{info["gpu_id"]}', "value": info}
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
        checker = GpuInfoCheck("GpuInfoCheck")
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
