#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers_012_kvm_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC KVM 功能测试，验证 KVM 服务配置查询、Session 参数设置及生效
2026/05/08: 改善 - 新增 MaximumNumberOfSessions ≥ 2 校验（业务要求支持 2 个及以上 KVM 会话）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

KVM_URI = "/redfish/v1/Managers/1/KvmService"

TEST_SESSION_TIMEOUT = 60   # 测试用 Session 超时时间（分钟）

class Managers012KvmTest(BmcTestBase):
    """BMC KVM 功能测试

    用例编号：Redfish_Managers_012
    测试内容：
    - 查询 KVM 服务配置（KvmUrl/MaximumNumberOfSessions/SessionTimeoutMinutes 等）
    - 验证 KvmUrl 格式合法（非空且包含 https）
    - 验证 MaximumNumberOfSessions ≥ 2（业务要求支持 2 个及以上 KVM 会话）
    - 修改 SessionTimeoutMinutes，验证生效
    - 恢复原始配置
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_012_kvm_test.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        kvm_info = {}
        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. 查询 KVM 服务配置
            # [SDK-GAP] get_raw(KVM_URI) 获取 KvmService 配置：
            #   KvmService 为 OEM 扩展路径，SDK 无对应类型化接口，保留 get_raw
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/KvmService')："
                           "KvmService 为 OEM 扩展路径，SDK 无对应类型化接口，待 SDK 补充后替换")
            kvm_data = self.client.get_raw(KVM_URI)
            kvm_url = kvm_data.get("KvmUrl", "")
            max_sessions = kvm_data.get("MaximumNumberOfSessions")
            active_sessions = kvm_data.get("NumberOfActivatedSessions")
            session_timeout = kvm_data.get("SessionTimeoutMinutes")
            encryption_enabled = kvm_data.get("EncryptionEnabled")
            activated_type = kvm_data.get("ActivatedSessionsType", "")

            test.print_log("INFO", f"KVM 配置: KvmUrl={kvm_url}, MaxSessions={max_sessions}, "
                           f"ActiveSessions={active_sessions}, Timeout={session_timeout}min, "
                           f"Encryption={encryption_enabled}, SessionType={activated_type}")
            kvm_info["initial"] = {
                "KvmUrl": kvm_url,
                "MaximumNumberOfSessions": max_sessions,
                "NumberOfActivatedSessions": active_sessions,
                "SessionTimeoutMinutes": session_timeout,
                "EncryptionEnabled": encryption_enabled,
                "ActivatedSessionsType": activated_type,
            }
            checks.append(("查询 KVM 配置成功", True))

            # 2. 验证 KvmUrl 格式合法
            url_valid = bool(kvm_url) and "https" in kvm_url
            test.print_log("INFO", f"KvmUrl 合法性: {url_valid} （{kvm_url}）")
            checks.append(("KvmUrl 非空且包含 https", url_valid))

            # 3. 验证 MaximumNumberOfSessions 为正整数且 ≥ 2（业务要求支持 2 个及以上 KVM 会话）
            max_ok = isinstance(max_sessions, int) and max_sessions > 0
            max_ge2 = isinstance(max_sessions, int) and max_sessions >= 2
            test.print_log("INFO", f"MaximumNumberOfSessions={max_sessions}, 合法: {max_ok}, ≥2: {max_ge2}")
            if not max_ge2:
                test.print_log("ERROR", f"MaximumNumberOfSessions={max_sessions} < 2，不满足业务要求（支持2个及以上KVM会话）")
            checks.append(("MaximumNumberOfSessions 为正整数", max_ok))
            checks.append(("MaximumNumberOfSessions ≥ 2（支持多会话）", max_ge2))

            # 4. 修改 SessionTimeoutMinutes 验证设置能力
            original_timeout = session_timeout
            test.print_log("INFO", f"修改 SessionTimeoutMinutes: {original_timeout} → {TEST_SESSION_TIMEOUT}")
            try:
                self.client.patch(KVM_URI, {"SessionTimeoutMinutes": TEST_SESSION_TIMEOUT})
                time.sleep(2)
                # [SDK-GAP] 同上，KvmService OEM 路径
                kvm_data = self.client.get_raw(KVM_URI)
                new_timeout = kvm_data.get("SessionTimeoutMinutes")
                test.print_log("INFO", f"验证 SessionTimeoutMinutes={new_timeout}")
                checks.append(("SessionTimeoutMinutes 设置验证", new_timeout == TEST_SESSION_TIMEOUT))
                kvm_info["after_patch"] = {"SessionTimeoutMinutes": new_timeout}
            except RedfishException as e:
                test.print_log("WARNING", f"SessionTimeoutMinutes PATCH 失败: {str(e)}")
                checks.append(("SessionTimeoutMinutes 设置验证", False))

            # 5. 恢复原始 Timeout
            test.print_log("INFO", f"恢复原始 SessionTimeoutMinutes={original_timeout}...")
            try:
                self.client.patch(KVM_URI, {"SessionTimeoutMinutes": original_timeout})
                time.sleep(1)
                # [SDK-GAP] 同上，KvmService OEM 路径
                kvm_data = self.client.get_raw(KVM_URI)
                restored = kvm_data.get("SessionTimeoutMinutes")
                checks.append(("SessionTimeoutMinutes 恢复验证", restored == original_timeout))
                kvm_info["after_restore"] = {"SessionTimeoutMinutes": restored}
            except RedfishException as e:
                test.print_log("WARNING", f"Timeout 恢复失败: {str(e)}")
                checks.append(("SessionTimeoutMinutes 恢复验证", False))

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
                                   value={"metrics": "kvm_config_info", "value": kvm_info})

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
        test_name = Managers012KvmTest("Managers012KvmTest")
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
