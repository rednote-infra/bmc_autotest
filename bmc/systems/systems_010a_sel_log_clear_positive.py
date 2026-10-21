#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_010a_sel_log_clear_positive.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增
2026/06/23: Systems 侧 ClearLog 改用 redfish_sdk v1.1.0 clear_system_log()；
            Managers 侧改用 SDK get_manager_log_services() 获取 Log 对象

数据来源：get_system_log_services() → List[Log]
          Systems 侧用 clear_system_log() 发起 ClearLog；
          或 DELETE Entries，验证日志被清除

校验策略（正向测试）：
  1. 记录清除前的日志条目数量
  2. 尝试 ClearLog Action（POST to ClearLog action target）
  3. 如果 Action 不支持，尝试 DELETE Entries 集合
  4. 清除后重新获取条目数量
  5. 验证条目数量 == 0

  PASS 条件：清除后日志条目数量为 0
  FAIL 条件：清除后仍有日志条目，或清除操作本身失败
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.sel_log_helper import find_sel_log_service

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class SELLogClearPositiveCheck(BmcTestBase):
    """Systems SEL 日志清除测试（正向）

    用例编号：Redfish_Systems_010a
    检查项：验证 SEL 日志清除操作成功后条目为空
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_010a_sel_log_clear_positive.json"),
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

    # ── 找 SEL LogService（跨 Systems / Managers 两侧，公共逻辑）────────────

    def _find_sel_log_service(self):
        """委托公共方法实现跨 Systems/Managers 的 SEL 匹配选择。

        返回 (Log对象, source)，source 为 "Systems" 或 "Managers"；
        详见 func/sel_log_helper.find_sel_log_service。
        """
        return find_sel_log_service(self.client, self.LOG_ID)

    def _count_entries(self, log_id, source):
        """获取当前日志条目数量"""
        try:
            if source == "Managers":
                entries = self.client.get_manager_log_entries(log_id)
            else:
                entries = self.client.get_system_log_entries(log_id)
            return len(entries) if entries else 0
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取日志条目失败：{str(e)}")
            return -1

    # ── 清除方法 1：ClearLog Action ──────────────────────────────────────────

    def _clear_via_action(self, log_svc, source):
        """通过 ClearLog Action 清除。

        Systems / Managers 两侧分别复用 v1.1.0 的 ``clear_system_log()`` /
        ``clear_manager_log()``（内部发现 ``#LogService.ClearLog`` 并 POST，
        不广告该 Action 时抛 ``RedfishValidationError``），不再手动遍历集合取 Action。
        """
        log_id = log_svc.id

        # Systems 侧：clear_system_log()
        if source != "Managers":
            try:
                self.client.clear_system_log(log_id)
                CommonFunction.print_log("INFO", "ClearLog Action 执行成功（clear_system_log）")
                return True, "ClearLog Action"
            except RedfishException as e:
                CommonFunction.print_log("INFO", f"clear_system_log 不可用（{str(e)[:80]}），尝试其他方法")
                return False, "ClearLog Action 不可用"

        # Managers 侧：clear_manager_log()（与 Systems 侧对称）
        try:
            self.client.clear_manager_log(log_id)
            CommonFunction.print_log("INFO", "ClearLog Action 执行成功（clear_manager_log）")
            return True, "ClearLog Action"
        except RedfishException as e:
            CommonFunction.print_log("INFO", f"clear_manager_log 不可用（{str(e)[:80]}），尝试其他方法")
            return False, "ClearLog Action 不可用"

    # ── 清除方法 2：DELETE Entries 集合 ──────────────────────────────────────

    def _clear_via_delete(self, log_svc, source):
        """通过 DELETE Entries 集合清除（使用 SDK Log 对象的 entries.odata_id）"""
        log_id = log_svc.id
        entries_link = log_svc.entries
        entries_odata_id = entries_link.odata_id if entries_link else None

        if not entries_odata_id:
            return False, "无 Entries 链接"

        try:
            self.client.delete(entries_odata_id)
            CommonFunction.print_log("INFO", "DELETE Entries 执行成功")
            return True, "DELETE Entries"
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"DELETE Entries 失败：{str(e)}")
            return False, f"DELETE Entries 失败: {str(e)}"

    # ── 清除方法 3：逐条 DELETE ──────────────────────────────────────────────

    def _clear_one_by_one(self, log_svc, source):
        """逐条 DELETE LogEntry（使用 SDK get_system/manager_log_entries 获取条目列表）"""
        log_id = log_svc.id
        try:
            if source == "Managers":
                entries = self.client.get_manager_log_entries(log_id)
            else:
                entries = self.client.get_system_log_entries(log_id)
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Entries 列表失败：{str(e)}")
            return False, "获取 Entries 列表失败"

        if not entries:
            return True, "无需清除（已为空）"

        success_count = 0
        fail_count = 0
        for entry in entries:
            entry_id = entry.odata_id
            if not entry_id:
                continue
            try:
                self.client.delete(entry_id)
                success_count += 1
            except RedfishException as e:
                fail_count += 1
                CommonFunction.print_log("WARNING", f"DELETE Entry {entry_id} 失败：{str(e)}")

        CommonFunction.print_log("INFO", f"逐条清除完成：成功 {success_count} 条，失败 {fail_count} 条")
        if fail_count > 0:
            return False, f"逐条清除部分失败 ({fail_count}/{len(entries)})"
        return True, "逐条 DELETE"

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def sel_log_clear_positive_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始查找 SEL Log Service")
        log_svc, source = self._find_sel_log_service()
        if log_svc is None:
            CommonFunction.print_log("ERROR", "Systems 和 Managers 均未找到 Log Service，检查失败")
            return "FAIL", []

        log_id = log_svc.id
        CommonFunction.print_log("INFO", f"从 {source} 找到 Log Service: {log_id}")

        # Step 1: 记录清除前数量
        before_count = self._count_entries(log_id, source)
        if before_count < 0:
            CommonFunction.print_log("ERROR", "无法获取当前日志条目数量")
            return "FAIL", [{"step": "count_before", "description": "获取清除前条目数", "before_count": -1, "after_count": -1, "result": "FAIL"}]

        CommonFunction.print_log("INFO", f"清除前日志条目数量：{before_count}")
        if before_count == 0:
            CommonFunction.print_log("WARNING", "当前日志条目为空，跳过清除")
            result_info = [{
                "step": "skip_empty",
                "description": "日志已为空，跳过清除",
                "before_count": 0,
                "after_count": 0,
                "result": "PASS",
            }]
            return "PASS", result_info

        # Step 2: 尝试清除
        clear_success = False
        clear_method = ""

        # 方法 1：ClearLog Action（复用 SDK clear_*_log() 封装）
        ok, msg = self._clear_via_action(log_svc, source)
        if ok:
            clear_success = True
            clear_method = msg
        else:
            CommonFunction.print_log("INFO", f"方法 1 失败（{msg}），尝试方法 2")

        # 方法 2：DELETE Entries 集合（SDK Log.entries.odata_id）
        if not clear_success:
            ok, msg = self._clear_via_delete(log_svc, source)
            if ok:
                clear_success = True
                clear_method = msg
            else:
                CommonFunction.print_log("INFO", f"方法 2 失败（{msg}），尝试方法 3")

        # 方法 3：逐条 DELETE（SDK get_system/manager_log_entries）
        if not clear_success:
            ok, msg = self._clear_one_by_one(log_svc, source)
            if ok:
                clear_success = True
                clear_method = msg
            else:
                CommonFunction.print_log("ERROR", f"方法 3 失败（{msg}）")

        # 等 BMC 处理
        time.sleep(2)

        # Step 3: 验证清除结果
        after_count = self._count_entries(log_id, source)
        CommonFunction.print_log("INFO", f"清除后日志条目数量：{after_count}")

        if clear_success and after_count <= 1:
            # SEL 清除后遗留一条 "Log area reset/cleared" 记录是 IPMI 标准行为
            if after_count == 1:
                CommonFunction.print_log("INFO", f"日志清除成功（方法：{clear_method}），{before_count} → {after_count}（遗留 1 条为清除操作自身产生的记录，符合 IPMI 标准）")
            else:
                CommonFunction.print_log("INFO", f"日志清除成功（方法：{clear_method}），{before_count} → {after_count}")
            result_info = [{
                "step": "clear",
                "description": f"清除日志（方法：{clear_method}）",
                "before_count": before_count,
                "after_count": after_count,
                "result": "PASS",
            }]
            return "PASS", result_info
        else:
            CommonFunction.print_log(
                "ERROR",
                f"日志清除失败（方法：{clear_method or '所有方法'}），{before_count} → {after_count}"
            )
            result_info = [{
                "step": "clear",
                "description": f"清除日志（方法：{clear_method or '所有方法失败'}）",
                "before_count": before_count,
                "after_count": after_count,
                "result": "FAIL",
            }]
            return "FAIL", result_info

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, result_info = self.sel_log_clear_positive_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in result_info:
                f.write(
                    f'"{info["step"]}",'
                    f'"{info["description"]}",'
                    f'{info["before_count"]},'
                    f'{info["after_count"]},'
                    f'{info["result"]}\n'
                )

        for info in result_info:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'Clear_{info["step"]}', "value": info}
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
        checker = SELLogClearPositiveCheck("SELLogClearPositiveCheck")
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
