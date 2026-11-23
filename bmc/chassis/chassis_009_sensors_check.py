#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/chassis_009_sensors_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增
2026/05/13: 新增必要传感器组覆盖检查（按硬件类型）
2026/06/08: 重写——去除厂商适配/降级逻辑，全量使用 SDK 标准接口
2026/06/10: 引入必要/非必要传感器分级；阶段二改用"有效读值"名称列表，修补覆盖检查漏洞
2026/11/16: 空槽位（Status.State=Absent，未安装硬件）按跳过处理，不再误报 FAIL；
            传感器组引入 severity 分级（硬盘温度/PCIe卡温度降级为 WARNING，
            CPU/内存/风扇/PSU/功耗仍为 ERROR）；PCIe卡温度关键字去 riser、加 slot

Sensor 集合来源：
  - 温度传感器：get_thermal().temperatures  → Temperature 列表
  - 风扇转速：  get_thermal().fans          → Fan 列表
  - 电压传感器：get_power().voltages        → Voltage 列表
  - 功耗控制：  get_power().power_control   → PowerControl 列表
  - 电源模块：  get_power_supplies()        → PowerSupply 列表

校验分两阶段：
  阶段一：字段合规校验（逐条，区分必要/非必要）
    空槽位（Status.State == "Absent"，未安装硬件）→ SKIP，不计入 PASS/FAIL
    必要传感器：name 命中 RequiredSensorGroups 的 keywords（temperature）；
              或整体必要类型（fan/psu/power_control）
    非必要传感器：其余所有传感器
    必要传感器字段异常 → 按组 severity：ERROR → FAIL；WARNING → 降级 WARNING
    非必要传感器字段异常 → WARNING，不计入整体 FAIL

  阶段二：必要传感器组覆盖检查（"已安装且有有效读值"名称列表）
    每组通过关键字匹配【已安装且有有效读值】的传感器名称，至少匹配 1 条即满足
    severity=ERROR 的组缺失 → FAIL；severity=WARNING 的组缺失 → WARNING
    （空槽位不计入有效读值，避免用未装硬件的槽位假 PASS）

整体结果：
  任意 FAIL（ERROR 级字段异常 / ERROR 级组缺失）→ FAIL
  仅 WARNING → PASS_WITH_WARNING（runner 记为 PASS 含 WARNING）
  均无 → PASS
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

VALID_HEALTH = {"OK", "Warning", "Critical"}


class SensorsInfoCheck(BmcTestBase):
    """Chassis Sensor 集合资源信息检查

    用例编号：Redfish_Chassis_009
    检查项：
      1. 温度/风扇/电压/功耗/电源模块传感器集合非空，逐条字段校验（必要/非必要分级）
         空槽位（Status.State=Absent）视为未安装硬件，跳过不计
      2. 必要传感器组覆盖检查（按配置，仅统计"已安装且有有效读值"的传感器）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_009_sensors_check.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        # 必要传感器组覆盖检查配置，格式：{组名: {source, keywords, description}}
        self.REQUIRED_SENSOR_GROUPS = conf_section.get("RequiredSensorGroups", {})

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
            except Exception:
                pass

    # ── 必要传感器判定辅助 ─────────────────────────────────────────────────────

    @staticmethod
    def _is_absent(status) -> bool:
        """判断传感器槽位是否未安装硬件（Status.State == 'Absent'）。

        空槽位（未插硬件）不应判为故障——按"未安装"跳过，不计入 PASS/FAIL。
        """
        return status is not None and getattr(status, "state", None) == "Absent"

    def _resolve_group(self, sensor_name: str, source: str) -> dict:
        """匹配传感器所属的必要传感器组，返回该组配置（含 severity）；非必要返回 None。

        - source 不匹配任何组        → None（非必要传感器）
        - 命中组且 keywords 为空      → 整体必要（fan/psu/power_control）
        - 命中组且名称含任一 keyword  → 该组必要
        """
        name_lower = (sensor_name or "").lower()
        for cfg in self.REQUIRED_SENSOR_GROUPS.values():
            if cfg.get("source", "") != source:
                continue
            keywords = [k.lower() for k in cfg.get("keywords", [])]
            if not keywords:
                return cfg
            if any(kw in name_lower for kw in keywords):
                return cfg
        return None

    def _group_for_source(self, source: str) -> dict:
        """返回某 source 的整体必要组配置（keywords 为空），无则 None。"""
        for cfg in self.REQUIRED_SENSOR_GROUPS.values():
            if cfg.get("source", "") == source and not cfg.get("keywords"):
                return cfg
        return None

    @staticmethod
    def _severity_of(group_cfg) -> str:
        """取组配置的告警级别，默认 ERROR；非必要传感器返回 WARNING。"""
        return (group_cfg.get("severity") or "ERROR").upper() if group_cfg else "WARNING"

    # ── 温度传感器校验 ──────────────────────────────────────────────────────

    def _check_temperature(self, temp, severity: str, is_mandatory: bool) -> tuple:
        """校验单条温度传感器。

        :param severity: 该传感器所属组的告警级别（ERROR / WARNING）
        :param is_mandatory: 是否为必要传感器组
        :return: (check_result, has_valid_reading, info_dict)
          check_result      — PASS / FAIL / WARNING / SKIP
          has_valid_reading — ReadingCelsius 是否有效（用于阶段二统计）
          info_dict         — 传感器信息字典
        """
        sensor_id   = temp.member_id or temp.name or "unknown"
        sensor_name = temp.name or sensor_id
        ctx         = temp.physical_context or "未知区域"
        tag         = "[必要]" if is_mandatory else "[非必要]"

        # 空槽位（未安装硬件）→ 跳过，不计入判定
        if self._is_absent(temp.status):
            CommonFunction.print_log(
                "INFO", f"[Temp]{tag} {sensor_name}：Status.State=Absent（未安装硬件），跳过"
            )
            return "SKIP", False, {
                "type": "temperature", "member_id": sensor_id, "name": sensor_name,
                "physical_context": ctx, "is_mandatory": is_mandatory, "severity": severity,
                "reading": None, "unit": "Celsius", "health": "", "check_result": "SKIP",
            }

        # 异常记录级别：ERROR 级必要组 → ERROR；其余 → WARNING
        log_fail   = "ERROR" if (is_mandatory and severity == "ERROR") else "WARNING"
        field_pass = True

        CommonFunction.print_log(
            "INFO",
            f"[Temp]{tag} MemberId={sensor_id} Name={sensor_name} PhysicalContext={ctx}"
        )

        if not temp.member_id:
            CommonFunction.print_log(log_fail, f"[Temp]{tag} {sensor_name}：MemberId 为空")
            field_pass = False
        if not temp.name:
            CommonFunction.print_log(log_fail, f"[Temp]{tag} {sensor_name}：Name 为空")
            field_pass = False

        has_valid_reading = (
            temp.reading_celsius is not None
            and isinstance(temp.reading_celsius, (int, float))
            and temp.reading_celsius >= -273.15
        )
        if not has_valid_reading:
            if temp.reading_celsius is None or not isinstance(temp.reading_celsius, (int, float)):
                CommonFunction.print_log(
                    log_fail,
                    f"[Temp]{tag} {sensor_name}：ReadingCelsius={temp.reading_celsius}，非有效数值"
                )
            else:
                CommonFunction.print_log(
                    log_fail,
                    f"[Temp]{tag} {sensor_name}：ReadingCelsius={temp.reading_celsius}°C，低于绝对零度"
                )
            field_pass = False
        else:
            CommonFunction.print_log(
                "INFO",
                f"[Temp]{tag} {sensor_name}：ReadingCelsius={temp.reading_celsius}°C"
            )

        health = temp.status.health if temp.status else None
        if health != "OK":
            CommonFunction.print_log(
                log_fail,
                f"[Temp]{tag} {sensor_name}：Status.Health={health}，期望 OK"
            )
            field_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Temp]{tag} {sensor_name}：Status.Health={health}")

        if field_pass:
            check_result = "PASS"
        else:
            check_result = "FAIL" if log_fail == "ERROR" else "WARNING"
        return check_result, has_valid_reading, {
            "type": "temperature",
            "member_id": sensor_id,
            "name": sensor_name,
            "physical_context": ctx,
            "is_mandatory": is_mandatory,
            "severity": severity,
            "reading": temp.reading_celsius,
            "unit": "Celsius",
            "health": health or "",
            "check_result": check_result,
        }

    # ── 风扇传感器校验（整体必要）───────────────────────────────────────────

    def _check_fan(self, fan) -> tuple:
        """校验单条风扇传感器（整体必要）。

        :return: (check_result, has_valid_reading, info_dict)
        """
        sensor_id = getattr(fan, "member_id", None) or getattr(fan, "name", None) or "unknown"
        name      = getattr(fan, "name", None)
        reading   = getattr(fan, "reading", None)
        status    = getattr(fan, "status", None)
        health    = status.health if status else None
        field_pass = True

        # 空槽位（未安装风扇）→ 跳过，不计入判定
        if self._is_absent(status):
            CommonFunction.print_log(
                "INFO", f"[Fan][必要] {sensor_id}：Status.State=Absent（未安装风扇），跳过"
            )
            return "SKIP", False, {
                "type": "fan", "member_id": sensor_id, "name": name or "",
                "physical_context": "", "is_mandatory": True, "severity": "ERROR",
                "reading": None, "unit": "RPM", "health": "", "check_result": "SKIP",
            }

        CommonFunction.print_log("INFO", f"[Fan][必要] MemberId={sensor_id} Name={name}")

        if not name:
            CommonFunction.print_log("ERROR", f"[Fan][必要] {sensor_id}：Name 为空")
            field_pass = False

        has_valid_reading = (
            reading is not None
            and isinstance(reading, (int, float))
            and reading >= 0
        )
        if not has_valid_reading:
            CommonFunction.print_log(
                "ERROR",
                f"[Fan][必要] {sensor_id}：Reading={reading}，应为 >= 0 的数值"
            )
            field_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Fan][必要] {sensor_id}：Reading={reading} RPM")

        if health != "OK":
            CommonFunction.print_log(
                "ERROR",
                f"[Fan][必要] {sensor_id}：Status.Health={health}，期望 OK"
            )
            field_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Fan][必要] {sensor_id}：Status.Health={health}")

        return ("PASS" if field_pass else "FAIL"), has_valid_reading, {
            "type": "fan",
            "member_id": sensor_id,
            "name": name or "",
            "physical_context": "",
            "is_mandatory": True,
            "severity": "ERROR",
            "reading": reading,
            "unit": "RPM",
            "health": health or "",
            "check_result": "PASS" if field_pass else "FAIL",
        }

    # ── 电压传感器校验（全部非必要）────────────────────────────────────────

    def _check_voltage(self, volt) -> tuple:
        """校验单条电压传感器（全部非必要，异常只 WARNING）。

        :return: (check_result, has_valid_reading, info_dict)
        """
        sensor_id  = volt.member_id or volt.name or "unknown"
        field_pass = True

        # 空槽位（未安装）→ 跳过
        if self._is_absent(volt.status):
            CommonFunction.print_log(
                "INFO", f"[Volt][非必要] {sensor_id}：Status.State=Absent，跳过"
            )
            return "SKIP", False, {
                "type": "voltage", "member_id": sensor_id, "name": volt.name or "",
                "physical_context": volt.physical_context or "", "is_mandatory": False,
                "severity": "WARNING", "reading": None, "unit": "Volts",
                "health": "", "check_result": "SKIP",
            }

        CommonFunction.print_log("INFO", f"[Volt][非必要] MemberId={sensor_id} Name={volt.name}")

        if not volt.member_id:
            CommonFunction.print_log("WARNING", f"[Volt][非必要] {sensor_id}：MemberId 为空")
            field_pass = False
        if not volt.name:
            CommonFunction.print_log("WARNING", f"[Volt][非必要] {sensor_id}：Name 为空")
            field_pass = False

        has_valid_reading = (
            volt.reading_volts is not None
            and isinstance(volt.reading_volts, (int, float))
        )
        if not has_valid_reading:
            CommonFunction.print_log(
                "WARNING",
                f"[Volt][非必要] {sensor_id}：ReadingVolts={volt.reading_volts}，非有效数值"
            )
            field_pass = False
        else:
            CommonFunction.print_log(
                "INFO",
                f"[Volt][非必要] {sensor_id}：ReadingVolts={volt.reading_volts} V"
            )

        health = volt.status.health if volt.status else None
        if health != "OK":
            CommonFunction.print_log(
                "WARNING",
                f"[Volt][非必要] {sensor_id}：Status.Health={health}，期望 OK"
            )
            field_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Volt][非必要] {sensor_id}：Status.Health={health}")

        return ("PASS" if field_pass else "WARNING"), has_valid_reading, {
            "type": "voltage",
            "member_id": volt.member_id or volt.name or "",
            "name": volt.name or "",
            "physical_context": volt.physical_context or "",
            "is_mandatory": False,
            "severity": "WARNING",
            "reading": volt.reading_volts,
            "unit": "Volts",
            "health": health or "",
            "check_result": "PASS" if field_pass else "WARNING",
        }

    # ── 功耗控制校验（整体必要）────────────────────────────────────────────

    def _check_power_control(self, pc) -> tuple:
        """校验单条功耗控制传感器（整体必要）。

        :return: (check_result, has_valid_reading, info_dict)
        """
        ctrl_id    = pc.member_id or pc.name or "unknown"
        field_pass = True

        CommonFunction.print_log("INFO", f"[PowerCtrl][必要] MemberId={ctrl_id} Name={pc.name}")

        if not pc.member_id:
            CommonFunction.print_log("ERROR", f"[PowerCtrl][必要] {ctrl_id}：MemberId 为空")
            field_pass = False
        if not pc.name:
            CommonFunction.print_log("ERROR", f"[PowerCtrl][必要] {ctrl_id}：Name 为空")
            field_pass = False

        has_valid_reading = (
            pc.power_consumed_watts is not None
            and isinstance(pc.power_consumed_watts, (int, float))
            and pc.power_consumed_watts >= 0
        )
        if not has_valid_reading:
            CommonFunction.print_log(
                "ERROR",
                f"[PowerCtrl][必要] {ctrl_id}：PowerConsumedWatts={pc.power_consumed_watts}，应为 >= 0 的数值"
            )
            field_pass = False
        else:
            CommonFunction.print_log(
                "INFO",
                f"[PowerCtrl][必要] {ctrl_id}：PowerConsumedWatts={pc.power_consumed_watts} W"
            )

        if pc.power_capacity_watts is None or not isinstance(pc.power_capacity_watts, (int, float)) or pc.power_capacity_watts <= 0:
            CommonFunction.print_log(
                "WARNING",
                f"[PowerCtrl][必要] {ctrl_id}：PowerCapacityWatts={pc.power_capacity_watts}，应为正数"
            )
        else:
            CommonFunction.print_log(
                "INFO",
                f"[PowerCtrl][必要] {ctrl_id}：PowerCapacityWatts={pc.power_capacity_watts} W"
            )

        return ("PASS" if field_pass else "FAIL"), has_valid_reading, {
            "type": "power_control",
            "member_id": pc.member_id or "",
            "name": pc.name or "",
            "physical_context": "",
            "is_mandatory": True,
            "severity": "ERROR",
            "reading": pc.power_consumed_watts,
            "power_capacity_watts": pc.power_capacity_watts,
            "unit": "Watts",
            "health": "",
            "check_result": "PASS" if field_pass else "FAIL",
        }

    # ── 电源模块校验（整体必要）────────────────────────────────────────────

    def _check_psu(self, psu) -> tuple:
        """校验单条电源模块（整体必要）。

        :return: (check_result, has_valid_reading, info_dict)
        """
        name       = psu.name or psu.member_id or "unknown"
        health     = psu.status.health if psu.status else None
        field_pass = True

        # 空槽位（未安装 PSU）→ 跳过
        if self._is_absent(psu.status):
            CommonFunction.print_log(
                "INFO", f"[PSU][必要] {name}：Status.State=Absent（未安装电源），跳过"
            )
            return "SKIP", False, {
                "type": "psu", "member_id": name, "name": name, "physical_context": "",
                "is_mandatory": True, "severity": "ERROR", "reading": None,
                "unit": "Watts", "health": "", "check_result": "SKIP",
            }

        CommonFunction.print_log("INFO", f"[PSU][必要] Name={name}")

        if not name:
            CommonFunction.print_log("ERROR", "[PSU][必要] Name 为空")
            field_pass = False
        if health != "OK":
            CommonFunction.print_log(
                "ERROR",
                f"[PSU][必要] {name}：Status.Health={health}，期望 OK"
            )
            field_pass = False
        else:
            CommonFunction.print_log("INFO", f"[PSU][必要] {name}：Status.Health={health}")

        # PSU 以 name 存在且 health=OK 视为有效（无读值字段）
        has_valid_reading = bool(name and name != "unknown")

        return ("PASS" if field_pass else "FAIL"), has_valid_reading, {
            "type": "psu",
            "member_id": name,
            "name": name,
            "physical_context": "",
            "is_mandatory": True,
            "severity": "ERROR",
            "reading": getattr(psu, "power_output_watts", None),
            "unit": "Watts",
            "health": health or "",
            "check_result": "PASS" if field_pass else "FAIL",
        }

    # ── 必要传感器组覆盖检查（阶段二）──────────────────────────────────────

    def _check_required_groups(self,
                               temp_valid_names: list,
                               volt_valid_names: list,
                               pc_valid_names:   list,
                               fan_valid_names:  list,
                               psu_valid_names:  list) -> tuple:
        """阶段二：对每个必要传感器组，在【已安装且有有效读值】名称列表中做关键字匹配。

        severity=ERROR 的组缺失 → ERROR 且计入整体 FAIL；
        severity=WARNING 的组缺失 → WARNING，不计入整体 FAIL。

        :param *_valid_names: 各类型中"已安装 + 有效"的传感器名列表（空槽位已排除）
        :return: (has_error_fail, group_results)
        """
        source_map = {
            "temperature":   temp_valid_names,
            "voltage":       volt_valid_names,
            "power_control": pc_valid_names,
            "fan":           fan_valid_names,
            "psu":           psu_valid_names,
        }
        group_results  = []
        has_error_fail = False

        for group_name, cfg in self.REQUIRED_SENSOR_GROUPS.items():
            source   = cfg.get("source", "")
            keywords = [k.lower() for k in cfg.get("keywords", [])]
            desc     = cfg.get("description", group_name)
            severity = self._severity_of(cfg)
            names    = source_map.get(source, [])
            matched  = (
                [n for n in names if any(kw in n.lower() for kw in keywords)]
                if keywords else names
            )

            if matched:
                result = "PASS"
                CommonFunction.print_log(
                    "INFO",
                    f"[必要传感器组] {group_name}：✓ 找到 {len(matched)} 条（均已安装且有有效读值）"
                )
            elif severity == "ERROR":
                result = "FAIL"
                has_error_fail = True
                CommonFunction.print_log(
                    "ERROR",
                    f"[必要传感器组] {group_name}：未找到已安装且有效读值的传感器"
                    f"（source={source}, keywords={keywords}）"
                )
            else:
                result = "WARNING"
                CommonFunction.print_log(
                    "WARNING",
                    f"[必要传感器组] {group_name}：未找到已安装且有效读值的传感器，"
                    f"降级为 WARNING（source={source}, keywords={keywords}）"
                )
            group_results.append({
                "group":       group_name,
                "description": desc,
                "source":      source,
                "severity":    severity,
                "keywords":    keywords,
                "matched":     matched,
                "result":      result,
            })

        return has_error_fail, group_results

    # ── 主检查流程 ──────────────────────────────────────────────────────────

    def sensors_info_check(self) -> tuple:
        # 字段校验累积结果，元素为 check_result（PASS/FAIL/WARNING/SKIP）
        field_results  = []
        all_sensor_info = []

        # 阶段二：仅统计"已安装且有有效读值"的传感器名（空槽位已排除）
        temp_valid_names = []
        volt_valid_names = []
        pc_valid_names   = []
        fan_valid_names  = []
        psu_valid_names  = []

        # ── 温度 + 风扇（Thermal）──────────────────────────────────────────
        CommonFunction.print_log("INFO", "获取 Thermal 数据（温度传感器 + 风扇）")
        try:
            thermal   = self.client.get_thermal()
            temp_list = thermal.temperatures if thermal and thermal.temperatures else []
            fan_list  = thermal.fans         if thermal and thermal.fans         else []
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"get_thermal() 失败：{str(e)}")
            temp_list, fan_list = [], []
            field_results.append("FAIL")

        if not temp_list:
            CommonFunction.print_log("ERROR", "温度传感器列表为空")
            field_results.append("FAIL")
        else:
            CommonFunction.print_log("INFO", f"温度传感器 {len(temp_list)} 条，开始校验")
            for temp in temp_list:
                sensor_name = temp.name or ""
                grp         = self._resolve_group(sensor_name, "temperature")
                severity    = self._severity_of(grp)
                is_mandatory = grp is not None
                cr, has_valid, info = self._check_temperature(temp, severity, is_mandatory)
                all_sensor_info.append(info)
                if is_mandatory:
                    field_results.append(cr)
                if sensor_name and has_valid:
                    temp_valid_names.append(sensor_name)

        if not fan_list:
            CommonFunction.print_log("ERROR", "风扇传感器列表为空（Thermal.Fans 为空）")
            field_results.append("FAIL")
        else:
            CommonFunction.print_log("INFO", f"风扇传感器 {len(fan_list)} 条，开始校验")
            for fan in fan_list:
                cr, has_valid, info = self._check_fan(fan)
                all_sensor_info.append(info)
                field_results.append(cr)
                n = getattr(fan, "name", None)
                if n and has_valid:
                    fan_valid_names.append(n)

        # ── 电压 + 功耗（Power）───────────────────────────────────────────
        CommonFunction.print_log("INFO", "获取 Power 数据（电压 + 功耗）")
        try:
            power     = self.client.get_power()
            volt_list = power.voltages      if power and power.voltages      else []
            pc_list   = power.power_control if power and power.power_control else []
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"get_power() 失败：{str(e)}")
            volt_list, pc_list = [], []
            field_results.append("FAIL")

        if not volt_list:
            # 电压传感器整体非必要，列表为空只 WARNING
            CommonFunction.print_log("WARNING", "电压传感器列表为空")
        else:
            CommonFunction.print_log("INFO", f"电压传感器 {len(volt_list)} 条，开始校验")
            for volt in volt_list:
                cr, has_valid, info = self._check_voltage(volt)
                all_sensor_info.append(info)
                # 非必要传感器：不计入整体判定
                if volt.name and has_valid:
                    volt_valid_names.append(volt.name)

        if not pc_list:
            CommonFunction.print_log("ERROR", "PowerControl 列表为空")
            field_results.append("FAIL")
        else:
            CommonFunction.print_log("INFO", f"PowerControl {len(pc_list)} 条，开始校验")
            for pc in pc_list:
                cr, has_valid, info = self._check_power_control(pc)
                all_sensor_info.append(info)
                field_results.append(cr)
                if pc.name and has_valid:
                    pc_valid_names.append(pc.name)

        # ── 电源模块（PowerSupplies）──────────────────────────────────────
        CommonFunction.print_log("INFO", "获取 PowerSupplies 数据")
        try:
            psu_list = self.client.get_power_supplies()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"get_power_supplies() 失败：{str(e)}")
            psu_list = []
            field_results.append("FAIL")

        if not psu_list:
            CommonFunction.print_log("ERROR", "PowerSupplies 列表为空")
            field_results.append("FAIL")
        else:
            CommonFunction.print_log("INFO", f"电源模块 {len(psu_list)} 条，开始校验")
            for psu in psu_list:
                cr, has_valid, info = self._check_psu(psu)
                all_sensor_info.append(info)
                field_results.append(cr)
                n = psu.name or psu.member_id
                if n and has_valid:
                    psu_valid_names.append(n)

        # ── 阶段二：必要传感器组覆盖检查（已安装且有有效读值） ──────────────
        CommonFunction.print_log("INFO", "=" * 50)
        CommonFunction.print_log("INFO", "阶段二：必要传感器组覆盖检查（已安装且有有效读值）")
        groups_error_fail, group_results = self._check_required_groups(
            temp_valid_names, volt_valid_names, pc_valid_names,
            fan_valid_names,  psu_valid_names
        )

        # 整体结果：存在 FAIL 字段（ERROR 级）或 ERROR 级组缺失 → FAIL；
        #           仅有 WARNING → PASS（含 WARNING）
        has_fail    = any(r == "FAIL" for r in field_results)
        has_warning = any(r == "WARNING" for r in field_results)
        if has_fail or groups_error_fail:
            final = "FAIL"
        elif has_warning or any(g["result"] == "WARNING" for g in group_results):
            final = "PASS_WITH_WARNING"
        else:
            final = "PASS"

        # 汇总统计
        skip_cnt      = sum(1 for i in all_sensor_info if i.get("check_result") == "SKIP")
        fail_cnt      = sum(1 for i in all_sensor_info if i.get("check_result") == "FAIL")
        warn_cnt      = sum(1 for i in all_sensor_info if i.get("check_result") == "WARNING")
        mandatory_cnt = sum(1 for i in all_sensor_info if i.get("is_mandatory"))

        CommonFunction.print_log(
            "INFO" if final != "FAIL" else "ERROR",
            f"Sensor 检查完成：{final}，共 {len(all_sensor_info)} 个传感器 "
            f"| 必要组: {mandatory_cnt} 个（FAIL {fail_cnt}）"
            f"| WARNING {warn_cnt} 个 | 空槽位跳过 {skip_cnt} 个"
        )
        return final, all_sensor_info, group_results

    # ── 测试入口 ────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, sensor_info_list, group_results = self.sensors_info_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in sensor_info_list:
                f.write(
                    f'"{info["type"]}",'
                    f'"{info["member_id"]}",'
                    f'"{info.get("name", "")}",'
                    f'"{info["physical_context"]}",'
                    f'{"mandatory" if info.get("is_mandatory") else "optional"},'
                    f'{info["reading"]},'
                    f'"{info["unit"]}",'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in sensor_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'{info["type"]}_{info["member_id"]}', "value": info}
            )
        test.add_key_value_to_json(
            self.result_json_path, "detail.required_groups_check",
            value=group_results
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
        checker = SensorsInfoCheck("SensorsInfoCheck")
        checker.run_test()
        # PASS / PASS_WITH_WARNING → 0；FAIL → 2；其余（异常）→ 1
        if checker.command_check_result in ("PASS", "PASS_WITH_WARNING"):
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
