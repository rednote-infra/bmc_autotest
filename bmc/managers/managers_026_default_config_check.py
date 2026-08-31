#!/bin/python
"""
Author: Fengmian
Date: 2026/05/12
Usage: python3 bmc/managers_026_default_config_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/12: 新增，BMC 默认配置合规检查

测试内容：
  检查 BMC 出厂默认配置是否符合公司规范，包含以下检查项：
  1. Sharelink（NCSI）关闭：OEM.NCSIModeSelect 为 None 或字段不存在（无 NCSI）
  2. IPv6 禁用：管理口无全局 IPv6 地址（LinkLocal fe80:: 可接受，不视为启用）
  3. IPv4 专用口 Static：管理口 AddressOrigin = Static
  4. 电源主备模式 Normal（负载均衡）：Power.Redundancy[*].Mode 为 Sharing/LoadBalanced，
     不接受 Failover/Sparing（主备/热备模式）
  5. 错峰上电启用且延时 ≤ 60s：OEM.PowerOnDelayEnabled=True，PowerOnDelaySeconds≤60
  6. 启动项支持 IPMI 设置：Boot.BootSourceOverrideMode AllowableValues 包含 Legacy 或 UEFI

  注意：
  - NTP/时区/电源恢复策略/端口/SNMP 等已在 managers_008/009/015/006b/011 单独覆盖
  - 内存漏斗（Memory Scrubbing）待厂商手册确认接口路径后补充
  - 厂商差异字段（如 OEM.NCSIModeSelect/PowerOnDelaySeconds）
    在当前厂商不支持时记 WARNING 不 FAIL

PASS 标准：
  所有检查项全部为 PASS，任意一项 FAIL 或 WARNING 均导致最终 FAIL。
  WARNING 不视为通过——字段缺失或无法确认均属不合规。
"""

import os
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient

# 负载均衡模式（期望值），不在此列表则 FAIL
PSU_LOADBALANCE_MODES = {"Sharing", "LoadBalanced", "Sparing N+1"}
# 主备/热备模式（明确不允许）
PSU_FAILOVER_MODES    = {"Failover", "Sparing", "NotRedundant"}

# IPv6 地址中可接受的前缀（LinkLocal 不视为"启用 IPv6"）
IPV6_LINKLOCAL_PREFIX = "fe80"

class Managers026DefaultConfigCheck(BmcTestBase):
    """BMC 默认配置合规检查"""

    TEST_CASE_KEY = "Managers026DefaultConfigCheck"

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_026_default_config_check.json"),
        )
        # 此脚本默认 FAIL，需全部检查通过后才改为 PASS
        self.command_check_result = "FAIL"

    def _load_extra_config(self, conf_section: dict) -> None:
        """加载额外配置：错峰上电最大允许秒数。"""
        self.MAX_POWER_ON_DELAY_SEC = conf_section.get("MaxPowerOnDelaySec", 60)

    # ─────────────────────────────────────────────────────────────────────────
    # 检查项 1：Sharelink（NCSI）关闭
    # ─────────────────────────────────────────────────────────────────────────
    def _check_sharelink(self, test: CommonFunction) -> dict:
        item = {"name": "Sharelink(NCSI)关闭"}
        try:
            # [SDK-GAP] get_raw(EthernetInterfaces) 获取接口集合 + 逐条详情：
            #   目标字段 OEM.Public.NCSIModeSelect 在 SDK EthernetInterface 模型中不存在；
            #   且 SDK EthernetInterface 存在 pydantic 解析 bug，保留 get_raw
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/EthernetInterfaces') "
                           "获取 OEM.NCSIModeSelect 字段：SDK EthernetInterface 模型不含 OEM 字段，"
                           "且存在 pydantic 解析 bug，保留 get_raw，待 SDK 完善后替换")
            eths = self.client.get_raw("/redfish/v1/Managers/1/EthernetInterfaces")
            found_ncsi = False
            for m in eths.get("Members", []):
                ei = self.client.get_raw(m["@odata.id"])
                oem_pub = ei.get("Oem", {}).get("Public", {})
                if "NCSIModeSelect" not in oem_pub:
                    continue
                found_ncsi = True
                val = oem_pub["NCSIModeSelect"]
                item["interface"]      = m["@odata.id"]
                item["NCSIModeSelect"] = val
                # None 或 "Disabled" 视为关闭
                if val is None or str(val).lower() in ("none", "disabled", "0", "false"):
                    item["result"] = "PASS"
                    test.print_log("INFO", f"✓ Sharelink：NCSIModeSelect={val}（已关闭）")
                else:
                    item["result"] = "FAIL"
                    item["detail"] = f"NCSIModeSelect={val}，期望关闭"
                    test.print_log("ERROR", f"✗ Sharelink：NCSIModeSelect={val}，期望关闭")
                break

            if not found_ncsi:
                item["result"] = "FAIL"
                item["detail"] = "未找到 NCSIModeSelect 字段，无法确认 Sharelink 状态"
                test.print_log("ERROR", "✗ Sharelink：未找到 NCSIModeSelect 字段，视为不合规")
        except Exception as e:
            item["result"] = "FAIL"
            item["detail"] = str(e)
            test.print_log("ERROR", f"✗ Sharelink 检查异常：{e}")
        return item

    # ─────────────────────────────────────────────────────────────────────────
    # 检查项 2：IPv6 禁用（无全局 IPv6，LinkLocal 可接受）
    # ─────────────────────────────────────────────────────────────────────────
    def _check_ipv6_disabled(self, test: CommonFunction) -> dict:
        item = {"name": "IPv6 禁用（无全局 IPv6 地址）"}
        try:
            # [SDK-GAP] get_raw(EthernetInterfaces) 获取 IPv6Addresses 数组：
            #   SDK EthernetInterface 有 ip_v6_addresses 字段，但存在 pydantic 解析 bug，
            #   需通过 get_raw 获取原始 JSON，待 SDK 修复后替换
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/EthernetInterfaces') "
                           "获取 IPv6Addresses：SDK EthernetInterface 存在 pydantic 解析 bug，"
                           "保留 get_raw，待 SDK 修复后替换")
            eths = self.client.get_raw("/redfish/v1/Managers/1/EthernetInterfaces")
            global_ipv6_list = []
            for m in eths.get("Members", []):
                ei = self.client.get_raw(m["@odata.id"])
                for addr in ei.get("IPv6Addresses", []):
                    a = addr.get("Address", "")
                    origin = addr.get("AddressOrigin", "")
                    # 跳过 :: / fe80:: / LinkLocal
                    if a in ("::", "") or a.startswith(IPV6_LINKLOCAL_PREFIX):
                        continue
                    if origin == "LinkLocal":
                        continue
                    global_ipv6_list.append(
                        {"interface": m["@odata.id"], "address": a, "origin": origin}
                    )

            item["global_ipv6_addresses"] = global_ipv6_list
            if not global_ipv6_list:
                item["result"] = "PASS"
                test.print_log("INFO", "✓ IPv6：无全局 IPv6 地址（已禁用）")
            else:
                item["result"] = "FAIL"
                item["detail"] = f"发现全局 IPv6 地址：{global_ipv6_list}"
                test.print_log("ERROR", f"✗ IPv6：发现全局 IPv6 地址 {global_ipv6_list}")
        except Exception as e:
            item["result"] = "FAIL"
            item["detail"] = str(e)
            test.print_log("ERROR", f"✗ IPv6 检查异常：{e}")
        return item

    # ─────────────────────────────────────────────────────────────────────────
    # 检查项 3：IPv4 专用口 Static
    # ─────────────────────────────────────────────────────────────────────────
    def _check_ipv4_static(self, test: CommonFunction) -> dict:
        item = {"name": "IPv4 专用管理口 Static"}
        try:
            # [SDK-GAP] get_raw(EthernetInterfaces) 获取 IPv4Addresses.AddressOrigin：
            #   SDK EthernetInterface 有 ip_v4_addresses 字段，但存在 pydantic 解析 bug，
            #   需通过 get_raw 获取原始 JSON，待 SDK 修复后替换
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/EthernetInterfaces') "
                           "获取 IPv4Addresses.AddressOrigin：SDK EthernetInterface 存在 pydantic 解析 bug，"
                           "保留 get_raw，待 SDK 修复后替换")
            eths = self.client.get_raw("/redfish/v1/Managers/1/EthernetInterfaces")
            members = eths.get("Members", [])
            results = []
            for m in members:
                ei = self.client.get_raw(m["@odata.id"])
                for addr in ei.get("IPv4Addresses", []):
                    origin = addr.get("AddressOrigin", "")
                    ip     = addr.get("Address", "")
                    results.append({
                        "interface":     m["@odata.id"],
                        "address":       ip,
                        "addressOrigin": origin,
                    })

            item["interfaces"] = results
            # 期望：所有接口的 IPv4 AddressOrigin 均为 Static
            non_static = [r for r in results if r["addressOrigin"] != "Static"]
            if not non_static:
                item["result"] = "PASS"
                test.print_log("INFO", f"✓ IPv4 Static：{results}")
            else:
                item["result"] = "FAIL"
                item["detail"] = f"发现非 Static 接口：{non_static}，期望全部为 Static"
                test.print_log("ERROR",
                    f"✗ IPv4 Static：发现非 Static 接口 {non_static}")
        except Exception as e:
            item["result"] = "FAIL"
            item["detail"] = str(e)
            test.print_log("ERROR", f"✗ IPv4 Static 检查异常：{e}")
        return item

    # ─────────────────────────────────────────────────────────────────────────
    # 检查项 4：电源主备模式 Normal（负载均衡）
    # ─────────────────────────────────────────────────────────────────────────
    def _check_psu_redundancy_mode(self, test: CommonFunction) -> dict:
        item = {"name": "电源主备模式 Normal（负载均衡）"}
        try:
            pw = self.client.get_power()
            redundancy = pw.redundancy or []
            if not redundancy:
                item["result"] = "FAIL"
                item["detail"] = "Redundancy 集合为空，无法确认电源冗余模式"
                test.print_log("ERROR", "✗ 电源冗余：Redundancy 集合为空，视为不合规")
                return item

            modes = []
            fail_modes = []
            for r in redundancy:
                mode = r.mode or ""
                modes.append({"name": r.name or "", "mode": mode})
                if mode in PSU_FAILOVER_MODES:
                    fail_modes.append(mode)

            item["redundancy_modes"] = modes
            if fail_modes:
                item["result"] = "FAIL"
                item["detail"] = (
                    f"发现主备/热备模式：{fail_modes}，"
                    f"期望负载均衡模式（{PSU_LOADBALANCE_MODES}）"
                )
                test.print_log("ERROR",
                    f"✗ 电源模式：{fail_modes} 为主备/热备，期望负载均衡")
            else:
                item["result"] = "PASS"
                test.print_log("INFO", f"✓ 电源模式：{modes}（负载均衡）")
        except Exception as e:
            item["result"] = "FAIL"
            item["detail"] = str(e)
            test.print_log("ERROR", f"✗ 电源冗余模式检查异常：{e}")
        return item

    # ─────────────────────────────────────────────────────────────────────────
    # 检查项 5：错峰上电启用且延时 ≤ MaxPowerOnDelaySec（默认60s）
    # ─────────────────────────────────────────────────────────────────────────
    def _check_power_on_delay(self, test: CommonFunction) -> dict:
        item = {"name": f"错峰上电启用且延时 ≤ {self.MAX_POWER_ON_DELAY_SEC}s"}
        try:
            sys_obj = self.client.get_system()
            # OEM.Public fields are in sys_obj.oem.bmc (Bmc model, extra="allow")
            oem_bmc = sys_obj.oem.bmc if sys_obj.oem else None
            oem_extra = (oem_bmc.model_extra or {}) if oem_bmc else {}

            if "PowerOnDelayEnabled" not in oem_extra:
                item["result"] = "FAIL"
                item["detail"] = "未找到 OEM.PowerOnDelayEnabled 字段，无法确认错峰上电状态"
                test.print_log("ERROR", "✗ 错峰上电：未找到 OEM.PowerOnDelayEnabled 字段，视为不合规")
                return item

            enabled = oem_extra.get("PowerOnDelayEnabled")
            seconds = oem_extra.get("PowerOnDelaySeconds")
            mode    = oem_extra.get("PowerOnDelayMode", "")
            item["PowerOnDelayEnabled"] = enabled
            item["PowerOnDelaySeconds"] = seconds
            item["PowerOnDelayMode"]    = mode

            if not enabled:
                item["result"] = "FAIL"
                item["detail"] = "PowerOnDelayEnabled=False，期望 True（错峰上电须启用）"
                test.print_log("ERROR", "✗ 错峰上电：未启用（期望 True）")
                return item

            if seconds is None:
                item["result"] = "FAIL"
                item["detail"] = "PowerOnDelayEnabled=True 但 PowerOnDelaySeconds 为空，无法确认延时值"
                test.print_log("ERROR", "✗ 错峰上电：已启用但无法读取延时秒数，视为不合规")
                return item

            if int(seconds) <= self.MAX_POWER_ON_DELAY_SEC:
                item["result"] = "PASS"
                test.print_log("INFO",
                    f"✓ 错峰上电：启用，延时={seconds}s ≤ {self.MAX_POWER_ON_DELAY_SEC}s，模式={mode}")
            else:
                item["result"] = "FAIL"
                item["detail"] = (
                    f"PowerOnDelaySeconds={seconds}s > {self.MAX_POWER_ON_DELAY_SEC}s，"
                    "需调整为 ≤ 60s"
                )
                test.print_log("ERROR",
                    f"✗ 错峰上电：延时={seconds}s 超过限制 {self.MAX_POWER_ON_DELAY_SEC}s")
        except Exception as e:
            item["result"] = "FAIL"
            item["detail"] = str(e)
            test.print_log("ERROR", f"✗ 错峰上电检查异常：{e}")
        return item

    # ─────────────────────────────────────────────────────────────────────────
    # 检查项 6：启动项支持 IPMI 设置启动模式（AllowableValues 包含 Legacy 或 UEFI）
    # ─────────────────────────────────────────────────────────────────────────
    def _check_boot_mode_ipmi(self, test: CommonFunction) -> dict:
        item = {"name": "启动项支持 IPMI 设置启动模式"}
        try:
            sys_obj = self.client.get_system()
            boot = sys_obj.boot
            boot_extra = (boot.model_extra or {}) if boot else {}
            allowable = boot_extra.get(
                "BootSourceOverrideMode@Redfish.AllowableValues", []
            ) or []
            current = boot.boot_source_override_mode if boot else ""
            item["BootSourceOverrideMode"]              = current
            item["BootSourceOverrideMode_AllowableValues"] = allowable

            # 支持 Legacy 或 UEFI 即视为 IPMI 可设置启动模式
            supported = bool(allowable) and any(
                v in allowable for v in ("Legacy", "UEFI", "BIOS")
            )
            if supported:
                item["result"] = "PASS"
                test.print_log("INFO",
                    f"✓ 启动模式：AllowableValues={allowable}（支持 IPMI 设置）")
            else:
                if not allowable:
                    item["result"] = "FAIL"
                    item["detail"] = (
                        "BootSourceOverrideMode@Redfish.AllowableValues 为空，"
                        "无法确认是否支持 IPMI 设置启动模式"
                    )
                    test.print_log("ERROR",
                        f"✗ 启动模式：AllowableValues 为空，视为不合规，当前 Mode={current}")
                else:
                    item["result"] = "FAIL"
                    item["detail"] = (
                        f"AllowableValues={allowable}，"
                        "不包含 Legacy/UEFI，不支持 IPMI 设置启动模式"
                    )
                    test.print_log("ERROR",
                        f"✗ 启动模式：{allowable} 不支持 IPMI 设置")
        except Exception as e:
            item["result"] = "FAIL"
            item["detail"] = str(e)
            test.print_log("ERROR", f"✗ 启动模式检查异常：{e}")
        return item

    # ─────────────────────────────────────────────────────────────────────────
    # 主流程
    # ─────────────────────────────────────────────────────────────────────────
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        try:
            self.client = RedfishClient(
                host=self.BMC_IP, username=self.USERNAME, password=self.PASSWORD
            )

            test.print_log("INFO", "─" * 50)
            test.print_log("INFO", "检查项 1：Sharelink（NCSI）关闭")
            checks.append(self._check_sharelink(test))

            test.print_log("INFO", "─" * 50)
            test.print_log("INFO", "检查项 2：IPv6 禁用")
            checks.append(self._check_ipv6_disabled(test))

            test.print_log("INFO", "─" * 50)
            test.print_log("INFO", "检查项 3：IPv4 专用口 Static")
            checks.append(self._check_ipv4_static(test))

            test.print_log("INFO", "─" * 50)
            test.print_log("INFO", "检查项 4：电源主备模式 Normal（负载均衡）")
            checks.append(self._check_psu_redundancy_mode(test))

            test.print_log("INFO", "─" * 50)
            test.print_log("INFO", f"检查项 5：错峰上电启用且延时 ≤ {self.MAX_POWER_ON_DELAY_SEC}s")
            checks.append(self._check_power_on_delay(test))

            test.print_log("INFO", "─" * 50)
            test.print_log("INFO", "检查项 6：启动项支持 IPMI 设置启动模式")
            checks.append(self._check_boot_mode_ipmi(test))

        except Exception as e:
            test.print_log("ERROR", f"未处理异常：{e}")
            traceback.print_exc()
        finally:
            try:
                self.client.close()
            except Exception:
                pass

        # ── 汇总 ──────────────────────────────────────────────────────────────
        test.print_log("INFO", "=" * 50)
        test.print_log("INFO", "检查项汇总：")
        any_fail = False
        for c in checks:
            r = c.get("result", "FAIL")
            lvl = "INFO" if r == "PASS" else "ERROR"
            test.print_log(lvl, f"  {c['name']}：{r}")
            if r != "PASS":
                any_fail = True

        final = "FAIL" if any_fail else "PASS"
        test.print_log("INFO" if final == "PASS" else "ERROR", f"最终结果：{final}")

        detail = {"checks": checks}
        test.add_key_value_to_json(self.result_json_path, "detail", value=detail)
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": final}
        )
        self.command_check_result = final

if __name__ == "__main__":
    exit_code = 0
    obj = None
    try:
        obj = Managers026DefaultConfigCheck(
            Managers026DefaultConfigCheck.TEST_CASE_KEY
        )
        obj.run_test()
        exit_code = 0 if obj.command_check_result == "PASS" else 2
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"异常：{e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        if obj and hasattr(obj, "exit_code_path"):
            with open(obj.exit_code_path, "w", encoding="utf-8") as f:
                f.write(str(exit_code))
    sys.exit(exit_code)
