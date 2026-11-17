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
2026/11/17: 电源模式判定改用 Oem.xFusion.ExpectedRedundancy。
  Power.Redundancy[*].Mode 是固定能力枚举（iBMC 源码按数组下标硬编码，恒为
  [0]=Sharing、[1]=Failover），不能反映实际生效模式，故改为解析
  PowerControl[*].Oem.xFusion.ExpectedRedundancy 的 @odata.id 反查真实模式；
  该字段缺失时降级为 WARNING（按「支持均流」口径），避免非 CMCC 机型恒误判。
  启动项检查补充 Boot.Target@Redfish.AllowableValues（Mode 的 AllowableValues 常为空）。

测试内容：
  检查 BMC 出厂默认配置是否符合公司规范，包含以下检查项：
  1. Sharelink（NCSI）关闭：Sharelink 口默认关闭；无 Sharelink 口的机型本项不适用
  2. IPv6 禁用：管理口无全局 IPv6 地址（LinkLocal fe80:: 可接受，不视为启用）
  3. IPv4 专用口 Static：专用管理口 AddressOrigin = Static
  4. 电源模式=均流（负载均衡）：按 PowerControl[*].Oem.xFusion.ExpectedRedundancy
     指向的冗余组取其 Mode，期望 Sharing/LoadBalanced，不接受 Failover 等主备模式。
     注意 Power.Redundancy[*].Mode 是能力枚举（恒含 Failover），不可直接判定。
  5. 错峰上电启用且延时 ≤ 60s：OEM.PowerOnDelayEnabled=True，PowerOnDelaySeconds≤60
     （xFusion 机型字段名为 PowerRestoreDelayMode/PowerRestoreDelaySeconds）
  6. 启动项支持 IPMI 设置：Boot 的 Target@Redfish.AllowableValues 包含有效启动项

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
        """电源模式检查：我方期望均流（负载均衡），不接受主备/热备等冗余模式。

        判定依据说明（重要）：

        ``Power.Redundancy[*].Mode`` **不能**直接用于判断实际生效模式。
        iBMC 源码 ``redfish_provider_chassis_power_mode.c:add_power_redundancy_mode()``
        在非 CMCC 定制分支里按数组下标硬编码：

            (0 == arr_index) ? Sharing : Failover

        配合 ``RF_POWER_REDUNDANCY_ARRAY_LEN = 2``，``Redundancy`` 恒为两个元素
        （[0] 恒 Sharing、[1] 恒 Failover），与设备真实工作模式无关。真实模式
        （``PROPERTY_ACTUAL_POWER_MODE``）虽被读出，但仅在 CMCC 定制下使用。

        xFusion 在 OEM 中给出了指向「当前期望生效冗余组」的链接：

            PowerControl[i].Oem.xFusion.ExpectedRedundancy = {"@odata.id": ".../Redundancy/N"}

        反查该 ``@odata.id`` 对应的 ``Redundancy[N].Mode`` 才是真实生效模式。
        另可参考同一 OEM 块中的 ``NormalAndRedundancy``（"Disabled" 表示未启用主备）。
        """
        item = {"name": "电源模式（均流/负载均衡）"}
        try:
            # 仅使用 SDK 类型化接口（issue/21：不接受 get_raw 绕过）
            pw = self.client.get_power()
            redundancy = pw.redundancy or []
            controls = pw.power_control or []
            if not redundancy:
                # SDK 当前未暴露该字段 → 无法判定，记 WARNING 等 SDK 适配（issue/21）
                item["result"] = "WARNING"
                item["detail"] = "SDK 未返回 Power.Redundancy，无法判定（待 SDK 适配）"
                test.print_log("WARNING",
                    "⚠ 电源模式：SDK 未返回 Power.Redundancy，无法判定，待 SDK 适配")
                return item

            # ① 建立 @odata.id -> (Mode, Name) 映射。
            #    SDK 的 Redundancy 模型未声明 odata_id 字段，它落在 model_extra 中。
            id2group = {}
            all_modes = []
            for r in redundancy:
                oid = getattr(r, "odata_id", None) or (r.model_extra or {}).get("@odata.id")
                mode = r.mode or ""
                all_modes.append({"name": r.name or "", "mode": mode, "odata_id": oid})
                if oid:
                    id2group[oid] = {"name": r.name or "", "mode": mode}

            item["redundancy_modes"] = all_modes

            # ② 从 PowerControl[*].Oem.xFusion.ExpectedRedundancy 取出期望生效组。
            #    PowerControl 模型同样未声明 Oem，需走 model_extra。
            effective = []          # [{"name":.., "mode":.., "via":..}]
            unknown_links = []
            for pc in controls:
                extra = pc.model_extra or {}
                oem = extra.get("Oem")
                if not isinstance(oem, dict):
                    continue
                xf = oem.get("xFusion")
                if not isinstance(xf, dict):
                    continue
                exp = xf.get("ExpectedRedundancy")
                oid = exp.get("@odata.id") if isinstance(exp, dict) else None
                if not oid:
                    continue
                grp = id2group.get(oid)
                if grp is None:
                    unknown_links.append(oid)
                    continue
                effective.append({
                    "name": grp["name"],
                    "mode": grp["mode"],
                    "via": "Oem.xFusion.ExpectedRedundancy",
                    "controller": getattr(pc, "member_id", None),
                })

            if effective:
                item["effective_modes"] = effective
                item["judged_by"] = "Oem.xFusion.ExpectedRedundancy"
                non_lb = [e["mode"] for e in effective
                          if e["mode"] not in PSU_LOADBALANCE_MODES]
                if non_lb:
                    item["result"] = "FAIL"
                    item["detail"] = (
                        f"实际生效模式非均流：{non_lb}，"
                        f"期望均流模式（{sorted(PSU_LOADBALANCE_MODES)}）"
                    )
                    test.print_log("ERROR",
                        f"✗ 电源模式：实际生效 {non_lb} 非均流，"
                        f"期望均流（负载均衡）")
                else:
                    item["result"] = "PASS"
                    test.print_log("INFO",
                        f"✓ 电源模式：实际生效 "
                        f"{sorted({e['mode'] for e in effective})}（均流/负载均衡）")
                return item

            # ③ 回退：无法获取 ExpectedRedundancy（非 xFusion OEM / 字段未下发）。
            #    此时 Redundancy[*].Mode 恒含 Failover，不能据此判 FAIL，
            #    否则所有非 CMCC 机型都会误判。按「支持均流」口径判定并降级为 WARNING。
            supported = {m["mode"] for m in all_modes}
            lb_supported = bool(supported & set(PSU_LOADBALANCE_MODES))
            item["supported_modes"] = sorted(supported)
            item["judged_by"] = "Redundancy[*].Mode (能力枚举)"
            if unknown_links:
                item["unresolved_expected_redundancy"] = unknown_links
            if lb_supported:
                item["result"] = "WARNING"
                item["detail"] = (
                    "未下发 Oem.xFusion.ExpectedRedundancy，无法判定实际生效模式；"
                    f"Redundancy 能力枚举含均流（{sorted(supported)}），按支持均流处理"
                )
                test.print_log("WARNING",
                    "⚠ 电源模式：未下发 ExpectedRedundancy，无法确认实际模式；"
                    f"能力枚举含均流 {sorted(supported)}")
            else:
                item["result"] = "FAIL"
                item["detail"] = (
                    f"未下发 ExpectedRedundancy 且能力枚举不含均流：{sorted(supported)}"
                )
                test.print_log("ERROR",
                    f"✗ 电源模式：能力枚举不含均流 {sorted(supported)}")
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
            # OEM 字段可能在 oem.bmc(Public) 或 oem.xFusion(厂商块) 中
            oem_extra = {}
            if sys_obj.oem:
                for attr_name in ("bmc", "xfusion"):
                    blk = getattr(sys_obj.oem, attr_name, None)
                    if blk is not None:
                        oem_extra.update(getattr(blk, "model_extra", None) or {})
                # 兜底：直接读 model_extra（厂商键原样保留时）
                raw = (sys_obj.oem.model_extra or {})
                for vendor in ("Public", "BMC", "xFusion"):
                    if isinstance(raw.get(vendor), dict):
                        oem_extra.update(raw[vendor])

            # 字段名映射：
            #   Lenovo/HPE 风格：PowerOnDelayEnabled / PowerOnDelaySeconds
            #   xFusion 风格   ：PowerRestoreDelayMode / PowerRestoreDelaySeconds
            if "PowerOnDelayEnabled" in oem_extra:
                enabled = oem_extra.get("PowerOnDelayEnabled")
                seconds = oem_extra.get("PowerOnDelaySeconds")
                mode    = oem_extra.get("PowerOnDelayMode", "")
                src     = "PowerOnDelayEnabled"
            elif "PowerRestoreDelayMode" in oem_extra:
                mode    = oem_extra.get("PowerRestoreDelayMode", "")
                seconds = oem_extra.get("PowerRestoreDelaySeconds")
                # "DefaultDelay"/"UserDefined" 表示启用错峰；"Disabled" 表示关闭
                enabled = str(mode).lower() not in ("disabled", "none", "")
                src     = "PowerRestoreDelayMode"
            else:
                item["result"] = "SKIP"
                item["detail"] = ("机型未下发 PowerOnDelayEnabled/"
                                  "PowerRestoreDelayMode，无法判定错峰上电")
                test.print_log("WARNING",
                    "错峰上电：机型未下发相关字段，计 SKIP")
                return item

            item["source"]              = src
            item["PowerOnDelayEnabled"] = enabled
            item["PowerOnDelaySeconds"] = seconds
            item["PowerOnDelayMode"]    = mode

            if not enabled:
                item["result"] = "FAIL"
                item["detail"] = f"{src} 显示错峰上电未启用（mode={mode}）"
                test.print_log("ERROR", f"✗ 错峰上电：未启用（mode={mode}）")
                return item

            if seconds is None:
                item["result"] = "FAIL"
                item["detail"] = "已启用但无法读取延时秒数，无法确认"
                test.print_log("ERROR", "✗ 错峰上电：已启用但无法读取延时秒数")
                return item

            if float(seconds) <= self.MAX_POWER_ON_DELAY_SEC:
                item["result"] = "PASS"
                test.print_log("INFO",
                    f"✓ 错峰上电：启用，延时={seconds}s ≤ "
                    f"{self.MAX_POWER_ON_DELAY_SEC}s，模式={mode}（字段来源：{src}）")
            else:
                item["result"] = "FAIL"
                item["detail"] = (
                    f"延时={seconds}s > {self.MAX_POWER_ON_DELAY_SEC}s，需调整为 ≤ 60s"
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

            # SDK 已将 BootSourceOverrideTarget@Redfish.AllowableValues 建模为
            # boot.allowable_values（带 @ 的键不会进入 model_extra）。
            allowable_target = list(getattr(boot, "allowable_values", None) or [])
            if not allowable_target:
                allowable_target = boot_extra.get(
                    "BootSourceOverrideTarget@Redfish.AllowableValues", []
                ) or []
            allowable_mode = boot_extra.get(
                "BootSourceOverrideMode@Redfish.AllowableValues", []
            ) or []
            current = boot.boot_source_override_mode if boot else ""
            item["BootSourceOverrideMode"]                   = current
            item["BootSourceOverrideMode_AllowableValues"]   = allowable_mode
            item["BootSourceOverrideTarget_AllowableValues"] = allowable_target

            # 判定：支持 Legacy/UEFI/BIOS 说明可切启动模式；
            #       或有非 None 的 OverrideTarget 说明可用 IPMI 指定启动设备。
            mode_supported = any(
                v in allowable_mode for v in ("Legacy", "UEFI", "BIOS")
            )
            target_supported = any(
                v not in (None, "", "None") for v in allowable_target
            )
            supported = mode_supported or target_supported

            if supported:
                item["result"] = "PASS"
                test.print_log("INFO",
                    f"✓ 启动模式：Mode AllowableValues={allowable_mode}，"
                    f"Target AllowableValues={allowable_target}（支持 IPMI 设置）")
            else:
                item["result"] = "FAIL"
                item["detail"] = (
                    f"Mode AllowableValues={allowable_mode}、"
                    f"Target AllowableValues={allowable_target}，均无可选项，"
                    f"无法通过 IPMI 设置启动模式"
                )
                test.print_log("ERROR",
                    f"✗ 启动模式：无可选项，当前 Mode={current}")
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
        # 判定：任一 FAIL → FAIL；仅 WARNING（如 SDK 未暴露字段）→ PASS 含 WARNING
        test.print_log("INFO", "=" * 50)
        test.print_log("INFO", "检查项汇总：")
        any_fail = False
        any_warn = False
        skip_count = 0
        for c in checks:
            r = c.get("result", "FAIL")
            if r == "PASS":
                lvl = "INFO"
            elif r in ("WARNING", "SKIP"):
                lvl = "WARNING"
                any_warn = True
                skip_count += 1
            else:
                lvl = "ERROR"
                any_fail = True
            test.print_log(lvl, f"  {c['name']}：{r}")

        if any_fail:
            final = "FAIL"
        else:
            final = "PASS"
        if skip_count:
            test.print_log("INFO",
                f"（其中 {skip_count} 项为 SKIP/WARNING：机型未提供该能力或 SDK 未暴露，不计入不合规）")
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
