#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers_009_timezone_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC 时区设置功能测试（PATCH DateTimeLocalOffset），验证时区修改和恢复
2026/05/09: 新增默认值校验（DefaultDateTimeLocalOffset=+08:00）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

MANAGER_URI = "/redfish/v1/Managers/1"

# 测试用时区偏移（UTC+9 日本标准时间）
TEST_TIMEZONE = "+09:00"

# 期望的默认时区（东八区）
DEFAULT_TIMEZONE = "+08:00"

class Managers009TimezoneTest(BmcTestBase):
    """BMC 时区设置功能测试

    用例编号：Redfish_Managers_009
    测试内容：
    - 【默认值校验】DateTimeLocalOffset 是否默认为东八区（+08:00）
    - 修改时区偏移（+09:00），验证生效
    - 恢复原始时区，验证恢复
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_009_timezone_test.json"),
        )

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        tz_info = {}
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. 查询当前时区
            mgr = self.client.get_manager()
            original_offset = mgr.date_time_local_offset or ""
            original_datetime = mgr.date_time or ""
            test.print_log("INFO", f"当前时区: DateTimeLocalOffset={original_offset}, "
                           f"DateTime={original_datetime}")
            tz_info["original"] = {
                "DateTimeLocalOffset": original_offset,
                "DateTime": original_datetime,
            }
            checks.append(("查询当前时区成功", True))

            # 【默认值校验】是否默认为东八区
            if original_offset == DEFAULT_TIMEZONE:
                test.print_log("INFO", f"✓ 时区默认为东八区（DateTimeLocalOffset={original_offset}）")
                checks.append(("默认时区为东八区", True))
            else:
                test.print_log("ERROR", f"✗ 时区默认非东八区（当前：{original_offset}，期望：{DEFAULT_TIMEZONE}）")
                checks.append(("默认时区为东八区", False))

            # 2. 修改时区为 +09:00
            test.print_log("INFO", f"修改时区为 {TEST_TIMEZONE}...")
            self.client.patch(MANAGER_URI, {"DateTimeLocalOffset": TEST_TIMEZONE})
            time.sleep(2)

            # 3. 验证时区已修改
            mgr = self.client.get_manager()
            new_offset = mgr.date_time_local_offset or ""
            new_datetime = mgr.date_time or ""
            test.print_log("INFO", f"修改后时区: DateTimeLocalOffset={new_offset}, DateTime={new_datetime}")
            checks.append(("时区修改验证", new_offset == TEST_TIMEZONE))
            tz_info["after_change"] = {
                "DateTimeLocalOffset": new_offset,
                "DateTime": new_datetime,
            }

            # 4. 恢复原始时区
            test.print_log("INFO", f"恢复原始时区 {original_offset}...")
            self.client.patch(MANAGER_URI, {"DateTimeLocalOffset": original_offset})
            time.sleep(2)

            # [SDK] Manager model has date_time_local_offset field
            mgr = self.client.get_manager()
            restored_offset = mgr.date_time_local_offset or ""
            test.print_log("INFO", f"恢复后时区: DateTimeLocalOffset={restored_offset}")
            checks.append(("时区恢复验证", restored_offset == original_offset))
            tz_info["after_restore"] = {
                "DateTimeLocalOffset": restored_offset,
            }

            final = "PASS" if all(r for _, r in checks) else "FAIL"

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish SDK 调用失败：{str(e)}")
            final = "FAIL"
        except Exception as e:
            test.print_log("ERROR", f"测试异常：{str(e)}")
            traceback.print_exc()
            final = "FAIL"
        finally:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass

        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "timezone_info", "value": tz_info})

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
        test_name = Managers009TimezoneTest("Managers009TimezoneTest")
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
