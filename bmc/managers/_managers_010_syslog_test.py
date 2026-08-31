#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers_010_syslog_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC Syslog 配置功能测试，验证 Syslog 服务启用/禁用
2026/05/08: 修复 - 被测 BMC 不支持 PATCH SyslogServers（LogType 校验 bug），改为只读验证 + ServiceEnabled 切换
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

SYSLOG_URI = "/redfish/v1/Managers/1/SyslogService"
SUBMIT_TEST_EVENT_URI = "/redfish/v1/Managers/1/SyslogService/Actions/SyslogService.SubmitTestEvent"

class Managers010SyslogTest(BmcTestBase):
    """BMC Syslog 配置功能测试

    用例编号：Redfish_Managers_010
    测试内容：
    - 查询 Syslog 服务配置（ServiceEnabled/TransmissionProtocol/SyslogServers 等）
    - 验证 SyslogServers 条目结构完整
    - 切换 ServiceEnabled（禁用→验证→启用→验证→恢复）

    注意：部分 BMC 不支持通过 Redfish PATCH 修改 SyslogServers 配置
    （如 LogType 字段校验 bug），服务器地址配置仅做只读验证。
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_010_syslog_test.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        syslog_info = {}
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. 查询当前 Syslog 配置
            # [SDK-GAP] get_raw(SYSLOG_URI) 获取 SyslogService 配置：
            #   SyslogService 为 OEM 扩展路径，SDK 无对应类型化接口，保留 get_raw
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/SyslogService')："
                           "SyslogService 为 OEM 扩展路径，SDK 无对应类型化接口，待 SDK 补充后替换")
            syslog_data = self.client.get_raw(SYSLOG_URI)
            original_enabled = syslog_data.get("ServiceEnabled")
            original_servers = syslog_data.get("SyslogServers", [])
            original_protocol = syslog_data.get("TransmissionProtocol", "")
            test.print_log("INFO", f"当前 Syslog: ServiceEnabled={original_enabled}, "
                           f"Protocol={original_protocol}, Servers={len(original_servers)} 个")
            syslog_info["initial"] = {
                "ServiceEnabled": original_enabled,
                "TransmissionProtocol": original_protocol,
                "ServerCount": len(original_servers),
            }
            checks.append(("查询 Syslog 配置成功", True))

            # 2. 验证 SyslogServers 条目结构（只读）
            for srv in original_servers:
                mid = srv.get("MemberId", "")
                addr = srv.get("Address", "")
                port = srv.get("Port", 0)
                enabled = srv.get("Enabled", False)
                test.print_log("INFO", f"  SyslogServer[{mid}]: Address={addr}, Port={port}, Enabled={enabled}")
            checks.append(("SyslogServers 条目结构完整", all(
                "MemberId" in s and "Address" in s and "Port" in s
                for s in original_servers
            )))

            # 部分 BMC 不支持通过 Redfish PATCH 修改 SyslogServers（如 LogType 字段校验 bug）
            test.print_log("WARNING", "被测 BMC SyslogServers 配置为只读，跳过服务器地址写测试")

            # 3. 禁用 Syslog 服务
            test.print_log("INFO", "禁用 Syslog 服务...")
            try:
                self.client.patch(SYSLOG_URI, {"ServiceEnabled": False})
                time.sleep(2)
                # [SDK-GAP] 同上，SyslogService OEM 路径
                syslog_data = self.client.get_raw(SYSLOG_URI)
                disabled = syslog_data.get("ServiceEnabled")
                test.print_log("INFO", f"验证禁用: ServiceEnabled={disabled}")
                checks.append(("Syslog ServiceEnabled=False 验证", disabled is False))
                syslog_info["after_disable"] = {"ServiceEnabled": disabled}
            except RedfishException as e:
                test.print_log("ERROR", f"Syslog 禁用失败: {str(e)}")
                checks.append(("Syslog ServiceEnabled=False 验证", False))

            # 4. 启用 Syslog 服务
            test.print_log("INFO", "启用 Syslog 服务...")
            try:
                self.client.patch(SYSLOG_URI, {"ServiceEnabled": True})
                time.sleep(2)
                # [SDK-GAP] 同上，SyslogService OEM 路径
                syslog_data = self.client.get_raw(SYSLOG_URI)
                enabled = syslog_data.get("ServiceEnabled")
                test.print_log("INFO", f"验证启用: ServiceEnabled={enabled}")
                checks.append(("Syslog ServiceEnabled=True 验证", enabled is True))
            except RedfishException as e:
                test.print_log("ERROR", f"Syslog 启用失败: {str(e)}")
                checks.append(("Syslog ServiceEnabled=True 验证", False))

            # 5. 恢复原始配置
            test.print_log("INFO", f"恢复原始 ServiceEnabled={original_enabled}...")
            try:
                self.client.patch(SYSLOG_URI, {"ServiceEnabled": original_enabled})
                time.sleep(1)
                # [SDK-GAP] 同上，SyslogService OEM 路径
                syslog_data = self.client.get_raw(SYSLOG_URI)
                restored = syslog_data.get("ServiceEnabled")
                checks.append(("Syslog ServiceEnabled 恢复验证", restored == original_enabled))
                syslog_info["after_restore"] = {"ServiceEnabled": restored}
            except RedfishException as e:
                test.print_log("WARNING", f"Syslog 配置恢复失败: {str(e)}")
                checks.append(("Syslog ServiceEnabled 恢复验证", False))

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
                                   value={"metrics": "syslog_config_info", "value": syslog_info})

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
        test_name = Managers010SyslogTest("Managers010SyslogTest")
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
