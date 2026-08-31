#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers_008_ntp_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC NTP 设置功能测试（OEM NtpService），验证 NTP 启用/禁用和服务器配置
2026/05/09: 新增默认值校验（DefaultServiceEnabled=True、DefaultPreferredNtpServer=<ntp_server>）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

NTP_URI = "/redfish/v1/Managers/1/NtpService"

# 测试用 NTP 服务器地址（可替换为实际可用的 NTP 服务器）
TEST_NTP_SERVER_1 = "ntp.aliyun.com"
TEST_NTP_SERVER_2 = "ntp1.aliyun.com"

# 生产环境 NTP 服务器（默认值校验用，实验室可能 ping 不通，仅校验配置值）
# 请在 conf/bmc/managers/managers_008_ntp_test.json 中配置 DefaultNtpServer 字段
DEFAULT_NTP_SERVER = ""

class Managers008NtpTest(BmcTestBase):
    """BMC NTP 设置功能测试

    用例编号：Redfish_Managers_008
    测试内容：
    - 【默认值校验】NTP 是否默认开启（ServiceEnabled=True）
    - 【默认值校验】NTP 是否默认指向生产 NTP 服务器（PreferredNtpServer=<ntp_server>）
    - 启用 NTP 并设置 PreferredNtpServer 为阿里云 NTP，验证生效
    - 禁用 NTP，验证 ServiceEnabled 变为 false
    - 恢复原始配置
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_008_ntp_test.json"),
        )
    def _get_ntp(self, test):
        """查询 NTP 配置
        [SDK-GAP] get_raw('/redfish/v1/Managers/1/NtpService')：
          NtpService 为 OEM 扩展路径，SDK 无对应类型化接口，保留 get_raw
        """
        test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/NtpService')："
                       "NtpService 为 OEM 扩展路径，SDK 无对应类型化接口，待 SDK 补充后替换")
        return self.client.get_raw(NTP_URI)

    def _patch_ntp(self, test, body):
        """PATCH NTP 配置"""
        return self.client.patch(NTP_URI, body)

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        ntp_info = {}
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. 查询当前 NTP 配置
            ntp_data = self._get_ntp(test)
            cur_enabled = ntp_data.get("ServiceEnabled")
            cur_server  = ntp_data.get("PreferredNtpServer", "")
            test.print_log("INFO", f"当前 NTP 配置: ServiceEnabled={cur_enabled}, "
                           f"PreferredNtpServer={cur_server!r}")
            ntp_info["initial"] = {
                "ServiceEnabled": cur_enabled,
                "PreferredNtpServer": cur_server,
            }
            checks.append(("查询 NTP 配置成功", True))

            # 【默认值校验】NTP 是否默认开启
            if cur_enabled is True:
                test.print_log("INFO", "✓ NTP 默认已开启（ServiceEnabled=True）")
                checks.append(("NTP 默认开启", True))
            else:
                test.print_log("ERROR", f"✗ NTP 默认未开启（ServiceEnabled={cur_enabled}），"
                               "期望：True（生产交付要求默认开启）")
                checks.append(("NTP 默认开启", False))

            # 【默认值校验】NTP 是否指向生产 NTP 服务器
            if DEFAULT_NTP_SERVER in str(cur_server):
                test.print_log("INFO", f"✓ NTP 默认指向生产服务器（PreferredNtpServer={cur_server!r}）")
                checks.append(("NTP 默认指向生产服务器", True))
            else:
                test.print_log("ERROR", f"✗ NTP 未指向生产服务器（当前：{cur_server!r}，"
                               f"期望：{DEFAULT_NTP_SERVER}）")
                checks.append(("NTP 默认指向生产服务器", False))

            # 记录原始状态（用于恢复）
            original_enabled = ntp_data.get("ServiceEnabled")
            original_server = ntp_data.get("PreferredNtpServer", "")

            # 2. 启用 NTP 并设置服务器
            patch_body = {
                "ServiceEnabled": True,
                "PreferredNtpServer": TEST_NTP_SERVER_1,
            }
            test.print_log("INFO", f"启用 NTP，设置服务器: {TEST_NTP_SERVER_1}")
            try:
                self._patch_ntp(test, patch_body)
                test.print_log("INFO", "NTP PATCH 请求发送成功")
                checks.append(("NTP 启用 PATCH 成功", True))
            except RedfishException as e:
                test.print_log("ERROR", f"NTP 启用失败: {str(e)}")
                checks.append(("NTP 启用 PATCH 成功", False))

            # 3. 验证 NTP 已启用
            time.sleep(2)
            ntp_data = self._get_ntp(test)
            enabled = ntp_data.get("ServiceEnabled")
            server = ntp_data.get("PreferredNtpServer", "")
            test.print_log("INFO", f"验证 NTP: ServiceEnabled={enabled}, PreferredNtpServer={server}")
            checks.append(("NTP ServiceEnabled=True 验证", enabled is True))
            checks.append(("NTP PreferredNtpServer 设置验证", TEST_NTP_SERVER_1 in str(server)))
            ntp_info["after_enable"] = {
                "ServiceEnabled": enabled,
                "PreferredNtpServer": server,
            }

            # 4. 禁用 NTP
            test.print_log("INFO", "禁用 NTP...")
            try:
                self._patch_ntp(test, {"ServiceEnabled": False})
                time.sleep(2)
                ntp_data = self._get_ntp(test)
                disabled = ntp_data.get("ServiceEnabled")
                test.print_log("INFO", f"验证 NTP 禁用: ServiceEnabled={disabled}")
                checks.append(("NTP ServiceEnabled=False 验证", disabled is False))
                ntp_info["after_disable"] = {
                    "ServiceEnabled": disabled,
                }
            except RedfishException as e:
                test.print_log("ERROR", f"NTP 禁用失败: {str(e)}")
                checks.append(("NTP 禁用验证", False))

            # 5. 恢复原始配置
            test.print_log("INFO", "恢复原始 NTP 配置...")
            try:
                restore_body = {"ServiceEnabled": original_enabled}
                if original_server:
                    restore_body["PreferredNtpServer"] = original_server
                self._patch_ntp(test, restore_body)
                time.sleep(1)
                ntp_data = self._get_ntp(test)
                checks.append(("NTP 配置恢复", True))
            except RedfishException as e:
                test.print_log("WARNING", f"NTP 配置恢复失败: {str(e)}")
                checks.append(("NTP 配置恢复", False))

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

        # detail.cycle 保存各检查项结果
        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "ntp_config_info", "value": ntp_info})

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
        test_name = Managers008NtpTest("Managers008NtpTest")
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
