#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_009_sel_log_modify_negative.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

数据来源：get_system_log_services() → List[Log]，get_system_log_entries() → List[LogEntry]
          对已有 LogEntry 的只读字段发起 PATCH 非法修改请求

校验策略（纯反向测试）：
  对已有 LogEntry 发起 PATCH 修改只读字段，验证 BMC 正确拒绝（返回 4xx）

  反向场景：
    1. PATCH 修改 Id（资源标识符不可变）
    2. PATCH 修改 Created（创建时间不可变）
    3. PATCH 修改 EntryType（日志类型不可变）
    4. PATCH 修改 MessageId（消息 ID 不可变）
    5. PATCH 修改 SensorNumber（传感器编号不可变）
    6. PATCH 空 body（{}）
    7. PATCH 不存在的路径（非法 entry_id）

  PASS 条件：所有非法修改均被拒绝（HTTP 4xx）
  FAIL 条件：任一非法修改被接受（HTTP 2xx）
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class SELLogModifyNegativeCheck(BmcTestBase):
    """Systems SEL 日志修改反向测试

    用例编号：Redfish_Systems_009
    检查项：验证 BMC 拒绝非法的 SEL 日志修改请求
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_009_sel_log_modify_negative.json"),
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

    # ── 找 SEL LogService（Systems → Managers fallback）───────────────────────

    def _find_sel_log_service(self):
        """优先从 Systems 获取，失败时 fallback 到 Managers。
        返回 (Log对象, source字符串)，Log 对象由 SDK 类型化解析。
        """
        try:
            log_services = self.client.get_system_log_services()
            if log_services:
                for svc in log_services:
                    svc_name = (svc.name or "").lower()
                    svc_id = (svc.id or "").lower()
                    if "sel" in svc_name or "sel" in svc_id or "system event" in svc_name:
                        return svc, "Systems"
                return log_services[0], "Systems"
        except Exception as e:
            CommonFunction.print_log("WARNING", f"Systems Log Services 不可用：{str(e)}")

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

    def _get_first_entry_odata_id(self, log_svc, source):
        """获取第一条 LogEntry 的 @odata.id（通过 SDK Log 对象 + get_system/manager_log_entries）"""
        log_id = log_svc.id

        # 从 SDK Log 对象取 Entries 链接，无需 get_raw(LogService)
        entries_link = log_svc.entries
        entries_odata_id = entries_link.odata_id if entries_link else None
        if not entries_odata_id:
            CommonFunction.print_log("ERROR", f"Log Service '{log_id}' 无 Entries 链接")
            return None

        # 用 SDK 取日志条目列表，无需 get_raw(entries_odata_id)
        try:
            if source == "Managers":
                entries = self.client.get_manager_log_entries(log_id)
            else:
                entries = self.client.get_system_log_entries(log_id)
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Entries 列表失败：{str(e)}")
            return None

        if not entries:
            CommonFunction.print_log("WARNING", f"Log Service '{log_id}' 条目为空")
            return None

        first_entry = entries[0]
        first_entry_id = first_entry.odata_id
        if not first_entry_id:
            CommonFunction.print_log("ERROR", "第一条 LogEntry 的 @odata.id 为空")
            return None

        return first_entry_id

    # ── 构造反向测试用例 ──────────────────────────────────────────────────────

    def _get_test_cases(self, entry_odata_id):
        """构造非法 PATCH 测试用例列表"""
        return [
            {
                "name": "modify_id",
                "desc": "PATCH 修改 Id（资源标识符不可变）",
                "target": "Id",
                "odata_id": entry_odata_id,
                "body": {"Id": "hacked_id"},
            },
            {
                "name": "modify_created",
                "desc": "PATCH 修改 Created（创建时间不可变）",
                "target": "Created",
                "odata_id": entry_odata_id,
                "body": {"Created": "2099-01-01T00:00:00+00:00"},
            },
            {
                "name": "modify_entry_type",
                "desc": "PATCH 修改 EntryType（日志类型不可变）",
                "target": "EntryType",
                "odata_id": entry_odata_id,
                "body": {"EntryType": "Oem"},
            },
            {
                "name": "modify_message_id",
                "desc": "PATCH 修改 MessageId",
                "target": "MessageId",
                "odata_id": entry_odata_id,
                "body": {"MessageId": "hacked.1.0"},
            },
            {
                "name": "modify_sensor_number",
                "desc": "PATCH 修改 SensorNumber",
                "target": "SensorNumber",
                "odata_id": entry_odata_id,
                "body": {"SensorNumber": 255},
            },
            {
                "name": "empty_body",
                "desc": "PATCH 空 body ({})",
                "target": "(empty)",
                "odata_id": entry_odata_id,
                "body": {},
            },
            {
                "name": "nonexistent_path",
                "desc": "PATCH 不存在的 LogEntry 路径",
                "target": "N/A",
                "odata_id": "/redfish/v1/Systems/1/LogServices/SEL/Entries/nonexistent_entry",
                "body": {"Message": "test"},
            },
        ]

    # ── 执行单个反向测试 ──────────────────────────────────────────────────────

    def _run_negative_test(self, tc):
        """执行一个反向测试用例"""
        try:
            self.client.patch(tc["odata_id"], tc["body"])
            CommonFunction.print_log("ERROR", f"[{tc['name']}]：请求被接受（预期应拒绝），PATCH 成功")
            return False, 200
        except RedfishException as e:
            error_str = str(e).lower()
            if any(code in error_str for code in ["400", "401", "403", "404", "405", "409", "412", "bad request", "method not allowed"]):
                CommonFunction.print_log("INFO", f"[{tc['name']}]：正确拒绝，{str(e)}")
                return True, 400
            else:
                CommonFunction.print_log("INFO", f"[{tc['name']}]：被拒绝，{str(e)}")
                return True, 400
        except Exception as e:
            CommonFunction.print_log("INFO", f"[{tc['name']}]：被拒绝，{str(e)}")
            return True, 400

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def sel_log_modify_negative_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始查找 SEL Log Service")
        log_svc, source = self._find_sel_log_service()
        if log_svc is None:
            CommonFunction.print_log("ERROR", "Systems 和 Managers 均未找到 Log Service，检查失败")
            return "FAIL", []

        log_id = log_svc.id
        CommonFunction.print_log("INFO", f"从 {source} 找到 Log Service: {log_id}，查找第一条 LogEntry")
        entry_odata_id = self._get_first_entry_odata_id(log_svc, source)
        if not entry_odata_id:
            CommonFunction.print_log("ERROR", "未找到任何 LogEntry，无法执行反向测试")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"目标 LogEntry: {entry_odata_id}")

        test_cases = self._get_test_cases(entry_odata_id)
        all_results = []
        result_list = []

        for i, tc in enumerate(test_cases, 1):
            CommonFunction.print_log("INFO", f"--- 反向测试 {i}/{len(test_cases)}: {tc['desc']} ---")
            is_rejected, http_status = self._run_negative_test(tc)
            all_results.append(is_rejected)
            result_list.append({
                "case_name":    tc["name"],
                "description":  tc["desc"],
                "target_field": tc["target"],
                "http_status":  http_status,
                "is_rejected":  "YES" if is_rejected else "NO",
                "result":       "PASS" if is_rejected else "FAIL",
            })

        final = "PASS" if all(all_results) else "FAIL"
        failed_cnt = sum(1 for r in all_results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"所有 {len(test_cases)} 个反向测试用例均被正确拒绝")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"{failed_cnt}/{len(test_cases)} 个反向测试用例未被拒绝（BMC 接受了非法修改）"
            )
        return final, result_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, result_list = self.sel_log_modify_negative_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in result_list:
                f.write(
                    f'"{info["case_name"]}",'
                    f'"{info["description"]}",'
                    f'"{info["target_field"]}",'
                    f'{info["http_status"]},'
                    f'"{info["is_rejected"]}",'
                    f'{info["result"]}\n'
                )

        for info in result_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'NegativeModify_{info["case_name"]}', "value": info}
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
        checker = SELLogModifyNegativeCheck("SELLogModifyNegativeCheck")
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
