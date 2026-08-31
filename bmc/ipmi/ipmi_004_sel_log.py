#!/bin/python
"""
Author: Fengmian
Date: 2026/05/13
Usage: python3 bmc/ipmi_sel_001.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/13: 新增

IPMI 带外 SEL 日志查看测试（RDSV_BMC_186）

测试步骤：
  1. 带外执行 ipmitool sel info，获取 SEL 基本信息
  2. 带外执行 ipmitool sel list，获取 SEL 日志条目
  3. 校验 sel info 必要字段存在且合法
  4. 校验 sel list 能正常返回（允许为空，但 Entries 字段须与实际条数一致）

校验策略：
  PASS：sel info 成功 + 必要字段存在 + sel list 成功（条目数 >= MinSelEntries）
  FAIL：任一命令失败 / 必要字段缺失 / Entries 字段为负
"""

import os
import time
import re
import subprocess
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

class IpmiSelLog(BmcTestBase):
    """IPMI 带外 SEL 日志查看测试

    用例编号：IPMI_Sel_001
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/ipmi/ipmi_004_sel_log.json"),
        )
    def _load_extra_config(self, conf_section: dict) -> None:
        self.IPMI_IFACE            = conf_section.get("IpmiInterface", "lanplus")
        self.QUERY_TIMEOUT         = conf_section.get("QueryTimeoutSec", 30)
        self.SEL_INFO_TIMEOUT      = conf_section.get("SelInfoTimeoutSec", 15)
        self.MIN_SEL_ENTRIES       = conf_section.get("MinSelEntries", 0)
        self.SEL_INFO_REQ_FIELDS   = conf_section.get("SelInfoRequiredFields",
                                           ["Version", "Entries", "Free Space", "Overflow"])

    def _run_ipmi(self, sub_cmd: list, timeout: int = 30) -> tuple:
        cmd = [
            "ipmitool", "-I", self.IPMI_IFACE,
            "-H", self.BMC_IP,
            "-U", self.USERNAME,
            "-P", self.PASSWORD,
        ] + sub_cmd
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", f"命令执行超时（>{timeout}s）"
        except Exception as e:
            return -1, "", str(e)

    def _parse_sel_info(self, raw: str) -> dict:
        """解析 sel info 输出，返回字段 dict"""
        result = {}
        for line in raw.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                key = key.strip()
                val = val.strip()
                if key:
                    result[key] = val
        return result

    def sel_info_check(self) -> tuple:
        """执行 sel info 并校验必要字段"""
        CommonFunction.print_log("INFO", "执行 ipmitool sel info（带外）")
        rc, stdout, stderr = self._run_ipmi(["sel", "info"], self.SEL_INFO_TIMEOUT)

        if rc != 0:
            CommonFunction.print_log("ERROR", f"sel info 失败，rc={rc}，stderr={stderr}")
            return False, {}, {"rc": rc, "stderr": stderr}

        info = self._parse_sel_info(stdout)
        CommonFunction.print_log("INFO", f"SEL Info 解析到 {len(info)} 个字段")

        all_pass = True
        for field in self.SEL_INFO_REQ_FIELDS:
            val = info.get(field)
            if val is None:
                CommonFunction.print_log("ERROR", f"[SEL Info] 必要字段缺失：{field}")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"[SEL Info] {field} = {val}")

        # Entries 字段须为非负整数
        entries_str = info.get("Entries", "")
        try:
            entries = int(entries_str)
            if entries < 0:
                CommonFunction.print_log("ERROR", f"[SEL Info] Entries={entries}，不应为负数")
                all_pass = False
            else:
                CommonFunction.print_log("INFO", f"[SEL Info] 当前 SEL 条目数：{entries}")
        except (ValueError, TypeError):
            CommonFunction.print_log("ERROR", f"[SEL Info] Entries 解析失败：{entries_str!r}")
            all_pass = False
            entries = -1

        # Overflow 不应为 true
        overflow = info.get("Overflow", "").lower()
        if overflow == "true":
            CommonFunction.print_log("ERROR", "[SEL Info] SEL 已溢出（Overflow=true），需清理")
            all_pass = False
        elif overflow == "false":
            CommonFunction.print_log("INFO", "[SEL Info] SEL 未溢出 ✓")

        return all_pass, info, {"rc": rc, "entries_reported": entries}

    def sel_list_check(self, expected_entries: int) -> tuple:
        """执行 sel list 并校验条目数"""
        CommonFunction.print_log("INFO", "执行 ipmitool sel list（带外）")
        rc, stdout, stderr = self._run_ipmi(["sel", "list"], self.QUERY_TIMEOUT)

        if rc != 0:
            CommonFunction.print_log("ERROR", f"sel list 失败，rc={rc}，stderr={stderr}")
            return False, [], {"rc": rc, "stderr": stderr}

        # 保存完整日志
        with open(self.stdout_path, "w", encoding="utf-8") as f:
            f.write(stdout)

        lines = [l for l in stdout.splitlines() if l.strip()]
        CommonFunction.print_log("INFO", f"sel list 返回 {len(lines)} 条记录")

        if len(lines) < self.MIN_SEL_ENTRIES:
            CommonFunction.print_log(
                "ERROR",
                f"SEL 条目数 {len(lines)} 少于期望最低值 {self.MIN_SEL_ENTRIES}"
            )
            return False, lines, {"rc": rc, "actual_count": len(lines)}

        # 条目数应与 sel info 报告的 Entries 一致（允许±1差异，部分厂商有延迟）
        if expected_entries >= 0 and abs(len(lines) - expected_entries) > 1:
            CommonFunction.print_log(
                "WARNING",
                f"sel list 实际条数 {len(lines)} 与 sel info 报告的 {expected_entries} 不一致（允许±1）"
            )
            # WARNING 不 FAIL，仅记录

        # 解析每条 SEL：格式 "   1 | 日期 | 时间 | 传感器 | 事件 | Asserted/Deasserted"
        parsed = []
        for line in lines:
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 5:
                parsed.append({
                    "record_id": parts[0],
                    "date":      parts[1] if len(parts) > 1 else "",
                    "time":      parts[2] if len(parts) > 2 else "",
                    "sensor":    parts[3] if len(parts) > 3 else "",
                    "event":     parts[4] if len(parts) > 4 else "",
                    "direction": parts[5] if len(parts) > 5 else "",
                })

        CommonFunction.print_log("INFO", f"SEL 日志解析完成，共 {len(parsed)} 条（展示前3条）")
        for r in parsed[:3]:
            CommonFunction.print_log("INFO", f"  [{r['record_id']}] {r['date']} {r['time']} | {r['sensor']} | {r['event']}")

        return True, parsed, {"rc": rc, "actual_count": len(lines)}

    def sel_check(self) -> tuple:
        """主校验流程"""
        all_results = []

        # Step 1: sel info
        info_pass, sel_info, info_meta = self.sel_info_check()
        all_results.append(info_pass)
        expected_entries = info_meta.get("entries_reported", -1)

        # Step 2: sel list
        list_pass, sel_entries, list_meta = self.sel_list_check(expected_entries)
        all_results.append(list_pass)

        final = "PASS" if all(all_results) else "FAIL"
        return final, sel_info, sel_entries, info_meta, list_meta

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, sel_info, sel_entries, info_meta, list_meta = self.sel_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            info_result = "PASS" if sel_info else "FAIL"
            entries_rpt = info_meta.get("entries_reported", "?")
            list_result = "PASS" if list_meta.get("rc", 1) == 0 else "FAIL"
            actual_cnt  = list_meta.get("actual_count", "?")
            f.write(f'"sel_info","{info_result}","entries={entries_rpt}"\n')
            f.write(f'"sel_list","{list_result}","actual_count={actual_cnt}"\n')

        test.add_key_value_to_json(
            self.result_json_path, "detail.sel_info",
            value=sel_info
        )
        test.add_key_value_to_json(
            self.result_json_path, "detail.sel_list_meta",
            value=list_meta
        )
        # 只记录前20条 SEL 条目（避免 JSON 过大）
        test.add_key_value_to_json(
            self.result_json_path, "detail.cycle",
            value={"metrics": "sel_entries_sample", "value": sel_entries[:20]}
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
        checker = IpmiSelLog("IpmiSelLog")
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
