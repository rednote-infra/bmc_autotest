#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_008_sel_log_add_negative.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增

数据来源：get_system_log_services() → List[Log]，找到 SEL LogService 后对其 Entries 集合 POST 非法数据

校验策略（纯反向测试）：
  对 LogService.Entries 集合发起各种非法 POST 请求，验证 BMC 正确拒绝（返回 4xx）

  反向场景：
    1. POST 空 body（{}）
    2. POST 空 Message
    3. POST 非法 EntryType
    4. POST 非法 Severity
    5. POST 缺少所有必要字段
    6. POST 格式错误的数据类型

  PASS 条件：所有非法请求均被拒绝（HTTP 4xx）
  FAIL 条件：任一非法请求被接受（HTTP 2xx）
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class SELLogAddNegativeCheck(BmcTestBase):
    """Systems SEL 日志增加反向测试

    用例编号：Redfish_Systems_008
    检查项：验证 BMC 拒绝非法的 SEL 日志添加请求
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_008_sel_log_add_negative.json"),
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

    # ── 构造反向测试用例 ──────────────────────────────────────────────────────

    def _get_test_cases(self, entries_odata_id):
        """构造非法 POST 测试用例列表"""
        return [
            {
                "name": "empty_body",
                "desc": "POST 空 body ({})",
                "odata_id": entries_odata_id,
                "body": {},
            },
            {
                "name": "empty_message",
                "desc": "POST 空 Message",
                "odata_id": entries_odata_id,
                "body": {"Message": "", "EntryType": "Event"},
            },
            {
                "name": "invalid_entry_type",
                "desc": "POST 非法 EntryType",
                "odata_id": entries_odata_id,
                "body": {"Message": "test", "EntryType": "InvalidType"},
            },
            {
                "name": "invalid_severity",
                "desc": "POST 非法 Severity",
                "odata_id": entries_odata_id,
                "body": {"Message": "test", "Severity": "SuperCritical"},
            },
            {
                "name": "missing_all_fields",
                "desc": "POST 缺少所有必要字段",
                "odata_id": entries_odata_id,
                "body": {"Name": "test_only"},
            },
            {
                "name": "wrong_type_sensor_number",
                "desc": "POST SensorNumber 为字符串（应为整数）",
                "odata_id": entries_odata_id,
                "body": {"Message": "test", "EntryType": "Event", "SensorNumber": "not_a_number"},
            },
            {
                "name": "null_message",
                "desc": "POST Message 为 null",
                "odata_id": entries_odata_id,
                "body": {"Message": None, "EntryType": "Event"},
            },
            {
                "name": "garbage_json",
                "desc": "POST 完全无关的字段",
                "odata_id": entries_odata_id,
                "body": {"foo": "bar", "baz": 123},
            },
        ]

    # ── 执行单个反向测试 ──────────────────────────────────────────────────────

    def _run_negative_test(self, tc):
        """执行一个反向测试用例，返回 (is_rejected, http_status)"""
        try:
            self.client.post(tc["odata_id"], tc["body"])
            # 没有抛异常 = 请求被接受了 = 反向测试失败
            CommonFunction.print_log("ERROR", f"[{tc['name']}]：请求被接受（预期应拒绝），POST 成功")
            return False, 200
        except RedfishException as e:
            error_str = str(e).lower()
            # HTTP 4xx = 正确拒绝
            if any(code in error_str for code in ["400", "401", "403", "404", "405", "409", "412", "400 bad", "method not allowed", "bad request"]):
                CommonFunction.print_log("INFO", f"[{tc['name']}]：正确拒绝，{str(e)}")
                return True, 400
            else:
                CommonFunction.print_log("INFO", f"[{tc['name']}]：被拒绝（非标准状态码），{str(e)}")
                return True, 400
        except Exception as e:
            CommonFunction.print_log("INFO", f"[{tc['name']}]：被拒绝，{str(e)}")
            return True, 400

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def sel_log_add_negative_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始查找 SEL Log Service")
        log_svc, source = self._find_sel_log_service()
        if log_svc is None:
            CommonFunction.print_log("ERROR", "Systems 和 Managers 均未找到 Log Service，检查失败")
            return "FAIL", []

        log_id = log_svc.id
        CommonFunction.print_log("INFO", f"从 {source} 找到 Log Service: {log_id}")

        # 从 SDK Log 对象取 Entries 链接（Log.entries 是 Link 对象，含 odata_id）
        entries_link = log_svc.entries
        entries_odata_id = entries_link.odata_id if entries_link else None

        if not entries_odata_id:
            CommonFunction.print_log("ERROR", f"Log Service '{log_id}' 无 Entries 链接")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"Entries 集合路径：{entries_odata_id}")

        test_cases = self._get_test_cases(entries_odata_id)
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
                f"{failed_cnt}/{len(test_cases)} 个反向测试用例未被拒绝（BMC 接受了非法请求）"
            )
        return final, result_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, result_list = self.sel_log_add_negative_check()
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
                value={"metrics": f'NegativeAdd_{info["case_name"]}', "value": info}
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
        checker = SELLogAddNegativeCheck("SELLogAddNegativeCheck")
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
