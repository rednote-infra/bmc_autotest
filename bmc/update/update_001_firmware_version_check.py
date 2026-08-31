#!/bin/python
"""
Author: Fengmian
Date: 2026/05/09
Usage: python3 bmc/update_001_firmware_version_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/09: 新增，固件版本基本信息检查
2026/05/09: 背板CPLD/EPLD改为必须项；适配浪潮 Front_HDD_CPLD1 命名；修复 JSON 写入

数据来源：get_firmware_inventory() → List[FirmwareInventory]
  返回系统所有固件/软件组件版本（BMC、BIOS、CPLD/EPLD、NIC 等）

校验策略：
  通过关键字模糊匹配（不区分大小写）识别四类关键组件：

  必须存在且版本非空（找不到 → FAIL，找到但版本空 → FAIL）：
    BMC      - Name/Id 含 "bmc"（ZTE: ActiveBMC；浪潮: BMC）
    BIOS     - Name/Id 含 "bios"（ZTE: ActiveBIOS；浪潮: BIOS）
    主板CPLD - Name/Id 含 "cpld" 或 "epld"，且含 "mainboard"/"mb"/"main"/"managercard"
    背板CPLD - Name/Id 含 "cpld" 或 "epld"，且含 "backplane"/"bp"/"disk"/"hdd"/"front"
               （ZTE: DiskBP_EPLD8/10；浪潮: Front_HDD_CPLD1 等）

  匹配 Active/Primary 优先原则：
    当 Active/Backup 同时存在时（如 ActiveBMC + BackupBMC），以 Active 为准

CSV：每行一个关键组件
JSON detail.cycle：固件清单及各组件检查结果；summary：整体 PASS/FAIL
"""

import os
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

# ── 关键字匹配规则 ────────────────────────────────────────────────────────────
def _name_lower(item) -> str:
    """提取 Name 和 Id 合并后的小写字符串"""
    name = (getattr(item, "name", None) or "").lower()
    eid  = (getattr(item, "id",   None) or "").lower()
    return name + " " + eid

def _is_active(item) -> bool:
    """判断是否为 Active/Primary 固件（排序优先级高）"""
    s = _name_lower(item)
    return "active" in s or "primary" in s or "running" in s

COMPONENT_RULES = [
    {
        "label":    "BMC 固件版本",
        "required": True,
        "match":    lambda s: "bmc" in s,
    },
    {
        "label":    "BIOS 固件版本",
        "required": True,
        "match":    lambda s: "bios" in s,
    },
    {
        "label":    "主板CPLD/EPLD版本",
        "required": True,
        # 精确匹配：含 cpld/epld 且含主板相关前缀
        # 兜底：仅含 cpld/epld（不带位置前缀，厂商统一命名时降级为 WARNING）
        "match":    lambda s: ("cpld" in s or "epld" in s) and any(
            x in s for x in ["mainboard", "mb_", "main_", "managercardepld", "managercard"]
        ),
        "fallback_match": lambda s: "cpld" in s or "epld" in s,
        "fallback_required": False,   # 兜底命中时降为 WARNING（非 FAIL）
    },
    {
        "label":    "背板CPLD/EPLD版本",
        "required": True,
        # ZTE: DiskBP_EPLD8/10/FanBoard_EPLD13；浪潮: Front_HDD_CPLD1 等
        "match":    lambda s: ("cpld" in s or "epld" in s) and any(
            x in s for x in ["backplane", "bp_", "disk", "hdd", "front_hdd", "fanboard", "fanboad"]
        ),
        "fallback_match": lambda s: "cpld" in s or "epld" in s,
        "fallback_required": False,   # 兜底命中时降为 WARNING（非 FAIL）
    },
]

# CPLD/EPLD 通用兜底：所有含关键字条目汇总参考
CPLD_EPLD_FALLBACK = lambda s: "cpld" in s or "epld" in s  # noqa: E731

class FirmwareVersionCheck(BmcTestBase):
    """UpdateService FirmwareInventory 固件版本检查

    用例编号：Redfish_UpdateService_001
    检查项：BMC/BIOS（必须），主板CPLD/背板CPLD（可选）版本存在且非空
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/update/update_001_firmware_version_check.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        warnings = []
        component_results = []
        all_items_info = []
        final = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            inventory = self.client.get_firmware_inventory()
            test.print_log("INFO", f"get_firmware_inventory() 返回 {len(inventory)} 条固件记录")

            # ── 打印所有条目（参考信息）────────────────────────────────────
            test.print_log("INFO", "=== 所有固件条目（参考）===")
            for item in inventory:
                name    = getattr(item, "name", None) or getattr(item, "id", "?")
                version = getattr(item, "version", None)
                desc    = getattr(item, "description", None)
                test.print_log("INFO", f"  [{name}] version={version!r}  desc={desc!r}")
                all_items_info.append({"name": name, "version": version, "description": desc})

            # ── 按规则匹配关键组件 ────────────────────────────────────────
            test.print_log("INFO", "=== 关键组件版本校验 ===")
            for rule in COMPONENT_RULES:
                label           = rule["label"]
                required        = rule["required"]
                match_fn        = rule["match"]
                fallback_match  = rule.get("fallback_match")
                fallback_req    = rule.get("fallback_required", required)

                matched = [item for item in inventory if match_fn(_name_lower(item))]

                if not matched and fallback_match:
                    # 尝试兜底匹配（例如通用 Cpld 条目）
                    fb_matched = [item for item in inventory if fallback_match(_name_lower(item))]
                    if fb_matched:
                        test.print_log("WARNING",
                            f"{label}：精确匹配失败，降级使用通用兜底条目"
                            f"（厂商可能未区分主板/背板，记WARNING）")
                        warnings.append(f"{label}：通用兜底匹配，未区分主板/背板位置")
                        matched   = fb_matched
                        required  = fallback_req   # 降为非必须（WARNING 而非 FAIL）

                if not matched:
                    msg = f"{label}：未在 FirmwareInventory 中匹配到对应条目"
                    if required:
                        test.print_log("ERROR", msg + "（必须字段，FAIL）")
                        checks.append((label, False))
                    else:
                        test.print_log("WARNING", msg + "（可选字段，WARNING）")
                        warnings.append(msg)
                    component_results.append({
                        "component": label, "matched_name": None,
                        "version": None,
                        "result": "FAIL" if required else "WARNING",
                    })
                    continue

                # Active 优先排序
                matched.sort(key=lambda x: (0 if _is_active(x) else 1))
                item = matched[0]
                item_name = getattr(item, "name", None) or getattr(item, "id", "?")
                version   = getattr(item, "version", None)

                if len(matched) > 1:
                    extras = [getattr(m, "name", None) or getattr(m, "id", "?") for m in matched[1:]]
                    test.print_log("WARNING", f"{label}：匹配到多个条目 {extras}，以 [{item_name}] 为准")

                version_ok = isinstance(version, str) and len(version.strip()) > 0
                if version_ok:
                    log_level = "INFO" if required else "WARNING"
                    test.print_log(log_level, f"{'✓' if required else '⚠️'} {label}：[{item_name}] version={version!r}")
                else:
                    msg = f"{label}：[{item_name}] version 为空或 None"
                    if required:
                        test.print_log("ERROR", msg)
                    else:
                        test.print_log("WARNING", msg)

                if required:
                    checks.append((label, version_ok))
                else:
                    if not version_ok:
                        warnings.append(f"{label} version 为空")

                component_results.append({
                    "component": label,
                    "matched_name": item_name,
                    "version": version,
                    "result": "PASS" if (version_ok and required) else
                              ("WARNING" if version_ok else ("FAIL" if required else "WARNING")),
                })

            # ── CPLD/EPLD 通用兜底（仅参考）────────────────────────────
            cpld_all = [item for item in inventory if CPLD_EPLD_FALLBACK(_name_lower(item))]
            if cpld_all:
                test.print_log("INFO", "=== 所有含 CPLD/EPLD 关键字的条目（参考）===")
                for c in cpld_all:
                    n = getattr(c, "name", None) or getattr(c, "id", "?")
                    v = getattr(c, "version", None)
                    test.print_log("INFO", f"  [{n}] version={v!r}")

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
            "all_firmware_items": all_items_info,
            "component_checks": component_results,
            "warnings": warnings,
        }
        summary = {"metrics": self.TEST_NAME, "value": final}

        test.add_key_value_to_json(self.result_json_path, "detail.cycle", value=cycle_result)
        test.add_key_value_to_json(self.result_json_path, "summary", value=summary)

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for r in component_results:
                f.write(f"{r['component']},{r['matched_name'] or ''},{r['version'] or ''},{r['result']}\n")

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
        checker = FirmwareVersionCheck("FirmwareVersionCheck")
        checker.run_test()
        exit_code = getattr(checker, "exit_code", 0)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception:
        traceback.print_exc()
        exit_code = 1
    sys.exit(exit_code)
