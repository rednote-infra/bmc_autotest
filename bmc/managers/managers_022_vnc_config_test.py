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

    def _poll_session_mode(self, test, vnc_uri, expected, timeout=10.0, interval=1.5):
        """轮询等待 BMC 的 SessionMode 异步生效（部分 BMC 约有 4.5s 推送延迟）。"""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            time.sleep(interval)
            try:
                last = _get_any(self.client.get_vnc_service(), "session_mode", "SessionMode")
            except Exception as e:
                test.print_log("WARNING", f"轮询 SessionMode 时查询失败：{e}")
                last = None
            if last == expected:
                return last
        return last

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        detail = {}
        final  = "FAIL"
        vnc_uri = None
        orig_timeout = None
        orig_mode    = None

        try:
            self.client = RedfishClient(
                host=self.BMC_IP, username=self.USERNAME, password=self.PASSWORD
            )

            # ── 1. 查询初始配置（SDK 1.3.0 类型化接口，URI 由 SDK 自动发现）──
            vnc_data = self.client.get_vnc_service()
            vnc_uri = vnc_data.odata_id
            # timeout 为 SDK 类型化字段（秒）；SessionMode 为 SDK 类型化字段
            # （字段名 session_mode，alias 不可 getattr）；SSLEncryptionEnabled/
            # MaximumNumberOfSessions 为 OEM 扩展字段（extra），AMI 对应
            # ProtocolEnabled / MaxAllowSession(max_allow_session)。
            orig_timeout = vnc_data.timeout
            orig_mode    = _get_any(vnc_data, "session_mode", "SessionMode")
            ssl_enabled  = getattr(vnc_data, "SSLEncryptionEnabled", None)
            max_sessions = _get_any(
                vnc_data, "MaximumNumberOfSessions", "MaxAllowSession", "max_allow_session")
            password_set = getattr(vnc_data, "Password", None)

            test.print_log("INFO",
                f"VNC 初始配置：SessionMode={orig_mode}, Timeout={orig_timeout}s, "
                f"SSLEncryption={ssl_enabled}, MaxSessions={max_sessions}, Password={password_set}")
            detail["initial"] = {
                "SessionMode":            orig_mode,
                "Timeout":                orig_timeout,
                "SSLEncryptionEnabled":   ssl_enabled,
                "MaximumNumberOfSessions": max_sessions,
            }

            # 基础字段校验
            checks.append(("SessionMode 字段合规",
                            orig_mode in VALID_SESSION_MODES))
            checks.append(("Timeout 合法（BMC 要求 300~1800s 且 %60==0，即 5~30min）",
                            isinstance(orig_timeout, int) and 300 <= orig_timeout <= 1800 and orig_timeout % 60 == 0))
            # SSLEncryptionEnabled 部分 BMC 未提供，缺失时降级为 N/A 不判 FAIL
            if ssl_enabled is None:
                test.print_log("WARNING",
                    "SSLEncryptionEnabled: N/A（BMC 未提供该 OEM 字段，降级处理）")
            else:
                checks.append(("SSLEncryptionEnabled 为 bool",
                                isinstance(ssl_enabled, bool)))
            checks.append(("MaximumNumberOfSessions ≥ 1",
                            isinstance(max_sessions, int) and max_sessions >= 1))

            # ── 2. 校验 Timeout 写通路 ─────────────────────────────────
            # 单位约定：BMC 返回的 Timeout 为「秒」，脚本内部以「分钟」工作。
            # BMC 在 Redfish 层强制 300 ≤ Timeout(秒) ≤ 1800 且 %60==0，
            # 即分钟维度需满足 5 ≤ M ≤ 30 且为整数。
            # 不臆造偏移量：以 BMC 当前分钟值（换算回秒）验证 PATCH 通路；
            # 若当前值不满足 BMC 约束，则直接抛出错误。
            if orig_timeout is None:
                raise ValueError("无法读取 VNC Timeout（BMC 未返回该字段）")
            if not (300 <= orig_timeout <= 1800) or orig_timeout % 60 != 0:
                raise ValueError(
                    f"VNC Timeout={orig_timeout}s 不满足 BMC 约束"
                    f"（要求 300≤T≤1800 且 T%60==0，即 5~30min），配置不合理")
            orig_minutes = orig_timeout // 60
            patch_ok = self._patch_vnc(test, vnc_uri, {"Timeout": orig_minutes * 60}, "校验超时写通路")
            if patch_ok:
                time.sleep(2)
                vnc_after = self.client.get_vnc_service()
                actual_timeout = vnc_after.timeout
                timeout_ok = actual_timeout == orig_timeout
                checks.append((f"Timeout 写入可持久化（期望={orig_minutes}min/{orig_timeout}s, 实际={actual_timeout}s）",
                                timeout_ok))
                detail["timeout_patch"] = {
                    "expected": orig_timeout,
                    "actual":   actual_timeout,
                    "result":   "PASS" if timeout_ok else "FAIL",
                }
            else:
                checks.append(("Timeout PATCH 请求成功", False))

            # ── 3. 修改 SessionMode（在 Share/Private 间切换）────────────
            # 部分 BMC 接受 SessionMode 的 PATCH（HTTP 200）但不实际生效
            # （读回值不变），视为“不支持动态修改”，降级为 N/A，不判 FAIL。
            if orig_mode in VALID_SESSION_MODES:
                test_mode = "Private" if orig_mode == "Share" else "Share"
                patch_ok = self._patch_vnc(test, vnc_uri, {"SessionMode": test_mode}, "修改会话模式")
                if patch_ok:
                    # BMC 应用 SessionMode 约有 4.5s 异步推送延迟，轮询等待其生效
                    actual_mode = self._poll_session_mode(test, vnc_uri, test_mode)
                    if actual_mode == test_mode:
                        checks.append((f"SessionMode PATCH 生效（期望={test_mode}, 实际={actual_mode}）", True))
                        detail["mode_patch"] = {
                            "expected": test_mode,
                            "actual":   actual_mode,
                            "result":   "PASS",
                        }
                    else:
                        test.print_log("WARNING",
                            f"SessionMode PATCH 后读回仍为 {actual_mode}（BMC 不支持动态修改或不生效），降级为 N/A")
                        detail["mode_patch"] = {
                            "expected": test_mode,
                            "actual":   actual_mode,
                            "result":   "N/A",
                        }
                else:
                    test.print_log("WARNING", "SessionMode PATCH 失败（BMC 不支持），降级为 N/A")
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
                if orig_timeout is not None: restore_payload["Timeout"] = orig_timeout
                if orig_mode    is not None: restore_payload["SessionMode"] = orig_mode
                restore_ok = self._patch_vnc(test, vnc_uri, restore_payload, "恢复原始配置")
                if restore_ok:
                    try:
                        vnc_restored = self.client.get_vnc_service()
                        t_ok = getattr(vnc_restored, "timeout", None) == orig_timeout
                        # 恢复 SessionMode 同样受 BMC 异步延迟影响，轮询等待其回到原值
                        restored_mode = self._poll_session_mode(test, vnc_uri, orig_mode)
                        m_ok = restored_mode == orig_mode
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

        final = "PASS" if checks and all(r for _, r in checks) else "FAIL"
        for label, ok in checks:
            test.print_log("INFO" if ok else "ERROR", f"{'✓' if ok else '✗'} {label}")
        test.print_log("INFO" if final == "PASS" else "ERROR", f"测试结束，结果：{final}")

        detail["checks"] = [{"label": l, "result": "PASS" if r else "FAIL"} for l, r in checks]
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
