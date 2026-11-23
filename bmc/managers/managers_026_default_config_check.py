#!/bin/python
"""
Author: Fengmian
Date: 2026/05/12
Usage: python3 bmc/managers_026_default_config_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/12: 新增，BMC 默认配置合规检查
2026/10/16: SDK 1.3.0 适配 - EthernetInterface 改用 get_manager_ethernet_interfaces()
  类型化接口（1.1.1 pydantic 解析 bug 已在 1.3.0 修复），URI 由 SDK 自动发现（issue/8）
2026/11/16: 网口角色改用统一约定定位（func/bmc_network_function.classify_eth_roles）：
  序号最小的网口为专用口（Detect），其余为 Sharelink(NCSI) 口；无 Sharelink 口
  （单管理口机型）时本项不适用记 PASS。电源模式判定改为「均流（负载均衡）」。
  SDK 未暴露的 OEM 字段（如错峰上电）记 WARNING 待 SDK 适配。

测试内容：
  检查 BMC 出厂默认配置是否符合公司规范，包含以下检查项：
  1. Sharelink（NCSI）关闭：Sharelink 口默认关闭；无 Sharelink 口的机型本项不适用
  2. IPv6 禁用：管理口无全局 IPv6 地址（LinkLocal fe80:: 可接受，不视为启用）
  3. IPv4 专用口 Static：专用管理口 AddressOrigin = Static
  4. 电源模式=均流（负载均衡）：Power.Redundancy[*].Mode 为 Sharing/LoadBalanced，
     不接受 Failover/Sparing 等主备/热备模式
     （SDK 未暴露 Power.Redundancy 时记 WARNING，待 SDK 适配）
  5. 错峰上电启用且延时 ≤ 60s：OEM.PowerOnDelayEnabled=True，PowerOnDelaySeconds≤60
     （SDK 未暴露该字段时记 WARNING，待 SDK 适配）
  6. 启动项支持 IPMI 设置：Boot.BootSourceOverrideMode AllowableValues 包含 Legacy 或 UEFI

  注意：
  - NTP/时区/电源恢复策略/端口/SNMP 等已在 managers_008/009/015/006b/011 单独覆盖
  - 仅使用 SDK 类型化接口，不使用 get_raw 绕过
  - 内存漏斗（Memory Scrubbing）待厂商手册确认接口路径后补充

PASS 标准：
  所有检查项 PASS → PASS；仅 WARNING（如 SDK 未暴露字段）→ PASS 含 WARNING；
  任一 FAIL → FAIL。
"""

import os
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.bmc_network_function import find_sharelink_ifaces, find_detect_ifaces

from redfish_sdk import RedfishClient

# 均流（负载均衡）模式——我方期望的默认电源模式
PSU_LOADBALANCE_MODES = {"Sharing", "LoadBalanced"}
# 非均流模式（主备/热备/无冗余，明确不符合我方规范）
PSU_NON_LOADBALANCE_MODES = {"Failover", "Sparing", "NotRedundant", "Active/Standby"}

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
            # SDK 1.3.0 类型化接口
            eths = self.client.get_manager_ethernet_interfaces()
            # 按约定定位网口角色：序号最小 = 专用口(Detect)，其余 = Sharelink(NCSI) 口
            sharelink = find_sharelink_ifaces(eths)
            item["total_interfaces"] = len(eths)
            item["sharelink_ifaces"] = [getattr(i, "id", None) or getattr(i, "odata_id", "")
                                        for i in sharelink]

            if not sharelink:
                # 单网口机型（或未暴露 Sharelink 口）→ 无 Sharelink 口需检查，视为合规
                item["result"] = "PASS"
                item["detail"] = "无 Sharelink(NCSI) 口（单管理口机型或未暴露），不适用"
                test.print_log("INFO",
                    "✓ Sharelink：未发现 Sharelink 口（共 %d 个网口），本项不适用" % len(eths))
                return item

            # Sharelink 口须默认关闭（InterfaceEnabled != True）
            enabled_sharelink = [i for i in sharelink
                                 if getattr(i, "interface_enabled", None) is True]
            if not enabled_sharelink:
                item["result"] = "PASS"
                test.print_log("INFO",
                    "✓ Sharelink：%d 个 Sharelink 口均已关闭" % len(sharelink))
            else:
                item["result"] = "FAIL"
                item["detail"] = (
                    "Sharelink 口处于启用状态："
                    + ", ".join((getattr(i, "id", None) or getattr(i, "odata_id", ""))
                                for i in enabled_sharelink)
                    + "（我方规范要求默认关闭）"
                )
                test.print_log("ERROR", "✗ Sharelink：" + item["detail"])
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
            # SDK 1.3.0 类型化接口（1.1.1 pydantic 解析 bug 已修复）
            eths = self.client.get_manager_ethernet_interfaces()
            global_ipv6_list = []
            for ei in eths:
                for addr in (ei.ipv6_addresses or []):
                    a = addr.address or ""
                    origin = addr.address_origin or ""
                    # 跳过 :: / fe80:: / LinkLocal
                    if a in ("::", "") or a.startswith(IPV6_LINKLOCAL_PREFIX):
                        continue
                    if origin == "LinkLocal":
                        continue
                    global_ipv6_list.append(
                        {"interface": ei.odata_id, "address": a, "origin": origin}
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
        """IPv4 检查：仅校验专用管理口（Detect），Sharelink 口不参与。"""
        item = {"name": "IPv4 专用管理口 Static"}
        try:
            # SDK 1.3.0 类型化接口（1.1.1 pydantic 解析 bug 已修复）
            eths = self.client.get_manager_ethernet_interfaces()
            # 仅取专用口（Detect）：序号最小的网口
            detect = find_detect_ifaces(eths)
            results = []
            for ei in detect:
                for addr in (ei.ipv4_addresses or []):
                    results.append({
                        "interface":     ei.odata_id,
                        "address":       addr.address or "",
                        "addressOrigin": addr.address_origin or "",
                    })

            item["interfaces"] = results
            if not results:
                item["result"] = "FAIL"
                item["detail"] = "专用管理口未配置 IPv4 地址"
                test.print_log("ERROR", "✗ IPv4：专用管理口未配置 IPv4 地址")
                return item

            # 期望：专用口 IPv4 AddressOrigin 均为 Static
            non_static = [r for r in results if r["addressOrigin"] != "Static"]
            if not non_static:
                item["result"] = "PASS"
                test.print_log("INFO", f"✓ IPv4 Static：{results}")
            else:
                item["result"] = "FAIL"
                item["detail"] = f"专用管理口发现非 Static：{non_static}，期望 Static"
                test.print_log("ERROR",
                    f"✗ IPv4 Static：专用管理口发现非 Static {non_static}")
        except Exception as e:
            item["result"] = "FAIL"
            item["detail"] = str(e)
            test.print_log("ERROR", f"✗ IPv4 Static 检查异常：{e}")
        return item

    # ─────────────────────────────────────────────────────────────────────────
    # 检查项 4：电源主备模式 Normal（负载均衡）
    # ─────────────────────────────────────────────────────────────────────────
    def _check_psu_redundancy_mode(self, test: CommonFunction) -> dict:
        """电源模式检查：我方期望均流（负载均衡），不接受主备/热备等冗余模式。"""
        item = {"name": "电源模式（均流/负载均衡）"}
        try:
            # 仅使用 SDK 类型化接口（issue/21：不接受 get_raw 绕过）
            pw = self.client.get_power()
            redundancy = pw.redundancy or []
            if not redundancy:
                # SDK 当前未暴露该字段 → 无法判定，记 WARNING 等 SDK 适配（issue/21）
                item["result"] = "WARNING"
                item["detail"] = "SDK 未返回 Power.Redundancy，无法判定（待 SDK 适配）"
                test.print_log("WARNING",
                    "⚠ 电源模式：SDK 未返回 Power.Redundancy，无法判定，待 SDK 适配")
                return item

            modes = []
            non_lb = []
            for r in redundancy:
                mode = r.mode or ""
                modes.append({"name": r.name or "", "mode": mode})
                if mode not in PSU_LOADBALANCE_MODES:
                    non_lb.append(mode)

            item["redundancy_modes"] = modes
            if non_lb:
                item["result"] = "FAIL"
                item["detail"] = (
                    f"发现非均流模式：{non_lb}，"
                    f"期望均流模式（{sorted(PSU_LOADBALANCE_MODES)}）"
                )
                test.print_log("ERROR",
                    f"✗ 电源模式：{non_lb} 非均流模式，期望均流（负载均衡）")
            else:
                item["result"] = "PASS"
                test.print_log("INFO", f"✓ 电源模式：{modes}（均流/负载均衡）")
        except Exception as e:
            item["result"] = "FAIL"
            item["detail"] = str(e)
            test.print_log("ERROR", f"✗ 电源模式检查异常：{e}")
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
                # SDK 当前未暴露该 OEM 字段 → 无法判定，记 WARNING 等 SDK 适配（issue/21）
                item["result"] = "WARNING"
                item["detail"] = "SDK 未暴露 OEM.PowerOnDelayEnabled，无法判定（待 SDK 适配）"
                test.print_log("WARNING",
                    "⚠ 错峰上电：SDK 未暴露 OEM.PowerOnDelayEnabled，无法判定，待 SDK 适配")
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
            if not self.prepare_system_client():
                return

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
        # 判定：任一 FAIL → FAIL；仅 WARNING（如 SDK 未暴露字段）→ PASS 含 WARNING
        test.print_log("INFO", "=" * 50)
        test.print_log("INFO", "检查项汇总：")
        any_fail = False
        any_warn = False
        for c in checks:
            r = c.get("result", "FAIL")
            if r == "PASS":
                lvl = "INFO"
            elif r == "WARNING":
                lvl = "WARNING"
                any_warn = True
            else:
                lvl = "ERROR"
                any_fail = True
            test.print_log(lvl, f"  {c['name']}：{r}")

        if any_fail:
            final = "FAIL"
        elif any_warn:
            final = "PASS_WITH_WARNING"
        else:
            final = "PASS"
        test.print_log("INFO" if final != "FAIL" else "ERROR", f"最终结果：{final}")

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
        # PASS / PASS_WITH_WARNING → 0；FAIL → 2
        exit_code = 0 if obj.command_check_result in ("PASS", "PASS_WITH_WARNING") else 2
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
