#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/managers_022_vnc_config_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/11: 新增，BMC VNC 服务配置读写测试
2026/10/16: SDK 1.3.0 适配 - 改用 get_vnc_service() 类型化接口，URI 由 SDK 自动发现（issue/8）

测试内容：
  1. 查询 VNC 服务当前配置，记录原始值
  2. 修改 SessionTimeoutMinutes（原值 ±5min 切换），验证生效
  3. 修改 SessionMode（在 Share/Private 间切换），验证生效
  4. 恢复原始配置，验证恢复成功

PASS 标准：
  - 查询成功，字段完整
  - PATCH 后读回值与期望值一致
  - 原始配置恢复成功
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

class Managers022VncConfigTest(BmcTestBase):
    """BMC VNC 服务配置读写测试"""

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_022_vnc_config_test.json"),
        )
        self.command_check_result = "FAIL"
    def _patch_vnc(self, test: CommonFunction, vnc_uri: str, payload: dict, desc: str) -> bool:
        """发送 PATCH 并打印结果，返回是否成功（URI 取自资源 @odata.id）"""
        try:
            self.client.patch(vnc_uri, payload)
            test.print_log("INFO", f"PATCH VNC [{desc}] 成功：{payload}")
            return True
        except RedfishException as e:
            test.print_log("ERROR", f"PATCH VNC [{desc}] 失败：{e}")
            return False
        except Exception as e:
            test.print_log("ERROR", f"PATCH VNC [{desc}] 异常：{e}")
            return False

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        detail = {}
        final  = "FAIL"
        orig_timeout = None
        orig_mode    = None

        try:
            self.client = RedfishClient(
                host=self.BMC_IP, username=self.USERNAME, password=self.PASSWORD
            )

            # ── 1. 查询初始配置（SDK 1.3.0 类型化接口，URI 由 SDK 自动发现）──
            vnc_data = self.client.get_vnc_service()
            vnc_uri = vnc_data.odata_id

            # SDK 已将 OEM 字段类型化（蛇形名），PascalCase 仅作兜底
            def _pick(snake, pascal=None):
                val = getattr(vnc_data, snake, None)
                if val is None and pascal:
                    val = getattr(vnc_data, pascal, None)
                return val

            orig_timeout = _pick("session_timeout_minutes", "SessionTimeoutMinutes")
            orig_mode    = _pick("session_mode", "SessionMode")
            ssl_enabled  = _pick("ssl_encryption_enabled", "SSLEncryptionEnabled")
            max_sessions = _pick("maximum_number_of_sessions", "MaximumNumberOfSessions")
            password_set = _pick("password", "Password")

            test.print_log("INFO",
                f"VNC 初始配置：SessionMode={orig_mode}, Timeout={orig_timeout}min, "
                f"SSLEncryption={ssl_enabled}, MaxSessions={max_sessions}, Password={password_set}")
            detail["initial"] = {
                "SessionMode":            orig_mode,
                "SessionTimeoutMinutes":  orig_timeout,
                "SSLEncryptionEnabled":   ssl_enabled,
                "MaximumNumberOfSessions": max_sessions,
            }

            # 基础字段校验
            #   SessionMode 为 null 表示本机型未启用会话模式选择，计 SKIP（None）
            if orig_mode is None:
                test.print_log("INFO", "SessionMode: - SKIP 字段为 null（本机型未启用会话模式选择）")
                checks.append(("SessionMode 字段合规", None))
            else:
                checks.append(("SessionMode 字段合规",
                                orig_mode in VALID_SESSION_MODES))
            checks.append(("SessionTimeoutMinutes 为正整数",
                            isinstance(orig_timeout, int) and orig_timeout > 0))
            checks.append(("SSLEncryptionEnabled 为 bool",
                            isinstance(ssl_enabled, bool)))
            checks.append(("MaximumNumberOfSessions ≥ 1",
                            isinstance(max_sessions, int) and max_sessions >= 1))

            # ── 2. 修改 SessionTimeoutMinutes ────────────────────────────
            # 原值在 [1,55] 取 +5，否则取 -5，确保在合法范围 [1,60] 内；
            # 原值为 None 时直接取 30。
            if orig_timeout is None:
                test_timeout = 30
            elif orig_timeout <= 55:
                test_timeout = orig_timeout + 5
            else:
                test_timeout = max(orig_timeout - 5, 1)
            patch_ok = self._patch_vnc(test, vnc_uri, {"SessionTimeoutMinutes": test_timeout}, "修改超时")
            if patch_ok:
                time.sleep(2)
                vnc_after = self.client.get_vnc_service()
                actual_timeout = getattr(vnc_after, "session_timeout_minutes", None)
                if actual_timeout is None:
                    actual_timeout = getattr(vnc_after, "SessionTimeoutMinutes", None)
                timeout_ok = actual_timeout == test_timeout
                checks.append((f"SessionTimeoutMinutes PATCH 生效（期望={test_timeout}, 实际={actual_timeout}）",
                                timeout_ok))
                detail["timeout_patch"] = {
                    "expected": test_timeout,
                    "actual":   actual_timeout,
                    "result":   "PASS" if timeout_ok else "FAIL",
                }
            else:
                checks.append(("SessionTimeoutMinutes PATCH 请求成功", False))

            # ── 3. 修改 SessionMode（在 Share/Private 间切换）────────────
            if orig_mode in VALID_SESSION_MODES:
                test_mode = "Private" if orig_mode == "Share" else "Share"
                patch_ok = self._patch_vnc(test, vnc_uri, {"SessionMode": test_mode}, "修改会话模式")
                if patch_ok:
                    time.sleep(2)
                    vnc_after = self.client.get_vnc_service()
                    actual_mode = getattr(vnc_after, "session_mode", None)
                    if actual_mode is None:
                        actual_mode = getattr(vnc_after, "SessionMode", None)
                    mode_ok = actual_mode == test_mode
                    checks.append((f"SessionMode PATCH 生效（期望={test_mode}, 实际={actual_mode}）",
                                    mode_ok))
                    detail["mode_patch"] = {
                        "expected": test_mode,
                        "actual":   actual_mode,
                        "result":   "PASS" if mode_ok else "FAIL",
                    }
                else:
                    checks.append(("SessionMode PATCH 请求成功", False))
            else:
                test.print_log("WARNING", f"SessionMode 初始值 {orig_mode} 不在已知范围，跳过切换测试")

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish 异常：{e}")
        except Exception as e:
            test.print_log("ERROR", f"异常：{e}")
            traceback.print_exc()
        finally:
            # ── 4. 恢复原始配置 ───────────────────────────────────────────
            if self.client and (orig_timeout is not None or orig_mode is not None):
                restore_payload = {}
                if orig_timeout is not None: restore_payload["SessionTimeoutMinutes"] = orig_timeout
                if orig_mode    is not None: restore_payload["SessionMode"] = orig_mode
                restore_ok = self._patch_vnc(test, vnc_uri, restore_payload, "恢复原始配置")
                if restore_ok:
                    time.sleep(2)
                    try:
                        vnc_restored = self.client.get_vnc_service()
                        t_restored = getattr(vnc_restored, "session_timeout_minutes", None)
                        if t_restored is None:
                            t_restored = getattr(vnc_restored, "SessionTimeoutMinutes", None)
                        m_restored = getattr(vnc_restored, "session_mode", None)
                        if m_restored is None:
                            m_restored = getattr(vnc_restored, "SessionMode", None)
                        t_ok = t_restored == orig_timeout
                        m_ok = m_restored == orig_mode
                        restore_verified = t_ok and m_ok
                    except Exception:
                        restore_verified = False
                    checks.append(("原始配置恢复成功", restore_verified))
                    detail["restore"] = {
                        "timeout_restored": orig_timeout,
                        "mode_restored":    orig_mode,
                        "result": "PASS" if restore_verified else "FAIL",
                    }
                else:
                    checks.append(("原始配置恢复 PATCH 成功", False))
            try:
                self.client.close()
            except Exception:
                pass

        # checks 中值为 None 表示 SKIP，不参与 PASS/FAIL 判定
        final = "PASS" if checks and all(r for _, r in checks if r is not None) else "FAIL"
        for label, ok in checks:
            if ok is None:
                test.print_log("INFO", f"- {label}（SKIP）")
            else:
                test.print_log("INFO" if ok else "ERROR", f"{'✓' if ok else '✗'} {label}")
        test.print_log("INFO" if final == "PASS" else "ERROR", f"测试结束，结果：{final}")

        detail["checks"] = [
            {"label": l, "result": "PASS" if r else ("SKIP" if r is None else "FAIL")}
            for l, r in checks
        ]
        test.add_key_value_to_json(self.result_json_path, "detail.cycle", value=detail)
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NAME, "value": final})
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        self.command_check_result = final

if __name__ == "__main__":
    exit_code = 0
    obj = None
    try:
        obj = Managers022VncConfigTest("Managers022VncConfigTest")
        obj.run_test()
        exit_code = 0 if obj.command_check_result == "PASS" else 2
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"异常：{e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        if obj and hasattr(obj, "exit_code_path"):
            with open(obj.exit_code_path, "w", encoding="utf-8") as f:
                f.write(str(exit_code))
    sys.exit(exit_code)
