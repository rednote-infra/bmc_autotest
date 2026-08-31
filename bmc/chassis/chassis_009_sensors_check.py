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

Sensor 集合来源：
  - 温度传感器：get_thermal().temperatures  → Temperature 列表
  - 风扇转速：  get_thermal().fans          → Fan 列表
  - 电压传感器：get_power().voltages        → Voltage 列表
  - 功耗控制：  get_power().power_control   → PowerControl 列表
  - 电源模块：  get_power_supplies()        → PowerSupply 列表

校验分两阶段：
  阶段一：字段合规校验（逐条，区分必要/非必要）
    必要传感器：name 命中 RequiredSensorGroups 的 keywords（temperature/voltage）；
              或整体必要类型（fan/psu/power_control）
    非必要传感器：其余所有传感器
    必要传感器字段异常 → ERROR + 计入整体 FAIL
    非必要传感器字段异常 → WARNING，不计入整体 FAIL

  阶段二：必要传感器组覆盖检查（"有效读值"名称列表）
    每组通过关键字匹配【有效读值】传感器名称，至少匹配 1 条即满足
    任意组缺失或无有效读值 → FAIL

整体结果：必要传感器全 PASS 且阶段二全 PASS → PASS
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
      2. 必要传感器组覆盖检查（按配置，仅统计有效读值传感器）
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

    def _build_mandatory_keywords(self) -> dict:
        """从 REQUIRED_SENSOR_GROUPS 中提取各 source 对应的关键字列表。

        返回 {source: [keyword, ...]}。
        keywords 为空列表的组（fan/psu/power_control）表示该类别整体必要，
        其 source 在返回 dict 中用空列表标记。
        """
        mandatory = {}
        for cfg in self.REQUIRED_SENSOR_GROUPS.values():
            source   = cfg.get("source", "")
            keywords = [k.lower() for k in cfg.get("keywords", [])]
            if source not in mandatory:
                mandatory[source] = []
            mandatory[source].extend(keywords)
        return mandatory

    def _is_mandatory(self, sensor_name: str, source: str, mandatory_kw: dict) -> bool:
        """判断传感器是否为必要传感器。

        - source 不在 mandatory_kw 中 → 非必要
        - source 在 mandatory_kw 中且 keywords 为空（fan/psu/power_control）→ 整体必要
        - source 在 mandatory_kw 中且 keywords 非空 → 名称命中任一 keyword 才必要
        """
        if source not in mandatory_kw:
            return False
        kws = mandatory_kw[source]
        if not kws:
            return True
        name_lower = (sensor_name or "").lower()
        return any(kw in name_lower for kw in kws)

    # ── 温度传感器校验 ──────────────────────────────────────────────────────

    def _check_temperature(self, temp, is_mandatory: bool) -> tuple:
        """校验单条温度传感器。

        :return: (field_pass, has_valid_reading, info_dict)
          field_pass        — 字段校验是否通过（非必要传感器此值不计入 overall）
          has_valid_reading — ReadingCelsius 是否有效（用于阶段二统计）
          info_dict         — 传感器信息字典
        """
        sensor_id   = temp.member_id or temp.name or "unknown"
        sensor_name = temp.name or sensor_id
        ctx         = temp.physical_context or "未知区域"
        tag         = "[必要]" if is_mandatory else "[非必要]"
        log_fail    = "ERROR" if is_mandatory else "WARNING"
        field_pass  = True

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

        check_result = "PASS" if field_pass else ("FAIL" if is_mandatory else "WARNING")
        return field_pass, has_valid_reading, {
            "type": "temperature",
            "member_id": sensor_id,
            "name": sensor_name,
            "physical_context": ctx,
            "is_mandatory": is_mandatory,
            "reading": temp.reading_celsius,
            "unit": "Celsius",
            "health": health or "",
            "check_result": check_result,
        }

    # ── 风扇传感器校验（整体必要）───────────────────────────────────────────

    def _check_fan(self, fan) -> tuple:
        """校验单条风扇传感器（整体必要）。

        :return: (field_pass, has_valid_reading, info_dict)
        """
        sensor_id = getattr(fan, "member_id", None) or getattr(fan, "name", None) or "unknown"
        name      = getattr(fan, "name", None)
        reading   = getattr(fan, "reading", None)
        status    = getattr(fan, "status", None)
        health    = status.health if status else None
        field_pass = True

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

        return field_pass, has_valid_reading, {
            "type": "fan",
            "member_id": sensor_id,
            "name": name or "",
            "physical_context": "",
            "is_mandatory": True,
            "reading": reading,
            "unit": "RPM",
            "health": health or "",
            "check_result": "PASS" if field_pass else "FAIL",
        }

    # ── 电压传感器校验（全部非必要）────────────────────────────────────────

    def _check_voltage(self, volt) -> tuple:
        """校验单条电压传感器（全部非必要，异常只 WARNING）。

        :return: (field_pass, has_valid_reading, info_dict)
        """
        sensor_id  = volt.member_id or volt.name or "unknown"
        field_pass = True

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

        return field_pass, has_valid_reading, {
            "type": "voltage",
            "member_id": volt.member_id or volt.name or "",
            "name": volt.name or "",
            "physical_context": volt.physical_context or "",
            "is_mandatory": False,
            "reading": volt.reading_volts,
            "unit": "Volts",
            "health": health or "",
            "check_result": "PASS" if field_pass else "WARNING",
        }

    # ── 功耗控制校验（整体必要）────────────────────────────────────────────

    def _check_power_control(self, pc) -> tuple:
        """校验单条功耗控制传感器（整体必要）。

        :return: (field_pass, has_valid_reading, info_dict)
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
                "ERROR",
                f"[PowerCtrl][必要] {ctrl_id}：PowerCapacityWatts={pc.power_capacity_watts}，应为正数"
            )
            field_pass = False
        else:
            CommonFunction.print_log(
                "INFO",
                f"[PowerCtrl][必要] {ctrl_id}：PowerCapacityWatts={pc.power_capacity_watts} W"
            )

        return field_pass, has_valid_reading, {
            "type": "power_control",
            "member_id": pc.member_id or "",
            "name": pc.name or "",
            "physical_context": "",
            "is_mandatory": True,
            "reading": pc.power_consumed_watts,
            "unit": "Watts",
            "health": "",
            "check_result": "PASS" if field_pass else "FAIL",
        }

    # ── 电源模块校验（整体必要）────────────────────────────────────────────

    def _check_psu(self, psu) -> tuple:
        """校验单条电源模块（整体必要）。

        :return: (field_pass, has_valid_reading, info_dict)
        """
        name       = psu.name or psu.member_id or "unknown"
        health     = psu.status.health if psu.status else None
        field_pass = True

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

        return field_pass, has_valid_reading, {
            "type": "psu",
            "member_id": name,
            "name": name,
            "physical_context": "",
            "is_mandatory": True,
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
        """阶段二：对每个必要传感器组，在【有效读值】名称列表中进行关键字匹配。

        :param *_valid_names: 各类型中 has_valid_reading=True 且有 name 的传感器名列表
        :return: (all_pass, group_results)
        """
        source_map = {
            "temperature":   temp_valid_names,
            "voltage":       volt_valid_names,
            "power_control": pc_valid_names,
            "fan":           fan_valid_names,
            "psu":           psu_valid_names,
        }
        group_results = []
        all_pass      = True

        for group_name, cfg in self.REQUIRED_SENSOR_GROUPS.items():
            source   = cfg.get("source", "")
            keywords = [k.lower() for k in cfg.get("keywords", [])]
            desc     = cfg.get("description", group_name)
            names    = source_map.get(source, [])
            matched  = (
                [n for n in names if any(kw in n.lower() for kw in keywords)]
                if keywords else names
            )

            result = "PASS" if matched else "FAIL"
            if result == "FAIL":
                all_pass = False
                CommonFunction.print_log(
                    "ERROR",
                    f"[必要传感器组] {group_name}：未找到匹配且有效读值的传感器"
                    f"（source={source}, keywords={keywords}）"
                )
            else:
                CommonFunction.print_log(
                    "INFO",
                    f"[必要传感器组] {group_name}：✓ 找到 {len(matched)} 条（均有有效读值）"
                )
            group_results.append({
                "group":       group_name,
                "description": desc,
                "source":      source,
                "keywords":    keywords,
                "matched":     matched,
                "result":      result,
            })

        return all_pass, group_results

    # ── 主检查流程 ──────────────────────────────────────────────────────────

    def sensors_info_check(self) -> tuple:
        # 预先构建必要传感器关键字映射
        mandatory_kw = self._build_mandatory_keywords()

        # 必要传感器的字段校验结果（False = 有必要传感器字段不合规）
        mandatory_field_results = []
        all_sensor_info         = []

        # 阶段二：仅统计有有效读值的传感器名
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
            mandatory_field_results.append(False)

        if not temp_list:
            CommonFunction.print_log("ERROR", "温度传感器列表为空")
            mandatory_field_results.append(False)
        else:
            CommonFunction.print_log("INFO", f"温度传感器 {len(temp_list)} 条，开始校验")
            for temp in temp_list:
                sensor_name  = temp.name or ""
                is_mandatory = self._is_mandatory(sensor_name, "temperature", mandatory_kw)
                field_pass, has_valid, info = self._check_temperature(temp, is_mandatory)
                all_sensor_info.append(info)
                if is_mandatory:
                    mandatory_field_results.append(field_pass)
                if sensor_name and has_valid:
                    temp_valid_names.append(sensor_name)

        if not fan_list:
            CommonFunction.print_log("ERROR", "风扇传感器列表为空（Thermal.Fans 为空）")
            mandatory_field_results.append(False)
        else:
            CommonFunction.print_log("INFO", f"风扇传感器 {len(fan_list)} 条，开始校验")
            for fan in fan_list:
                field_pass, has_valid, info = self._check_fan(fan)
                all_sensor_info.append(info)
                mandatory_field_results.append(field_pass)
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
            mandatory_field_results.append(False)

        if not volt_list:
            # 电压传感器整体非必要，列表为空只 WARNING
            CommonFunction.print_log("WARNING", "电压传感器列表为空")
        else:
            CommonFunction.print_log("INFO", f"电压传感器 {len(volt_list)} 条，开始校验")
            for volt in volt_list:
                field_pass, has_valid, info = self._check_voltage(volt)
                all_sensor_info.append(info)
                # 非必要传感器：field_pass 不计入 mandatory_field_results
                if volt.name and has_valid:
                    volt_valid_names.append(volt.name)

        if not pc_list:
            CommonFunction.print_log("ERROR", "PowerControl 列表为空")
            mandatory_field_results.append(False)
        else:
            CommonFunction.print_log("INFO", f"PowerControl {len(pc_list)} 条，开始校验")
            for pc in pc_list:
                field_pass, has_valid, info = self._check_power_control(pc)
                all_sensor_info.append(info)
                mandatory_field_results.append(field_pass)
                if pc.name and has_valid:
                    pc_valid_names.append(pc.name)

        # ── 电源模块（PowerSupplies）──────────────────────────────────────
        CommonFunction.print_log("INFO", "获取 PowerSupplies 数据")
        try:
            psu_list = self.client.get_power_supplies()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"get_power_supplies() 失败：{str(e)}")
            psu_list = []
            mandatory_field_results.append(False)

        if not psu_list:
            CommonFunction.print_log("ERROR", "PowerSupplies 列表为空")
            mandatory_field_results.append(False)
        else:
            CommonFunction.print_log("INFO", f"电源模块 {len(psu_list)} 条，开始校验")
            for psu in psu_list:
                field_pass, has_valid, info = self._check_psu(psu)
                all_sensor_info.append(info)
                mandatory_field_results.append(field_pass)
                n = psu.name or psu.member_id
                if n and has_valid:
                    psu_valid_names.append(n)

        # ── 阶段二：必要传感器组覆盖检查（有效读值） ────────────────────────
        CommonFunction.print_log("INFO", "=" * 50)
        CommonFunction.print_log("INFO", "阶段二：必要传感器组覆盖检查（有效读值）")
        groups_pass, group_results = self._check_required_groups(
            temp_valid_names, volt_valid_names, pc_valid_names,
            fan_valid_names,  psu_valid_names
        )

        # 整体结果：必要传感器字段全 PASS 且阶段二全 PASS
        mandatory_ok = all(mandatory_field_results) if mandatory_field_results else False
        final        = "PASS" if (mandatory_ok and groups_pass) else "FAIL"

        # 汇总统计
        mandatory_cnt = len(mandatory_field_results)
        mandatory_fail_cnt = sum(1 for r in mandatory_field_results if not r)
        optional_cnt  = sum(1 for info in all_sensor_info if not info.get("is_mandatory"))
        optional_warn_cnt = sum(
            1 for info in all_sensor_info
            if not info.get("is_mandatory") and info.get("check_result") == "WARNING"
        )

        CommonFunction.print_log(
            "INFO" if final == "PASS" else "ERROR",
            f"Sensor 检查完成：{'全部通过' if final == 'PASS' else '存在失败项'}，"
            f"共 {len(all_sensor_info)} 个传感器 "
            f"| 必要: {mandatory_cnt} 个（失败 {mandatory_fail_cnt} 个）"
            f"| 非必要: {optional_cnt} 个（WARNING {optional_warn_cnt} 个）"
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
