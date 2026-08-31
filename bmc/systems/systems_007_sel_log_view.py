#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_007_sel_log_view.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增
2026/06/23: 增加单 LogService 资源验证（v1.1.0 get_system_log_service，探测 #LogService.ClearLog Action）

数据来源：get_system_log_services() → List[Log]
          get_system_log_service(log_id) → Log（单资源，含 Actions）
          get_system_log_entries(log_id) → List[LogEntry]

校验策略：
  Log 服务必要字段（FAIL + ERROR）：
    Id、Name、ServiceEnabled
  LogEntry 必要字段（FAIL + ERROR）：
    Id、EntryType、Severity、Message
  LogEntry 非必要字段（WARNING）：
    Created、MessageId、MessageArgs、SensorType、SensorNumber、EntryCode、OemLogEntryCode

CSV：每行一条 LogEntry
JSON detail.cycle：每条日志一条记录 + Log 服务信息；summary：整体 PASS/FAIL
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

VALID_SEVERITY = {"OK", "Warning", "Critical", "Informational"}

class SELLogViewCheck(BmcTestBase):
    """Systems SEL 日志查看测试

    用例编号：Redfish_Systems_007
    检查项：SEL 日志服务状态 + 日志条目关键字段校验
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_007_sel_log_view.json"),
        )
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
            except Exception as e:
                CommonFunction.print_log("WARNING", f"关闭 SDK 客户端时出错: {str(e)}")

    # ── Log 服务校验 ──────────────────────────────────────────────────────────

    def _check_log_service(self, log_svc) -> tuple:
        svc_id = log_svc.id or "unknown"
        label = f"LogService/{svc_id}"
        all_pass = True

        # 1. Id
        if not svc_id or svc_id == "unknown":
            CommonFunction.print_log("ERROR", f"[{label}]：Id 为空")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：Id = {svc_id}")

        # 2. Name
        name = log_svc.name or ""
        if not name.strip():
            CommonFunction.print_log("ERROR", f"[{label}]：Name 为空")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：Name = {name}")

        # 3. ServiceEnabled
        if log_svc.service_enabled is None:
            CommonFunction.print_log("ERROR", f"[{label}]：ServiceEnabled 为 None")
            all_pass = False
        elif not log_svc.service_enabled:
            CommonFunction.print_log("ERROR", f"[{label}]：ServiceEnabled = False，日志服务未启用")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：ServiceEnabled = True")

        # INFO 字段（不强制）
        for fname, fval in [
            ("OverWritePolicy", log_svc.overwrite_policy),
            ("MaxNumberOfRecords", log_svc.max_number_of_records),
        ]:
            if fval is None:
                CommonFunction.print_log("INFO", f"[{label}]：{fname} = None")
            else:
                CommonFunction.print_log("INFO", f"[{label}]：{fname} = {fval}")

        return all_pass, svc_id

    # ── 单 LogService 资源 + ClearLog Action 可发现性（v1.1.0 get_system_log_service）──

    def _check_log_service_single(self, log_id, source) -> bool:
        """用 get_system_log_service(id) 获取单个 LogService（含 Actions 块），
        验证可获取并探测 #LogService.ClearLog Action 可发现性。

        - 仅 Systems 侧支持该 SDK 接口；Managers 侧跳过（SDK 无对应单资源接口）。
        - 只读验证，不执行清除（清除见 systems_010a）。
        - ClearLog Action 缺失记 WARNING（厂商差异，可走 DELETE Entries），不计 FAIL；
          仅当单资源获取失败时才返回 False（FAIL）。
        """
        if source != "Systems":
            CommonFunction.print_log(
                "WARNING",
                f"LogService 来自 {source}，SDK 无单资源接口，跳过单资源/Action 验证"
            )
            return True
        try:
            single = self.client.get_system_log_service(log_id)
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"get_system_log_service({log_id}) 获取失败：{str(e)}")
            return False
        if single is None or single.id is None:
            CommonFunction.print_log("ERROR", "get_system_log_service 返回空或缺少 Id")
            return False
        CommonFunction.print_log("INFO", f"单 LogService 获取成功：Id={single.id}")

        actions = single.actions or {}
        if "#LogService.ClearLog" in actions:
            target = actions.get("#LogService.ClearLog", {}).get("target")
            CommonFunction.print_log("INFO", f"  含 #LogService.ClearLog Action（target={target}）")
        else:
            CommonFunction.print_log(
                "WARNING",
                f"  未发现 #LogService.ClearLog Action（厂商差异，清除可走 DELETE Entries），"
                f"actions keys={list(actions.keys())}"
            )
        return True

    # ── 单条 LogEntry 校验 ────────────────────────────────────────────────────

    def _check_entry(self, entry, log_service_id: str) -> tuple:
        entry_id = entry.id or "unknown"
        label = f"LogService/{log_service_id}/Entry/{entry_id}"
        all_pass = True

        # ══ 必要字段 ══

        # 1. Id
        if not entry_id or entry_id == "unknown":
            CommonFunction.print_log("ERROR", f"[{label}]：Id 为空")
            all_pass = False

        # 2. EntryType
        entry_type = entry.entry_type or ""
        if not entry_type.strip():
            CommonFunction.print_log("ERROR", f"[{label}]：EntryType 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：EntryType = {entry_type}")

        # 3. Severity
        severity = entry.severity or ""
        if not severity.strip():
            CommonFunction.print_log("ERROR", f"[{label}]：Severity 为空或 None")
            all_pass = False
        elif severity not in VALID_SEVERITY:
            CommonFunction.print_log("WARNING", f"[{label}]：Severity = '{severity}'，不在常见枚举值 {VALID_SEVERITY} 内")
            # 不强制 FAIL，BMC 可能有自定义 severity
        else:
            CommonFunction.print_log("INFO", f"[{label}]：Severity = {severity}")

        # 4. Message
        message = entry.message or ""
        if not message.strip():
            CommonFunction.print_log("ERROR", f"[{label}]：Message 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[{label}]：Message = {message[:100]}{'...' if len(message) > 100 else ''}")

        # ══ 非必要字段 ══

        for fname, fval in [
            ("MessageId",       entry.message_id),
            ("MessageArgs",     entry.message_args),
            ("Created",         entry.created),
            ("SensorType",      entry.sensor_type),
            ("SensorNumber",    entry.sensor_number),
            ("EntryCode",       entry.entry_code),
            ("OemLogEntryCode", entry.oem_log_entry_code),
        ]:
            if fval is None or (isinstance(fval, str) and fval.strip() == ""):
                CommonFunction.print_log("WARNING", f"[{label}]：{fname} 为空或 None")
            else:
                display_val = str(fval)
                if len(display_val) > 80:
                    display_val = display_val[:80] + "..."
                CommonFunction.print_log("INFO", f"[{label}]：{fname} = {display_val}")

        entry_info = {
            "log_service_id": log_service_id,
            "entry_id":       entry_id,
            "entry_type":     entry_type,
            "severity":       severity,
            "message":        message,
            "message_id":     entry.message_id or "",
            "created":        entry.created or "",
            "sensor_type":    entry.sensor_type or "",
            "check_result":   "PASS" if all_pass else "FAIL",
        }
        return all_pass, entry_info

    # ── 找 SEL LogService（Systems → Managers fallback）───────────────────────

    def _find_sel_log_service(self):
        """优先从 Systems 获取，失败时 fallback 到 Managers。
        返回 (Log对象, source字符串)，Log 对象已由 SDK 类型化解析。
        """
        # Step 1: 尝试 Systems
        try:
            log_services = self.client.get_system_log_services()
            if log_services:
                for svc in log_services:
                    svc_name = (svc.name or "").lower()
                    svc_id = (svc.id or "").lower()
                    if "sel" in svc_name or "sel" in svc_id or "system event" in svc_name:
                        return svc, "Systems"
                # 没找到 SEL，用第一个
                return log_services[0], "Systems"
        except Exception as e:
            CommonFunction.print_log("WARNING", f"Systems Log Services 不可用：{str(e)}")

        # Step 2: Fallback 到 Managers
        CommonFunction.print_log("INFO", "尝试从 Managers 获取 Log Services")
        try:
            log_services = self.client.get_manager_log_services()
            if log_services:
                for svc in log_services:
                    svc_name = (svc.name or "").lower()
                    svc_id = (svc.id or "").lower()
                    if "sel" in svc_name or "sel" in svc_id or "system event" in svc_name:
                        return svc, "Managers"
                return log_services[0], "Managers"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"Managers Log Services 也不可用：{str(e)}")

        return None, None

    def _get_log_entries(self, log_id, source):
        """根据 source 获取日志条目"""
        if source == "Managers":
            return self.client.get_manager_log_entries(log_id)
        else:
            return self.client.get_system_log_entries(log_id)

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def sel_log_view_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始获取 Log Services（优先 Systems，fallback Managers）")
        log_svc, source = self._find_sel_log_service()
        if log_svc is None:
            CommonFunction.print_log("ERROR", "Systems 和 Managers 均未找到 Log Service，检查失败")
            return "FAIL", []

        log_id = log_svc.id
        CommonFunction.print_log("INFO", f"从 {source} 找到 Log Service: {log_id}")
        all_results = []
        entry_info_list = []

        # 校验 Log Service 本身（SDK 已返回类型化 Log 对象，无需 get_raw）
        try:
            svc_pass, _ = self._check_log_service(log_svc)
            all_results.append(svc_pass)
        except Exception as e:
            CommonFunction.print_log("ERROR", f"校验 Log Service 详情失败：{str(e)}")
            all_results.append(False)

        # 校验单 LogService 资源 + ClearLog Action 可发现性（v1.1.0 get_system_log_service）
        try:
            single_ok = self._check_log_service_single(log_id, source)
            all_results.append(single_ok)
        except Exception as e:
            CommonFunction.print_log("ERROR", f"校验单 LogService 资源失败：{str(e)}")
            all_results.append(False)

        # 获取日志条目（带 fallback）
        try:
            entries = self._get_log_entries(log_id, source)
        except (RedfishException, AttributeError) as e:
            CommonFunction.print_log("ERROR", f"获取 Log Entries ({log_id}) 失败：{str(e)}")
            all_results.append(False)
            return "FAIL", []

        if not entries:
            CommonFunction.print_log("WARNING", f"Log Service '{log_id}' 条目为空，可能尚未产生日志")
        else:
            CommonFunction.print_log("INFO", f"共获取到 {len(entries)} 条日志条目，开始逐一校验")
            for entry in entries:
                passed, entry_info = self._check_entry(entry, log_id)
                all_results.append(passed)
                entry_info_list.append(entry_info)

        final = "PASS" if all(all_results) else "FAIL"
        failed_cnt = sum(1 for r in all_results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"SEL 日志查看检查全部通过，共 {len(all_results)} 项（含 LogService + {len(entries)} 条 Entry）")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"SEL 日志查看检查失败，{failed_cnt}/{len(all_results)} 项存在异常"
            )
        return final, entry_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, entry_info_list = self.sel_log_view_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in entry_info_list:
                f.write(
                    f'"{info["log_service_id"]}",'
                    f'"{info["entry_id"]}",'
                    f'"{info["entry_type"]}",'
                    f'"{info["severity"]}",'
                    f'"{info["message"]}",'
                    f'"{info["message_id"]}",'
                    f'"{info["created"]}",'
                    f'"{info["sensor_type"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in entry_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'Entry_{info["log_service_id"]}_{info["entry_id"]}', "value": info}
            )

        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result}
        )
        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{self.command_check_result}")

# ── 主程序 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = SELLogViewCheck("SELLogViewCheck")
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
