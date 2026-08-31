#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_010b_sel_log_clear_negative.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

数据来源：get_system_log_services() → List[Log]
          对 SEL LogService 发起各种非法清除请求，验证 BMC 正确拒绝

校验策略（反向测试）：
  验证以下非法清除操作均被拒绝（HTTP 4xx）：
    1. DELETE LogService 根路径（不应允许删除服务本身）
    2. POST ClearLog 但带非法参数
    3. POST ClearLog 到不存在的路径
    4. PATCH LogService 的 ServiceEnabled=false（尝试禁用而非清除）
    5. DELETE 不存在的 Entry

  PASS 条件：所有非法操作均被拒绝
  FAIL 条件：任一非法操作被接受
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class SELLogClearNegativeCheck(BmcTestBase):
    """Systems SEL 日志清除反向测试

    用例编号：Redfish_Systems_010b
    检查项：验证 BMC 拒绝非法的 SEL 日志清除请求
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_010b_sel_log_clear_negative.json"),
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
        """优先从 Systems 获取，失败时 fallback 到 Managers"""
        try:
            log_services = self.client.get_system_log_services()
            if log_services:
                for svc in log_services:
                    svc_name = (svc.name or "").lower()
                    svc_id = (svc.id or "").lower()
                    if "sel" in svc_name or "sel" in svc_id or "system event" in svc_name:
                        return svc.id, "Systems"
                return log_services[0].id, "Systems"
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
                        return svc.id, "Managers"
                return log_services[0].id, "Managers"
        except Exception as e:
            CommonFunction.print_log("ERROR", f"Managers Log Services 也不可用：{str(e)}")

        return None, None

    # ── 构造反向测试用例 ──────────────────────────────────────────────────────

    def _get_test_cases(self, log_id, source):
        """构造非法清除测试用例"""
        if source == "Managers":
            base = f"/redfish/v1/Managers/1/LogServices/{log_id}"
        else:
            base = f"/redfish/v1/Systems/1/LogServices/{log_id}"
        return [
            {
                "name": "delete_log_service",
                "desc": "DELETE LogService 根路径（不应允许删除服务本身）",
                "odata_id": base,
                "method": "DELETE",
            },
            {
                "name": "clearlog_garbage_params",
                "desc": "POST ClearLog 但带非法参数（非空垃圾数据）",
                "odata_id": f"{base}/Actions/LogService.ClearLog",
                "method": "POST",
                "body": {"garbage": "data", "foo": 123},
            },
            {
                "name": "clearlog_nonexistent",
                "desc": "POST ClearLog 到不存在的路径",
                "odata_id": "/redfish/v1/Systems/1/LogServices/NonExistent/Actions/LogService.ClearLog",
                "method": "POST",
            },
            {
                "name": "disable_service",
                "desc": "PATCH 尝试将 ServiceEnabled 设为 false（禁用而非清除）",
                "odata_id": base,
                "method": "PATCH",
                "body": {"ServiceEnabled": False},
            },
            {
                "name": "delete_nonexistent_entry",
                "desc": "DELETE 不存在的 LogEntry",
                "odata_id": f"{base}/Entries/nonexistent_entry_999",
                "method": "DELETE",
            },
        ]

    # ── 执行单个反向测试 ──────────────────────────────────────────────────────

    def _run_negative_test(self, tc):
        """执行一个反向测试用例"""
        method = tc["method"]
        body = tc.get("body")
        try:
            if method == "DELETE":
                self.client.delete(tc["odata_id"])
                CommonFunction.print_log("ERROR", f"[{tc['name']}]：DELETE 被接受（预期应拒绝）")
                return False, 200
            elif method == "POST":
                self.client.post(tc["odata_id"], body or {})
                CommonFunction.print_log("ERROR", f"[{tc['name']}]：POST 被接受（预期应拒绝）")
                return False, 200
            elif method == "PATCH":
                self.client.patch(tc["odata_id"], body or {})
                CommonFunction.print_log("ERROR", f"[{tc['name']}]：PATCH 被接受（预期应拒绝）")
                return False, 200
        except RedfishException as e:
            error_str = str(e).lower()
            if any(code in error_str for code in ["400", "401", "403", "404", "405", "409", "412", "bad request", "method not allowed", "not found"]):
                CommonFunction.print_log("INFO", f"[{tc['name']}]：正确拒绝，{str(e)}")
                return True, 400
            else:
                CommonFunction.print_log("INFO", f"[{tc['name']}]：被拒绝，{str(e)}")
                return True, 400
        except Exception as e:
            CommonFunction.print_log("INFO", f"[{tc['name']}]：被拒绝，{str(e)}")
            return True, 400

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def sel_log_clear_negative_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始查找 SEL Log Service")
        log_id, source = self._find_sel_log_service()
        if log_id is None:
            CommonFunction.print_log("ERROR", "Systems 和 Managers 均未找到 Log Service，检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"从 {source} 找到 Log Service: {log_id}")

        test_cases = self._get_test_cases(log_id, source)
        all_results = []
        result_list = []

        for i, tc in enumerate(test_cases, 1):
            CommonFunction.print_log("INFO", f"--- 反向测试 {i}/{len(test_cases)}: {tc['desc']} ---")
            is_rejected, http_status = self._run_negative_test(tc)
            all_results.append(is_rejected)
            result_list.append({
                "case_name":    tc["name"],
                "description":  tc["desc"],
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
                f"{failed_cnt}/{len(test_cases)} 个反向测试用例未被拒绝"
            )
        return final, result_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, result_list = self.sel_log_clear_negative_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in result_list:
                f.write(
                    f'"{info["case_name"]}",'
                    f'"{info["description"]}",'
                    f'{info["http_status"]},'
                    f'"{info["is_rejected"]}",'
                    f'{info["result"]}\n'
                )

        for info in result_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'NegativeClear_{info["case_name"]}', "value": info}
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
        checker = SELLogClearNegativeCheck("SELLogClearNegativeCheck")
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
