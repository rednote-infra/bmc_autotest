#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/managers_023_sol_log_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/11: 新增，BMC SOL 串口日志采集测试
2026/05/11: 侧重 SOL 连接建立与日志收集，不做串口源配置切换

测试内容：
  1. GET SOLSourceControlInfo，验证 SOL 配置结构完整性
  2. 通过 ipmitool sol activate 建立 SOL 连接
  3. 采集 SOL_CAPTURE_SEC 秒日志输出（默认 30s）
  4. 验证日志有效性：连接成功 + 采集到非空输出
  5. deactivate 断开 SOL 连接

PASS 标准：
  - SOL 配置查询成功，SerialSource 字段存在
  - ipmitool sol activate 连接成功（无报错）
  - 采集到的 SOL 日志非空（有任何字符输出）→ 空输出视为 FAIL
  - ipmitool sol deactivate 正常断开

注意：
  SOL 采集用 subprocess 控制，采集 SOL_CAPTURE_SEC 秒后主动终止，属正常退出。
  若连接成功但日志为空，说明 SOL 数据路径异常或目标机串口未正确配置，判定 FAIL。
"""

import os
import time
import subprocess
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

SOL_INFO_URI     = "/redfish/v1/Managers/1/SOLSourceControlInfo"
SOL_CAPTURE_SEC  = 30    # SOL 日志采集时长（秒）
SOL_CMD_TIMEOUT  = 60    # ipmitool sol activate 总超时（秒，含采集时间 + 裕量）

class Managers023SolLogTest(BmcTestBase):
    """BMC SOL 串口日志采集测试"""

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/managers/managers_023_sol_log_test.json"),
        )
        self.command_check_result = "FAIL"
    def _ipmitool_base_cmd(self) -> list:
        return [
            "ipmitool", "-I", "lanplus",
            "-H", self.BMC_IP,
            "-U", self.USERNAME,
            "-P", self.PASSWORD,
        ]

    def _deactivate_sol(self, test: CommonFunction):
        """断开 SOL 连接（best-effort，失败不影响结果）"""
        try:
            result = subprocess.run(
                self._ipmitool_base_cmd() + ["sol", "deactivate"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15
            )
            if result.returncode == 0:
                test.print_log("INFO", "SOL deactivate 成功")
            else:
                stderr = result.stderr.decode(errors="replace").strip()
                test.print_log("WARNING", f"SOL deactivate 返回非0（可能已断开）：{stderr}")
        except Exception as e:
            test.print_log("WARNING", f"SOL deactivate 异常（忽略）：{e}")

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        detail = {}
        final  = "FAIL"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP, username=self.USERNAME, password=self.PASSWORD
            )

            # ── 1. 查询 SOL 配置 ─────────────────────────────────────────
            # [SDK-GAP] get_raw(SOL_INFO_URI) 获取 SOLSourceControlInfo 配置：
            #   SOLSourceControlInfo 为 OEM 扩展路径，SDK 无对应类型化接口，保留 get_raw
            test.print_log("WARNING", "[SDK-GAP] get_raw('/redfish/v1/Managers/1/SOLSourceControlInfo')："
                           "SOLSourceControlInfo 为 OEM 扩展路径，SDK 无对应类型化接口，待 SDK 补充后替换")
            sol_data = self.client.get_raw(SOL_INFO_URI)
            serial_source    = sol_data.get("SerialSource", "")
            parameters       = sol_data.get("Parameters", {})
            allowable_values = parameters.get("AllowableValues", []) \
                               if isinstance(parameters, dict) else []

            test.print_log("INFO",
                f"SOL 配置：SerialSource={serial_source}, AllowableValues={allowable_values}")
            detail["sol_config"] = {
                "SerialSource":    serial_source,
                "AllowableValues": allowable_values,
            }
            checks.append(("SOL 配置查询成功", True))
            checks.append(("SerialSource 字段存在", bool(serial_source)))

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish 查询 SOL 配置失败：{e}")
            checks.append(("SOL 配置查询成功", False))
        except Exception as e:
            test.print_log("ERROR", f"Redfish 异常：{e}")
            checks.append(("SOL 配置查询成功", False))
        finally:
            try:
                self.client.close()
            except Exception:
                pass

        # ── 2. ipmitool sol activate + 采集日志 ──────────────────────────
        # 先确保 SOL 没有残留连接
        self._deactivate_sol(test)
        time.sleep(2)

        test.print_log("INFO",
            f"建立 SOL 连接，采集 {SOL_CAPTURE_SEC}s 日志（超时 {SOL_CMD_TIMEOUT}s）...")
        sol_output = b""
        sol_connected = False

        try:
            proc = subprocess.Popen(
                self._ipmitool_base_cmd() + ["sol", "activate"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
            )
            start = time.time()
            while time.time() - start < SOL_CAPTURE_SEC:
                # 非阻塞读（500ms 超时）
                try:
                    chunk = proc.stdout.read(4096)
                    if chunk:
                        sol_output += chunk
                        if not sol_connected:
                            sol_connected = True
                            test.print_log("INFO", "SOL 连接已建立，开始接收日志")
                except Exception:
                    pass
                time.sleep(0.5)

            proc.terminate()
            try:
                remaining, _ = proc.communicate(timeout=5)
                if remaining:
                    sol_output += remaining
            except Exception:
                proc.kill()

        except FileNotFoundError:
            test.print_log("ERROR", "ipmitool 未安装或不在 PATH 中")
            checks.append(("SOL 连接建立成功", False))
        except Exception as e:
            test.print_log("ERROR", f"SOL 采集异常：{e}")
            traceback.print_exc()
            checks.append(("SOL 连接建立成功", False))
        else:
            # 判断连接是否成功（有任何输出，或进程启动未立即报错）
            stderr_hint = sol_output.decode(errors="replace")
            connect_failed = any(kw in stderr_hint.lower() for kw in [
                "error", "unable to", "connection refused", "session", "failed"
            ]) and not sol_connected

            checks.append(("SOL 连接建立成功", not connect_failed))

            sol_text = sol_output.decode(errors="replace")
            sol_bytes = len(sol_output)
            test.print_log("INFO", f"SOL 采集完成，输出 {sol_bytes} 字节")

            # 保存原始 SOL 日志
            with open(self.sol_log_path, "w", encoding="utf-8", errors="replace") as f:
                f.write(sol_text)
            test.print_log("INFO", f"SOL 日志已保存至 {self.sol_log_path}")

            if sol_bytes > 0:
                checks.append(("SOL 采集到有效日志输出（非空）", True))
            else:
                test.print_log("ERROR",
                    f"SOL 日志为空（连接 {SOL_CAPTURE_SEC}s 无任何输出），"
                    "可能 SOL 数据路径异常或目标机串口未正确配置")
                checks.append(("SOL 采集到有效日志输出（非空）", False))

            detail["sol_capture"] = {
                "capture_sec":  SOL_CAPTURE_SEC,
                "output_bytes": sol_bytes,
                "log_path":     self.sol_log_path,
                "result":       "PASS" if sol_bytes > 0 else "FAIL",
            }

        # ── 3. 断开 SOL 连接 ──────────────────────────────────────────────
        self._deactivate_sol(test)

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
        obj = Managers023SolLogTest("Managers023SolLogTest")
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
