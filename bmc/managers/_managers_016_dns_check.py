#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/managers_016_dns_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/11: 新增，BMC DNS 服务配置检查，验证 HostNameConfig 和 DnsIpConfig 字段
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

DNS_SERVICE_URI = "/redfish/v1/Managers/1/DnsService"

class Managers016DnsCheck(BmcTestBase):
    """BMC DNS 服务配置检查

    用例编号：Redfish_Managers_016
    测试内容：
    - GET /redfish/v1/Managers/1/DnsService 接口可达
    - HostNameConfig 字段存在且为 dict
    - DnsIpConfig 字段存在且为 dict
    - DnsIpConfig.NameServers 存在（list，可为空）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_016_dns_check.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        dns_info = {}
        final = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. GET DnsService 接口
            test.print_log("INFO", "=== 步骤1: 查询 DnsService 配置 ===")
            # [SDK-GAP] get_raw(DNS_SERVICE_URI) 获取 DnsService 配置：
            #   DnsService 为 OEM 扩展路径，SDK 无对应类型化接口，保留 get_raw
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/DnsService')："
                           "DnsService 为 OEM 扩展路径，SDK 无对应类型化接口，待 SDK 补充后替换")
            dns_data = self.client.get_raw(DNS_SERVICE_URI)
            test.print_log("INFO", "DnsService 接口查询成功")
            checks.append(("DnsService 接口查询成功", True))

            # 2. HostNameConfig 字段存在且为 dict
            hostname_config = dns_data.get("HostNameConfig")
            hostname_ok = isinstance(hostname_config, dict)
            test.print_log("INFO", f"HostNameConfig: {'✓ 存在且为 dict' if hostname_ok else '✗ 缺失或类型不对'}")
            checks.append(("HostNameConfig 存在且为 dict", hostname_ok))

            # 3. DnsIpConfig 字段存在且为 dict
            dns_ip_config = dns_data.get("DnsIpConfig")
            dns_ip_ok = isinstance(dns_ip_config, dict)
            test.print_log("INFO", f"DnsIpConfig: {'✓ 存在且为 dict' if dns_ip_ok else '✗ 缺失或类型不对'}")
            checks.append(("DnsIpConfig 存在且为 dict", dns_ip_ok))

            # 4. DnsIpConfig.NameServers 存在（list，可为空）
            nameservers_ok = False
            nameservers_val = None
            if isinstance(dns_ip_config, dict):
                nameservers_val = dns_ip_config.get("NameServers")
                nameservers_ok = isinstance(nameservers_val, list)
            test.print_log("INFO",
                f"DnsIpConfig.NameServers: {'✓ 存在且为 list' if nameservers_ok else '✗ 缺失或类型不对'} "
                f"（值：{nameservers_val}）")
            checks.append(("DnsIpConfig.NameServers 存在（list）", nameservers_ok))

            # 记录详细信息
            dns_info["HostNameConfig"] = hostname_config
            dns_info["DnsIpConfig"] = dns_ip_config

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

        # 结果写入
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "HostNameConfig", "value": hostname_config})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "DnsIpConfig", "value": dns_ip_config})
        for label, passed in checks:
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": "PASS" if passed else "FAIL"})

        self.command_check_result = final
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NAME, "value": final})
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        test.print_log("INFO", f"测试完成，结果：{final}")

if __name__ == '__main__':
    exit_code = 0
    test_name = None
    try:
        test_name = Managers016DnsCheck("Managers016DnsCheck")
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
