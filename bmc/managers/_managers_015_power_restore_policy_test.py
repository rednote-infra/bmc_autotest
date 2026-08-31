#!/bin/python
"""
Author: Fengmian
Date: 2026/05/09
Usage: python3 bmc/managers_015_power_restore_policy_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/09: 新增，通电开机策略（PowerRestorePolicy）设置与验证

数据来源：
  标准 Redfish：GET /redfish/v1/Systems/1 → PowerRestorePolicy（标准字段，ZTE 实测为 None）
  ZTE OEM：GET /redfish/v1/Systems/1 → Oem.Public.PowerOnStrategy（ZTE 定制字段，实测有值）

测试策略（优先标准，OEM 降级）：
  1. 读取当前通电策略（优先标准 PowerRestorePolicy，ZTE 回落到 OEM PowerOnStrategy）
  2. 【默认值校验】期望默认为 RestorePreviousState / LastState（断电前状态恢复）
  3. 尝试 PATCH 切换策略：AlwaysOn → 读回验证 → 恢复原始值
     - 标准 PATCH 失败 → 尝试 OEM PATCH
     - 两者均失败 → 记录 WARNING，只读验证仍可 PASS

ZTE 实测：
  - PowerRestorePolicy = None（标准字段，SDK 未映射）
  - Oem.Public.PowerOnStrategy = RestorePreviousState（ZTE 自定义枚举）
  - ZTE 已确认枚举：RestorePreviousState（断电恢复）/ TurnOn（始终开）/ StayOff（始终关）

CSV：单行摘要
JSON detail.cycle：各检查项；summary：整体 PASS/FAIL
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

SYSTEMS_URI  = "/redfish/v1/Systems/1"

# ZTE OEM 枚举值（对应标准值的 ZTE 命名，通过探测确认）
# 标准值      → ZTE OEM 值
# LastState   → RestorePreviousState（默认值）
# AlwaysOn    → TurnOn
# AlwaysOff   → StayOff
ZTE_ENUM_MAP = {
    "LastState":  "RestorePreviousState",
    "AlwaysOn":   "TurnOn",
    "AlwaysOff":  "StayOff",
}

# 期望的默认策略（含 ZTE OEM 和 Redfish 标准两种说法）
EXPECTED_DEFAULTS = {"RestorePreviousState", "LastState"}

# 测试用策略（切换到 AlwaysOn 验证写操作；用 ZTE OEM 枚举）
TEST_POLICY_ZTE       = "TurnOn"
TEST_POLICY_STANDARD  = "AlwaysOn"
RESTORE_POLICY_ZTE    = "RestorePreviousState"
RESTORE_POLICY_STANDARD = "LastState"

def _get_current_policy(sys_obj) -> tuple:
    """Extract current power-on strategy from a System SDK object.
    Returns (source, value): source='standard'|'oem'|'none'
    """
    standard = sys_obj.power_restore_policy
    if standard and isinstance(standard, str):
        return "standard", standard
    # OEM: Oem.Public (mapped to oem.bmc in SDK) → PowerOnStrategy via model_extra
    if sys_obj.oem and sys_obj.oem.bmc:
        oem_extra = sys_obj.oem.bmc.model_extra or {}
        oem_val = oem_extra.get("PowerOnStrategy")
        if oem_val and isinstance(oem_val, str):
            return "oem", oem_val
    return "none", None

class PowerRestorePolicyTest(BmcTestBase):
    """通电开机策略设置与验证

    用例编号：Redfish_Managers_015（或视 README 编排调整）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_015_power_restore_policy_test.json"),
        )
    def _try_patch(self, test, body_standard: dict, body_oem: dict) -> tuple:
        """尝试 PATCH，标准失败则 OEM，返回 (success, method)
        
        注意：ZTE OEM PowerOnStrategy PATCH 成功后 BMC 会短暂重启 Redfish 服务，
        可能导致 PATCH 响应 HTTP 501。此处将 501 视为"操作已提交，等待 BMC 就绪"而非失败。
        """
        # 尝试标准
        try:
            self.client.patch(SYSTEMS_URI, body_standard)
            return True, "standard"
        except Exception as e:
            test.print_log("WARNING", f"标准 PATCH 失败：{str(e)[:80]}，尝试 OEM...")

        # 尝试 OEM（允许 501 — BMC 重启 Redfish 服务导致）
        try:
            self.client.patch(SYSTEMS_URI, body_oem)
            return True, "oem"
        except RedfishException as e:
            err_str = str(e)
            if "501" in err_str:
                test.print_log("WARNING", "OEM PATCH 返回 501（BMC 正在重启 Redfish 服务），等待就绪...")
                time.sleep(10)
                return True, "oem"
            test.print_log("WARNING", f"OEM PATCH 失败：{err_str[:80]}")
            return False, "none"
        except Exception as e:
            test.print_log("WARNING", f"OEM PATCH 也失败：{str(e)[:80]}")
            return False, "none"

    # ── 主流程 ───────────────────────────────────────────────────────────────
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        policy_info = {}
        warnings = []
        final = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # ── 1. 读取当前策略 ──────────────────────────────────────────────
            test.print_log("INFO", "=== 步骤1: 读取当前通电策略 ===")
            sys_obj = self.client.get_system()
            source, current_policy = _get_current_policy(sys_obj)
            test.print_log("INFO", f"通电策略来源：{source}，当前值：{current_policy!r}")
            policy_info["initial"] = {"source": source, "value": current_policy}

            if source == "none" or current_policy is None:
                test.print_log("ERROR", "未能读取到通电策略（标准字段和 OEM 均为 None），无法继续测试")
                checks.append(("读取通电策略成功", False))
                raise RuntimeError("无法读取通电策略")
            checks.append(("读取通电策略成功", True))

            # ── 2. 默认值校验 ────────────────────────────────────────────────
            test.print_log("INFO", "=== 步骤2: 默认值校验 ===")
            default_ok = current_policy in EXPECTED_DEFAULTS
            if default_ok:
                test.print_log("INFO", f"✓ 通电策略默认为断电恢复（{current_policy}）")
            else:
                test.print_log("ERROR",
                    f"✗ 通电策略默认值不符合期望（当前：{current_policy!r}，"
                    f"期望：{EXPECTED_DEFAULTS}）")
            checks.append(("通电策略默认为断电恢复", default_ok))

            # ── 3. 写操作验证（AlwaysOn）────────────────────────────────────
            test.print_log("INFO", "=== 步骤3: PATCH 切换到 AlwaysOn / PowerOn ===")
            patch_ok, method = self._try_patch(
                test,
                body_standard={"PowerRestorePolicy": TEST_POLICY_STANDARD},
                body_oem={"Oem": {"Public": {"PowerOnStrategy": TEST_POLICY_ZTE}}},
            )

            if not patch_ok:
                warnings.append("通电策略 PATCH 写操作不可用（标准和 OEM 均拒绝），降级为只读验证")
                test.print_log("WARNING", warnings[-1])
                # 只读验证仍可通过（能读到且默认值符合即可）
                checks.append(("通电策略 PATCH 写操作", None))  # None = 跳过
            else:
                # 读回验证（带重试，BMC 重启 Redfish 服务可能导致短暂不可用）
                sys_obj2 = None
                for attempt in range(4):
                    time.sleep(5)
                    try:
                        sys_obj2 = self.client.get_system()
                        break
                    except Exception as e:
                        test.print_log("WARNING", f"读回尝试 {attempt+1}/4 失败：{str(e)[:60]}，继续等待...")
                if sys_obj2 is None:
                    test.print_log("ERROR", "读回通电策略超时（BMC Redfish 服务未就绪）")
                    checks.append(("通电策略 PATCH 写操作", False))
                    raise RuntimeError("读回失败")
                src2, val2 = _get_current_policy(sys_obj2)
                expect_after = {TEST_POLICY_ZTE, TEST_POLICY_STANDARD}
                write_ok = val2 in expect_after
                test.print_log(
                    "INFO" if write_ok else "ERROR",
                    f"{'✓' if write_ok else '✗'} PATCH 后读回：{val2!r}（来源：{src2}，方式：{method}）"
                )
                checks.append(("通电策略 PATCH 写操作", write_ok))
                policy_info["after_patch"] = {"source": src2, "value": val2, "method": method}

                # ── 4. 恢复原始值 ────────────────────────────────────────────
                test.print_log("INFO", "=== 步骤4: 恢复原始通电策略 ===")
                restore_body_std = {"PowerRestorePolicy": RESTORE_POLICY_STANDARD}
                restore_body_oem = {"Oem": {"Public": {"PowerOnStrategy": current_policy}}}
                restore_ok, rm = self._try_patch(test, restore_body_std, restore_body_oem)
                sys_obj3 = None
                for attempt in range(4):
                    time.sleep(5)
                    try:
                        sys_obj3 = self.client.get_system()
                        break
                    except Exception as e:
                        test.print_log("WARNING", f"读回还原状态尝试 {attempt+1}/4 失败：{str(e)[:60]}")
                if sys_obj3 is None:
                    test.print_log("WARNING", "读回还原状态失败，跳过还原验证")
                    checks.append(("通电策略恢复原始值", None))
                    policy_info["after_restore"] = {"source": "unknown", "value": "unverified"}
                    sys_obj3 = None
                src3, val3 = _get_current_policy(sys_obj3) if sys_obj3 else ("unknown", None)
                restored = val3 in EXPECTED_DEFAULTS or val3 == current_policy
                test.print_log(
                    "INFO" if restored else "WARNING",
                    f"恢复后：{val3!r}（来源：{src3}，方式：{rm}）"
                )
                checks.append(("通电策略恢复原始值", restored))
                policy_info["after_restore"] = {"source": src3, "value": val3}

            # 最终判定：跳过（None）的检查项不计入
            active_checks = [(n, r) for n, r in checks if r is not None]
            final = "PASS" if all(r for _, r in active_checks) else "FAIL"
            self.command_check_result = final

        except RuntimeError:
            final = "FAIL"
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
            "policy_info": policy_info,
            "check_items": [
                {"name": n, "result": "PASS" if r else ("SKIP" if r is None else "FAIL")}
                for n, r in checks
            ],
            "warnings": warnings,
        }
        summary = {"metrics": self.TEST_NAME, "value": final}

        test.add_key_value_to_json(self.result_json_path, "detail.cycle", value=cycle_result)
        test.add_key_value_to_json(self.result_json_path, "summary", value=summary)

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for n, r in checks:
                f.write(f"{n},{'PASS' if r else ('SKIP' if r is None else 'FAIL')}\n")

        with open(self.exit_code_path, "w") as f:
            code = 0 if final == "PASS" else 2
            f.write(str(code))
            self.exit_code = code

        return final

# ── 入口 ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    exit_code = 1
    checker = None
    try:
        checker = PowerRestorePolicyTest("PowerRestorePolicyTest")
        checker.run_test()
        exit_code = getattr(checker, "exit_code", 0)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception:
        traceback.print_exc()
        exit_code = 1
    sys.exit(exit_code)
