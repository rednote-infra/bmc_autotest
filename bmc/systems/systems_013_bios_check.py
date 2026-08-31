#!/bin/python
"""
Author: Fengmian
Date: 2026/05/09
Usage: python3 bmc/systems_013_bios_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/09: 新增，BIOS 基本信息检查

数据来源：
  get_bios()                  → Bios 对象（Attributes 字段，1000+ BIOS 属性）
  get_raw('/redfish/v1/Systems/1') → BiosVersion 字段（ZTE 实测版本号放在 Systems/1 顶层）

校验策略：
  必要字段（为空/异常 → FAIL + ERROR）：
    BiosVersion（从 Systems/1.BiosVersion 获取，非空字符串）
    Attributes（非 None，BIOS 属性集合存在）

CSV：单行摘要（BiosVersion, AttributesCount, Result）
JSON detail.cycle：BIOS 信息条目；summary：整体 PASS/FAIL
"""

import os
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class BiosInfoCheck(BmcTestBase):
    """Systems BIOS 基本信息检查

    用例编号：Redfish_Systems_013
    检查项：BiosVersion 非空、Attributes 字段存在
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_013_bios_check.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        bios_info = {}
        final = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # ── 1. BiosVersion（从 Systems/1 顶层获取）────────────────────
            # 说明：BiosVersion 是 ComputerSystem 规范字段，挂载在 /Systems/1 顶层；
            #       SDK System 模型已封装 bios_version 属性，直接读取无需 get_raw。
            test.print_log("INFO", "=== 步骤1: 获取 BiosVersion（SDK get_system()）===")
            system = self.client.get_system()
            bios_version = system.bios_version
            version_ok = isinstance(bios_version, str) and len(bios_version.strip()) > 0
            if version_ok:
                test.print_log("INFO", f"BiosVersion = {bios_version!r}")
            else:
                test.print_log("ERROR", f"BiosVersion 为空或非字符串（当前值：{bios_version!r}）")
            checks.append(("BiosVersion 非空", version_ok))
            bios_info["BiosVersion"] = bios_version

            # ── 2. Attributes（从 /Systems/1/Bios 获取）──────────────────
            test.print_log("INFO", "=== 步骤2: 获取 BIOS Attributes ===")
            bios = self.client.get_bios()
            attributes = bios.attributes
            attr_ok = attributes is not None
            if attr_ok:
                attr_count = len(attributes) if isinstance(attributes, dict) else -1
                test.print_log("INFO", f"Attributes 字段存在（共 {attr_count} 个属性键）")
            else:
                test.print_log("ERROR", "Attributes 字段为 None，BIOS 属性集缺失")
            checks.append(("Attributes 字段存在", attr_ok))
            bios_info["AttributesCount"] = len(attributes) if isinstance(attributes, dict) else 0

            final = "PASS" if all(r for _, r in checks) else "FAIL"
            self.command_check_result = final

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish SDK 调用失败：{str(e)}")
            final = "FAIL"
        except Exception as e:
            test.print_log("ERROR", f"未知异常：{str(e)}")
            test.print_log("ERROR", traceback.format_exc())
            final = "FAIL"
        finally:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass

        # ── 结果写入 ─────────────────────────────────────────────────────────
        test.print_log("INFO", f"测试结束，结果：{final}")

        cycle_result = {
            "bios_info": bios_info,
            "check_items": [{"name": n, "result": "PASS" if r else "FAIL"} for n, r in checks],
        }
        summary = {"metrics": self.TEST_NAME, "value": final}

        test.add_key_value_to_json(self.result_json_path, "detail.cycle", value=cycle_result)
        test.add_key_value_to_json(self.result_json_path, "summary", value=summary)

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"{bios_info.get('BiosVersion','')},{bios_info.get('AttributesCount',0)},{final}\n")

        with open(self.exit_code_path, "w") as f:
            if final == "PASS":
                f.write("0")
                self.exit_code = 0
            else:
                f.write("2")
                self.exit_code = 2

        return final

# ── 入口 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    exit_code = 1
    checker = None
    try:
        checker = BiosInfoCheck("BiosInfoCheck")
        checker.run_test()
        exit_code = getattr(checker, "exit_code", 0)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception:
        traceback.print_exc()
        exit_code = 1
    sys.exit(exit_code)
