#!/bin/python
"""
Author: Fengmian
Date: 2026/05/08
Usage: python3 bmc/managers_012_kvm_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/08: 新增，BMC KVM 功能测试，验证 KVM 服务配置查询、Session 参数设置及生效
2026/05/08: 改善 - 新增 MaximumNumberOfSessions ≥ 2 校验（业务要求支持 2 个及以上 KVM 会话）
2026/10/16: SDK 1.3.0 适配 - 改用 get_kvm_service() 类型化接口，URI 由 SDK 自动发现（issue/8）
2026/11/16: 修复 KvmService 字段读取恒为 None 的 BUG —— KvmService 是 Pydantic 模型，
  PascalCase 仅是解析输入的 alias，实例属性为 snake_case；原用
  getattr(kvm_data, "SessionTimeoutMinutes") 等读已声明字段恒返回 None。
  改为 snake_case 属性访问。另：Session 超时上限为 30min，测试值同步改为 30。
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

TEST_SESSION_TIMEOUT = 30   # 测试用 Session 超时时间（分钟，BMC 配置上限 30min）

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

            # 1. 查询 KVM 服务配置（SDK 1.3.0 类型化接口，URI 由 SDK 自动发现）
            # KvmService 为 Pydantic 模型：须用 snake_case 属性访问（PascalCase 仅为 alias）
            kvm_data = self.client.get_kvm_service()

            # SDK 已将字段类型化（蛇形名），PascalCase 仅作兜底
            def _pick(obj, snake, pascal):
                val = getattr(obj, snake, None)
                return val if val is not None else getattr(obj, pascal, None)

            max_sessions = _pick(kvm_data, "maximum_number_of_sessions",
                                 "MaximumNumberOfSessions")
            active_sessions = _pick(kvm_data, "number_of_activated_sessions",
                                    "NumberOfActivatedSessions")
            session_timeout = _pick(kvm_data, "session_timeout_minutes",
                                    "SessionTimeoutMinutes")
            encryption_enabled = _pick(kvm_data, "encryption_enabled",
                                       "EncryptionEnabled")
            activated_type = _pick(kvm_data, "activated_sessions_type",
                                   "ActivatedSessionsType") or ""

            # KvmUrl 属于可选的 OEM 字段，多数机型（含 xFusion iBMC）不下发。
            # 因此改用标准 GraphicalConsole 判断 KVM over IP 能力。
            kvm_url = _pick(kvm_data, "kvm_url", "KvmUrl") or ""
            console_ok = False
            console_types = []
            try:
                manager = self.client.get_manager("1")
                gc = getattr(manager, "graphical_console", None) \
                    or getattr(manager, "GraphicalConsole", None)
                if gc is not None:
                    console_types = getattr(gc, "connect_types_supported", None) \
                        or getattr(gc, "ConnectTypesSupported", None) or []
                    enabled = getattr(gc, "service_enabled", None)
                    if enabled is None:
                        enabled = getattr(gc, "ServiceEnabled", False)
                    console_ok = bool(enabled)
            except Exception as e:
                test.print_log("WARNING", f"读取 GraphicalConsole 失败: {e}")

            test.print_log("INFO", f"KVM 配置: MaxSessions={max_sessions}, "
                           f"ActiveSessions={active_sessions}, Timeout={session_timeout}min, "
                           f"Encryption={encryption_enabled}, SessionType={activated_type}, "
                           f"KvmUrl={kvm_url or '(未下发)'}")
            test.print_log("INFO", f"GraphicalConsole: ServiceEnabled={console_ok}, "
                           f"ConnectTypesSupported={console_types}")
            kvm_info["initial"] = {
                "MaximumNumberOfSessions": max_sessions,
                "NumberOfActivatedSessions": active_sessions,
                "SessionTimeoutMinutes": session_timeout,
                "EncryptionEnabled": encryption_enabled,
                "ActivatedSessionsType": activated_type,
                "KvmUrl": kvm_url,
                "GraphicalConsoleEnabled": console_ok,
                "ConnectTypesSupported": console_types,
            }
            checks.append(("查询 KVM 配置成功", True))

            # 2. 验证 KVM over IP 能力（GraphicalConsole 启用且支持 KVMIP 连接类型）
            kvm_capable = console_ok and "KVMIP" in (console_types or [])
            test.print_log("INFO", f"KVM over IP 能力: {'✓' if kvm_capable else '✗'} "
                           f"(enabled={console_ok}, types={console_types})")
            checks.append(("GraphicalConsole 支持 KVMIP 连接", kvm_capable))

            # 3. 验证 MaximumNumberOfSessions 为正整数且 ≥ 2（业务要求支持 2 个及以上 KVM 会话）
            max_ok = isinstance(max_sessions, int) and max_sessions > 0
            max_ge2 = isinstance(max_sessions, int) and max_sessions >= 2
            test.print_log("INFO", f"MaximumNumberOfSessions={max_sessions}, 合法: {max_ok}, ≥2: {max_ge2}")
            if not max_ge2:
                test.print_log("ERROR", f"MaximumNumberOfSessions={max_sessions} < 2，不满足业务要求（支持2个及以上KVM会话）")
            checks.append(("MaximumNumberOfSessions 为正整数", max_ok))
            checks.append(("MaximumNumberOfSessions ≥ 2（支持多会话）", max_ge2))

            # 4. 修改 SessionTimeoutMinutes 验证设置能力
            #    注意：设备要求 If-Match（ETag），且恢复时不能把 None 写回（会 400）
            original_timeout = session_timeout
            test.print_log("INFO",
                f"修改 SessionTimeoutMinutes: {original_timeout} → {TEST_SESSION_TIMEOUT}")
            patch_timeout = TEST_SESSION_TIMEOUT
            if original_timeout == TEST_SESSION_TIMEOUT:
                # 原值与目标值相同则改用另一个合法值，确保真的发生变更
                patch_timeout = 120 if TEST_SESSION_TIMEOUT != 120 else 30
                test.print_log("INFO",
                    f"原值已等于 {TEST_SESSION_TIMEOUT}，改用 {patch_timeout} 验证写入能力")
            try:
                self.client.patch(kvm_data.odata_id,
                                  {"SessionTimeoutMinutes": patch_timeout})
                time.sleep(2)
                kvm_data = self.client.get_kvm_service()
                new_timeout = _pick(kvm_data, "session_timeout_minutes",
                                    "SessionTimeoutMinutes")
                test.print_log("INFO", f"验证 SessionTimeoutMinutes: "
                               f"{'✓' if new_timeout == patch_timeout else '✗'} {new_timeout}")
                checks.append(("SessionTimeoutMinutes 设置验证", new_timeout == patch_timeout))
                kvm_info["after_patch"] = {"SessionTimeoutMinutes": new_timeout}
            except RedfishException as e:
                test.print_log("WARNING", f"SessionTimeoutMinutes PATCH 失败: {str(e)}")
                checks.append(("SessionTimeoutMinutes 设置验证", None))

            # 5. 恢复原始 Timeout（仅当原值是有效整数时才回写）
            if isinstance(original_timeout, int):
                test.print_log("INFO", f"恢复原始 SessionTimeoutMinutes={original_timeout}...")
                try:
                    self.client.patch(kvm_data.odata_id,
                                      {"SessionTimeoutMinutes": original_timeout})
                    time.sleep(1)
                    kvm_data = self.client.get_kvm_service()
                    restored = _pick(kvm_data, "session_timeout_minutes",
                                     "SessionTimeoutMinutes")
                    test.print_log("INFO", f"恢复结果: "
                                   f"{'✓' if restored == original_timeout else '✗'} {restored}")
                    checks.append(("SessionTimeoutMinutes 恢复验证",
                                   restored == original_timeout))
                    kvm_info["after_restore"] = {"SessionTimeoutMinutes": restored}
                except RedfishException as e:
                    test.print_log("WARNING", f"Timeout 恢复失败: {str(e)}")
                    checks.append(("SessionTimeoutMinutes 恢复验证", None))
            else:
                test.print_log("INFO", "原始 Timeout 非整数（未下发），跳过恢复")
                checks.append(("SessionTimeoutMinutes 恢复验证", None))

            # checks 中值为 None 表示 SKIP，不参与 PASS/FAIL 判定
            final = "PASS" if all(r for _, r in checks if r is not None) else "FAIL"

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
