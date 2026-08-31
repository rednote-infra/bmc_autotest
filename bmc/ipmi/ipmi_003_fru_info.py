#!/bin/python
"""
Author: Fengmian
Date: 2026/05/13
Usage: python3 bmc/ipmi_fru_001.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/13: 新增

IPMI 带外 FRU 信息查看测试（RDSV_BMC_185）

测试步骤：
  1. 带外执行 ipmitool fru，获取所有 FRU 设备信息
  2. 逐个 FRU 解析字段，校验必要字段存在
  3. 按 RequiredFruGroups 配置，检查主板、硬盘背板、Riser 卡是否覆盖

FRU 字段解析策略：
  ipmitool fru 输出格式：
    FRU Device Description : <desc> (ID <n>)
      Board Product : xxx
      Board Serial  : xxx
      ...
  以 "FRU Device Description" 行为分隔符，拆分为独立的 FRU 块

必要组覆盖校验（关键字匹配 Description 或 Board Product，不区分大小写）：
  主板：builtin fru device / mb / mainboard / motherboard
  硬盘背板：bp / backplane / disk bp / hdd bp / nvme bp
  Riser卡：riser / mc / mezzanine / ocp

PASS 标准：
  - ipmitool 命令成功
  - 至少存在 1 个有效 FRU（有 Board Product 或 Product Name）
  - 主板、硬盘背板、Riser 三个必要组均找到匹配
"""

import os
import time
import subprocess
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

class IpmiFruInfo(BmcTestBase):
    """IPMI 带外 FRU 信息查看测试

    用例编号：IPMI_Fru_001
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/ipmi/ipmi_003_fru_info.json"),
        )
    def _load_extra_config(self, conf_section: dict) -> None:
        self.IPMI_IFACE        = conf_section.get("IpmiInterface", "lanplus")
        self.QUERY_TIMEOUT     = conf_section.get("QueryTimeoutSec", 30)
        self.REQUIRED_GROUPS   = conf_section.get("RequiredFruGroups", {})
        self.REQUIRED_FIELDS   = conf_section.get("RequiredFields", ["Board Product", "Board Mfg", "Board Part Number"])
        self.OPTIONAL_FIELDS   = conf_section.get("OptionalFields", ["Board Serial"])
        self.SKIP_FIELD_CHECK_KWS = [k.lower() for k in conf_section.get("SkipRequiredFieldCheckKeywords", ["psu", "power supply"])]

    def _run_ipmi(self, sub_cmd: list, timeout: int = 30) -> tuple:
        cmd = [
            "ipmitool", "-I", self.IPMI_IFACE,
            "-H", self.BMC_IP,
            "-U", self.USERNAME,
            "-P", self.PASSWORD,
        ] + sub_cmd
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout, proc.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", f"命令执行超时（>{timeout}s）"
        except Exception as e:
            return -1, "", str(e)

    def _parse_fru_output(self, raw: str) -> list:
        """
        解析 ipmitool fru 输出，返回 FRU 块列表。
        每块为 dict：{"description": str, "id": int, "fields": {k: v}}
        忽略 "Device not present" 的块。
        """
        frus = []
        current = None

        for line in raw.splitlines():
            if line.startswith("FRU Device Description"):
                # 保存上一块
                if current and current.get("fields"):
                    frus.append(current)
                # 解析新块 header：FRU Device Description : Builtin FRU Device (ID 0)
                rest = line.split(":", 1)[1].strip() if ":" in line else line
                fru_id = None
                if "(ID" in rest:
                    try:
                        fru_id = int(rest[rest.rfind("(ID") + 3:rest.rfind(")")].strip())
                    except Exception:
                        pass
                    desc = rest[:rest.rfind("(ID")].strip()
                else:
                    desc = rest
                current = {"description": desc, "id": fru_id, "fields": {}}
            elif current is not None:
                # 跳过 "Device not present"
                if "Device not present" in line:
                    current = None
                    continue
                # 解析字段行：" Board Product         : MB23A06A"
                if ":" in line:
                    key, _, val = line.partition(":")
                    key = key.strip()
                    val = val.strip()
                    if key and val:
                        current["fields"][key] = val

        # 保存最后一块
        if current and current.get("fields"):
            frus.append(current)

        return frus

    def _check_required_groups(self, frus: list) -> tuple:
        """
        按 RequiredFruGroups 配置检查必要组覆盖。
        匹配 description 或 Board Product 字段（不区分大小写）。
        """
        group_results = []
        all_pass = True

        for group_name, cfg in self.REQUIRED_GROUPS.items():
            keywords = [k.lower() for k in cfg.get("keywords", [])]
            desc = cfg.get("description", group_name)

            matched = []
            for fru in frus:
                # 检查 description 和 Board Product / Product Name
                targets = [
                    fru.get("description", "").lower(),
                    fru["fields"].get("Board Product", "").lower(),
                    fru["fields"].get("Product Name", "").lower(),
                ]
                combined = " ".join(targets)
                if any(kw in combined for kw in keywords):
                    matched.append(
                        fru["fields"].get("Board Product") or fru["fields"].get("Product Name") or fru.get("description")
                    )

            result = "PASS" if matched else "FAIL"
            if result == "FAIL":
                all_pass = False
                CommonFunction.print_log("ERROR", f"[必要FRU组] {group_name}：未找到匹配！（keywords={keywords}）")
            else:
                CommonFunction.print_log("INFO", f"[必要FRU组] {group_name}：✓ 匹配到 {matched}")

            group_results.append({
                "group": group_name,
                "description": desc,
                "keywords": keywords,
                "matched": matched,
                "result": result,
            })

        return all_pass, group_results

    def fru_check(self) -> tuple:
        CommonFunction.print_log("INFO", "执行 ipmitool fru（带外）")
        rc, stdout, stderr = self._run_ipmi(["fru"], self.QUERY_TIMEOUT)

        if rc != 0:
            CommonFunction.print_log("ERROR", f"ipmitool fru 失败，rc={rc}，stderr={stderr}")
            return "FAIL", [], [], {"rc": rc, "stderr": stderr}

        # 保存原始输出
        with open(self.stdout_path, "w", encoding="utf-8") as f:
            f.write(stdout)

        frus = self._parse_fru_output(stdout)
        CommonFunction.print_log("INFO", f"解析到 {len(frus)} 个有效 FRU 块")

        if not frus:
            CommonFunction.print_log("ERROR", "未解析到任何有效 FRU 数据")
            return "FAIL", [], [], {"rc": rc, "raw": stdout[:500]}

        fru_info_list = []

        # Step1：先做必要组覆盖检查，收集命中了必要组的 FRU id 集合
        CommonFunction.print_log("INFO", "Step1：必要 FRU 组覆盖检查")
        groups_pass, group_results = self._check_required_groups(frus)

        # 收集所有命中必要组的 FRU id（这些才需要做字段校验）
        required_fru_ids = set()
        for group_name, cfg in self.REQUIRED_GROUPS.items():
            keywords = [k.lower() for k in cfg.get("keywords", [])]
            for fru in frus:
                targets = [
                    fru.get("description", "").lower(),
                    fru["fields"].get("Board Product", "").lower(),
                    fru["fields"].get("Product Name", "").lower(),
                ]
                combined = " ".join(targets)
                if any(kw in combined for kw in keywords):
                    required_fru_ids.add(fru.get("id"))

        # Step2：只对命中必要组的 FRU 块做字段校验，其余跳过
        CommonFunction.print_log("INFO", f"Step2：对必要组 FRU（ID={required_fru_ids}）做字段校验，其余跳过")
        field_results = []

        for fru in frus:
            fru_id  = fru.get("id", "?")
            desc    = fru.get("description", "")
            fields  = fru.get("fields", {})

            if fru_id not in required_fru_ids:
                CommonFunction.print_log("INFO", f"[FRU ID={fru_id}] {desc}：非必要组，跳过字段校验")
                fru_info_list.append({
                    "fru_id": fru_id, "description": desc,
                    "board_product": fields.get("Board Product", ""),
                    "board_mfg": fields.get("Board Mfg", ""),
                    "board_part_number": fields.get("Board Part Number", ""),
                    "board_serial": fields.get("Board Serial", ""),
                    "product_name": fields.get("Product Name", ""),
                    "fields": fields, "check_result": "SKIP",
                })
                continue

            fru_pass = True
            # 必要字段：缺失 → FAIL（只对必要组 FRU）
            for req_field in self.REQUIRED_FIELDS:
                val = fields.get(req_field, "")
                if not val or val.upper() == "NULL":
                    CommonFunction.print_log("ERROR",
                        f"[FRU ID={fru_id}] {desc}：必要字段 {req_field} 为空或 NULL → FAIL")
                    fru_pass = False
                else:
                    CommonFunction.print_log("INFO", f"[FRU ID={fru_id}] {desc}：{req_field}={val}")

            # 可选字段：缺失 → WARNING，不影响结果
            for opt_field in self.OPTIONAL_FIELDS:
                val = fields.get(opt_field, "")
                if not val or val.upper() == "NULL":
                    CommonFunction.print_log("WARNING",
                        f"[FRU ID={fru_id}] {desc}：可选字段 {opt_field} 为空或 NULL（不影响结果）")
                else:
                    CommonFunction.print_log("INFO", f"[FRU ID={fru_id}] {desc}：{opt_field}={val}")

            field_results.append(fru_pass)
            fru_info_list.append({
                "fru_id":            fru_id,
                "description":       desc,
                "board_product":     fields.get("Board Product", ""),
                "board_mfg":         fields.get("Board Mfg", ""),
                "board_part_number": fields.get("Board Part Number", ""),
                "board_serial":      fields.get("Board Serial", ""),
                "product_name":      fields.get("Product Name", ""),
                "fields":            fields,
                "check_result":      "PASS" if fru_pass else "FAIL",
            })

        fields_pass = all(field_results) if field_results else True
        final = "PASS" if (groups_pass and fields_pass) else "FAIL"
        return final, fru_info_list, group_results, {"rc": rc, "fru_count": len(frus)}

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, fru_info_list, group_results, meta = self.fru_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in fru_info_list:
                f.write(
                    f'"{info["fru_id"]}",'
                    f'"{info["description"]}",'
                    f'"{info["board_product"]}",'
                    f'"{info["board_mfg"]}",'
                    f'"{info["board_part_number"]}",'
                    f'"{info["board_serial"]}",'
                    f'"{info["check_result"]}"\n'
                )

        for info in fru_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'fru_id_{info["fru_id"]}', "value": info}
            )

        test.add_key_value_to_json(
            self.result_json_path, "detail.required_groups_check",
            value=group_results
        )
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result}
        )
        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{self.command_check_result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = IpmiFruInfo("IpmiFruInfo")
        checker.run_test()
        exit_code = 0 if checker.command_check_result == "PASS" else 2
        time.sleep(5)
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "键盘中断")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"未处理异常: {e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        if checker:
            try:
                with open(checker.exit_code_path, "w") as f:
                    f.write(str(exit_code))
            except Exception as e:
                CommonFunction.print_log("ERROR", f"写入退出码失败: {e}")
    sys.exit(exit_code)
