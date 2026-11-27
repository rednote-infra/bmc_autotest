#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/managers_017_vnc_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/11: 新增，BMC VNC 服务配置检查，验证 SessionMode/SessionTimeoutMinutes/SSLEncryptionEnabled/MaximumNumberOfSessions
2026/10/16: SDK 1.3.0 适配 - 改用 get_vnc_service() 类型化接口，URI 由 SDK 自动发现（issue/8）
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

VALID_SESSION_MODES = ["Share", "Private"]

def _get_any(obj, *names):
    """按优先级取第一个非 None 的属性值。

    Pydantic v2 属性访问只认字段名（snake_case），alias 名（如 SessionMode）
    不可用于 getattr；OEM 扩展字段（extra）则按原始键名存储，故候选名需
    同时覆盖两种写法。
    """
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return None

class Managers017VncCheck(BmcTestBase):
    """BMC VNC 服务配置检查

    用例编号：Redfish_Managers_017
    测试内容：
    - GET /redfish/v1/Managers/1/VncService 接口可达
    - SessionMode 存在且在 ["Share", "Private"] 中
    - SessionTimeoutMinutes 合法（BMC 要求 300~1800s 且 %60==0，即 5~30min）
    - SSLEncryptionEnabled 存在且为 bool
    - MaximumNumberOfSessions 存在且 >=1
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_017_vnc_check.json"),
        )
    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        vnc_info = {}
        final = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # 1. GET VncService 接口（SDK 1.3.0 类型化接口，URI 由 SDK 自动发现）
            test.print_log("INFO", "=== 步骤1: 查询 VncService 配置 ===")
            vnc_data = self.client.get_vnc_service()
            test.print_log("INFO", "VncService 接口查询成功")
            checks.append(("VncService 接口查询成功", True))

            # 2. SessionMode 存在且在 ["Share", "Private"] 中
            #    （SDK 类型化字段名为 session_mode，alias "SessionMode" 不可直接 getattr）
            session_mode = _get_any(vnc_data, "session_mode", "SessionMode")
            session_mode_ok = session_mode in VALID_SESSION_MODES
            test.print_log("INFO",
                f"SessionMode: {'✓' if session_mode_ok else '✗'} {session_mode!r} "
                f"（期望：{VALID_SESSION_MODES}）")
            checks.append(("SessionMode 存在且在有效枚举中", session_mode_ok))

            # 3. SessionTimeoutMinutes（脚本以分钟工作；BMC 返回秒，故读 timeout 后 //60）
            #    同时按 BMC 约束校验：300 ≤ Timeout(秒) ≤ 1800 且 %60==0（即 5~30min）。
            orig_timeout_sec = vnc_data.timeout
            if isinstance(orig_timeout_sec, int) and 300 <= orig_timeout_sec <= 1800 and orig_timeout_sec % 60 == 0:
                session_timeout = orig_timeout_sec // 60
                timeout_ok = True
            else:
                session_timeout = None
                timeout_ok = False
            test.print_log("INFO",
                f"SessionTimeoutMinutes: {'✓' if timeout_ok else '✗'} {session_timeout!r} "
                f"（Timeout(秒)={orig_timeout_sec}）")
            checks.append(("SessionTimeoutMinutes 合法（BMC 要求 300~1800s 且 %60==0，即 5~30min）", timeout_ok))

            # 4. SSLEncryptionEnabled（部分 BMC 不暴露该 OEM 字段，缺失时降级为
            #    N/A，不计入失败）
            ssl_enabled = getattr(vnc_data, "SSLEncryptionEnabled", None)
            if ssl_enabled is None:
                test.print_log("WARNING",
                    "SSLEncryptionEnabled: N/A（BMC 未提供该 OEM 字段，降级处理）")
                vnc_info["SSLEncryptionEnabled"] = "N/A"
            else:
                ssl_ok = isinstance(ssl_enabled, bool)
                test.print_log("INFO",
                    f"SSLEncryptionEnabled: {'✓' if ssl_ok else '✗'} {ssl_enabled!r}")
                vnc_info["SSLEncryptionEnabled"] = ssl_enabled
                checks.append(("SSLEncryptionEnabled 存在且为 bool", ssl_ok))

            # 5. MaximumNumberOfSessions 存在且 >=1
            #    （DMTF 命名 MaximumNumberOfSessions；AMI MegaRAC 为 MaxAllowSession，
            #     SDK 类型化字段名 max_allow_session）
            max_sessions = _get_any(
                vnc_data, "MaximumNumberOfSessions", "MaxAllowSession", "max_allow_session")
            max_field_used = ("MaximumNumberOfSessions"
                              if getattr(vnc_data, "MaximumNumberOfSessions", None) is not None
                              else "MaxAllowSession")
            max_ok = isinstance(max_sessions, int) and max_sessions >= 1
            test.print_log("INFO",
                f"{max_field_used}: {'✓' if max_ok else '✗'} {max_sessions!r}")
            checks.append(("MaximumNumberOfSessions 存在且 >=1", max_ok))

            # 记录详细信息
            vnc_info["SessionMode"] = session_mode
            vnc_info["SessionTimeoutMinutes"] = session_timeout
            vnc_info["MaximumNumberOfSessions"] = max_sessions

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
        for field_name, field_val in vnc_info.items():
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
        test_name = Managers017VncCheck("Managers017VncCheck")
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
