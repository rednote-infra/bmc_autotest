#!/bin/python
"""
Author: Fengmian
Date: 2026/04/27
Update: 2026/07/02 — SDK v1.1.1 日志路径动态发现，消除硬编码 entries_url
Usage: python3 bmc/managers_004a_sel_log_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/27: 新增，BMC SEL 日志服务检查（LogServices/SEL）
2026/07/02: 改用 SDK v1.1.1 动态发现的 Log.entries.odata_id，消除硬编码路径
"""

import os
import re
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

TARGET_LOG_ID = "SEL"

class Managers004aSelLogCheck(BmcTestBase):
    """BMC SEL 日志服务检查

    用例编号：Redfish_Managers_004a
    检查项（等价类/边界）：
    - LogServices 列表中存在 id == "SEL" 的服务（边界：不存在为 FAIL）
    - service_enabled == True
    - max_number_of_records > 0（边界：0 为异常）
    - overwrite_policy 非空（等价类：WrapsWhenFull / NeverOverwrites 均合法）
    - Entries 集合可访问，get_manager_log_entries 返回列表（空列表也 PASS，代表无日志）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_004a_sel_log_check.json"),
        )

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            svcs = self.client.get_manager_log_services()
            target = next((s for s in svcs if getattr(s, 'id', '') == TARGET_LOG_ID), None)

            # 存在性检查（边界）
            ok = target is not None
            checks.append((f"LogService '{TARGET_LOG_ID}' 存在", ok))
            if not ok:
                test.print_log("ERROR", f"未找到 LogService id='{TARGET_LOG_ID}'，已有：{[getattr(s,'id','') for s in svcs]}")
            else:
                # service_enabled
                enabled = getattr(target, 'service_enabled', None)
                test.print_log("INFO", f"service_enabled: {enabled}")
                ok = enabled is True
                checks.append(("service_enabled == True", ok))
                if not ok:
                    test.print_log("ERROR", f"service_enabled 不为 True：{enabled}")

                # max_number_of_records > 0（边界）
                max_rec = getattr(target, 'max_number_of_records', 0)
                test.print_log("INFO", f"max_number_of_records: {max_rec}")
                ok = isinstance(max_rec, int) and max_rec > 0
                checks.append(("max_number_of_records > 0", ok))
                if not ok:
                    test.print_log("ERROR", f"max_number_of_records 异常：{max_rec}")

                # overwrite_policy 非空（等价类）
                policy = getattr(target, 'overwrite_policy', None)
                test.print_log("INFO", f"overwrite_policy: {policy}")
                ok = bool(policy)
                checks.append(("overwrite_policy 非空", ok))
                if not ok:
                    test.print_log("ERROR", "overwrite_policy 为空")

                # Entries 可访问 + 合规性校验（空列表也 PASS）
                # v1.1.1: 使用 Log.entries.odata_id 动态发现 Entries URL，不再硬编码路径
                entries_url = target.entries.odata_id if target.entries else None
                if not entries_url:
                    test.print_log("ERROR", f"LogService '{TARGET_LOG_ID}' 无 Entries 链接")
                    checks.append(("Entries 集合可访问", False))
                else:
                    test.print_log("INFO", f"动态发现 Entries URL: {entries_url}")
                    # Use SDK get_manager_log_entries() to fetch expanded entries
                    log_entries = self.client.get_manager_log_entries(TARGET_LOG_ID)
                    entry_count = len(log_entries)
                    test.print_log("INFO", f"Entries 总数：{entry_count}")

                    ok = entry_count >= 0
                    checks.append(("Entries 集合可访问", ok))
                    if not ok:
                        test.print_log("ERROR", "Entries 集合不可访问")
                    elif entry_count > 0:
                        # DMTF LogEntry v1_4_3 合规性校验（前 10 条）
                        # Required 字段（缺失 → ERROR，整体 FAIL）
                        REQUIRED = ["@odata.id", "@odata.type", "Id", "EntryType", "Name"]
                        # Field mapping from Redfish JSON key to SDK model attribute
                        FIELD_ATTR_MAP = {
                            "@odata.id": "odata_id",
                            "@odata.type": "odata_type",
                            "Id": "id",
                            "EntryType": "entry_type",
                            "Name": "name",
                        }
                        # Optional 字段（存在则校验格式/值，不通过 → WARNING，不影响整体结果）
                        OPTIONAL_SEVERITY_VALUES = {"OK", "Warning", "Critical"}

                        test.print_log("INFO", f"开始 DMTF 合规性校验（前 10 条），共 {entry_count} 条")
                        required_fail_count = 0
                        log_entries_info = []
                        for j, entry in enumerate(log_entries[:10]):
                            entry_id = entry.id or f"entry_{j}"
                            if not entry.odata_id:
                                test.print_log("ERROR", f"  集合条目[{j}] 缺少 @odata.id（Required）")
                                required_fail_count += 1
                                continue
                            entry_info = {"id": entry_id}

                            # --- Required fields check via SDK model attributes ---
                            missing_required = [
                                f for f in REQUIRED
                                if not getattr(entry, FIELD_ATTR_MAP[f], None)
                            ]
                            if missing_required:
                                required_fail_count += 1
                                test.print_log("ERROR", f"  条目[{j}]({entry_id}) 缺少 Required 字段：{missing_required}")
                            entry_info["missing_required"] = missing_required

                            # --- Optional 字段校验（WARNING，不影响整体） ---
                            # Created：存在则校验 ISO 8601 格式
                            created = entry.created or ""
                            entry_info["Created"] = created
                            if created:
                                created_ok = bool(re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}", created))
                                if not created_ok:
                                    test.print_log("WARNING", f"  条目[{j}]({entry_id}) Created 格式不符合 ISO 8601：{created}")
                                entry_info["Created_format_ok"] = created_ok

                            # Message：Optional，存在则记录
                            msg = entry.message
                            entry_info["Message"] = (msg or "")[:80]
                            if msg is not None and not msg:
                                test.print_log("WARNING", f"  条目[{j}]({entry_id}) Message 存在但为空字符串")

                            # Severity：Optional，存在则校验枚举值
                            severity = entry.severity
                            entry_info["Severity"] = severity
                            if severity is not None and severity not in OPTIONAL_SEVERITY_VALUES:
                                test.print_log("WARNING", f"  条目[{j}]({entry_id}) Severity 值不在规范枚举内：{severity}")

                            log_entries_info.append(entry_info)

                        required_ok = (required_fail_count == 0)
                        checks.append(("日志条目 Required 字段合规（DMTF v1_4_3）", required_ok))
                        if not required_ok:
                            test.print_log("ERROR", f"存在 {required_fail_count} 条条目缺少 Required 字段")

                        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                                   value={"metrics": "log_entries_sample",
                                                          "value": log_entries_info})
                    else:
                        test.print_log("INFO", "无日志条目，跳过合规性校验")

            final = "PASS" if all(r for _, r in checks) else "FAIL"

            # detail.cycle 保存各检查项结果
            for label, passed in checks:
                test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                           value={"metrics": label, "value": "PASS" if passed else "FAIL"})

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish SDK 调用失败：{str(e)}")
            final = "FAIL"
        except Exception as e:
            test.print_log("ERROR", f"测试异常：{str(e)}")
            traceback.print_exc()
            final = "FAIL"
        finally:
            if self.client:
                self.client.close()

        self.command_check_result = final
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NUM, "value": final})
        with open(self.result_csv_path, "a") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        test.print_log("INFO", f"测试完成，结果：{final}")

if __name__ == '__main__':
    exit_code = 0
    test_name = None
    try:
        test_name = Managers004aSelLogCheck("Managers004aSelLogCheck")
        test_name.run_test()
        if test_name.command_check_result in ["FINISH", "PASS"]:
            exit_code = 0
        elif test_name.command_check_result == "FAIL":
            exit_code = 2
        else:
            exit_code = 1
        start_time = time.time()
        while (time.time() - start_time) < 5:
            time.sleep(1)
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "检测到键盘中断，提前终止")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"发生未处理异常: {str(e)}")
        traceback.print_exc()
        exit_code = 1
    finally:
        try:
            if test_name and hasattr(test_name, 'exit_code_path'):
                with open(test_name.exit_code_path, "w", encoding="utf-8") as e:
                    e.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)