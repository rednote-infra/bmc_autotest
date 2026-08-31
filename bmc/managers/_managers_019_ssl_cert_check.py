#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/managers_019_ssl_cert_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/11: 新增，BMC HTTPS SSL 证书检查，验证 Issuer/Subject/ValidNotAfter/PublicKeyLengthBits
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

HTTPS_CERT_URI = "/redfish/v1/Managers/1/SecurityService/HttpsCert"

class Managers019SslCertCheck(BmcTestBase):
    """BMC HTTPS SSL 证书检查

    用例编号：Redfish_Managers_019
    测试内容：
    - GET /redfish/v1/Managers/1/SecurityService/HttpsCert 接口可达
    - Issuer 存在且非空
    - Subject 存在且非空
    - ValidNotAfter 存在
    - PublicKeyLengthBits 存在且 >=2048
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_019_ssl_cert_check.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        cert_info = {}
        final = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. GET HttpsCert 接口
            test.print_log("INFO", "=== 步骤1: 查询 HttpsCert 配置 ===")
            # [SDK-GAP] get_raw(HTTPS_CERT_URI) 获取 HttpsCert 配置：
            #   SecurityService/HttpsCert 为 OEM 扩展路径，SDK 无对应类型化接口，保留 get_raw
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/SecurityService/HttpsCert')："
                           "HttpsCert 为 OEM 扩展路径，SDK 无对应类型化接口，待 SDK 补充后替换")
            cert_data = self.client.get_raw(HTTPS_CERT_URI)
            test.print_log("INFO", "HttpsCert 接口查询成功")
            checks.append(("HttpsCert 接口查询成功", True))

            # 2. Issuer 存在且非空
            issuer = cert_data.get("Issuer")
            issuer_ok = isinstance(issuer, str) and len(issuer.strip()) > 0
            test.print_log("INFO",
                f"Issuer: {'✓' if issuer_ok else '✗'} {issuer!r}")
            checks.append(("Issuer 存在且非空", issuer_ok))

            # 3. Subject 存在且非空
            subject = cert_data.get("Subject")
            subject_ok = isinstance(subject, str) and len(subject.strip()) > 0
            test.print_log("INFO",
                f"Subject: {'✓' if subject_ok else '✗'} {subject!r}")
            checks.append(("Subject 存在且非空", subject_ok))

            # 4. ValidNotAfter 存在
            valid_not_after = cert_data.get("ValidNotAfter")
            valid_not_after_ok = valid_not_after is not None
            test.print_log("INFO",
                f"ValidNotAfter: {'✓' if valid_not_after_ok else '✗'} {valid_not_after!r}")
            checks.append(("ValidNotAfter 存在", valid_not_after_ok))

            # 5. PublicKeyLengthBits 存在且 >=2048
            pub_key_len = cert_data.get("PublicKeyLengthBits")
            pub_key_ok = isinstance(pub_key_len, (int, float)) and pub_key_len >= 2048
            test.print_log("INFO",
                f"PublicKeyLengthBits: {'✓' if pub_key_ok else '✗'} {pub_key_len!r}")
            checks.append(("PublicKeyLengthBits 存在且 >=2048", pub_key_ok))

            # 记录详细信息
            cert_info["Issuer"] = issuer
            cert_info["Subject"] = subject
            cert_info["ValidNotAfter"] = valid_not_after
            cert_info["PublicKeyLengthBits"] = pub_key_len

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
        for field_name, field_val in cert_info.items():
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": field_name, "value": field_val})
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
        test_name = Managers019SslCertCheck("Managers019SslCertCheck")
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
