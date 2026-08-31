#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/bmc_stress_003_bmc_reset_stress.py -i <bmc_ip> -u <username> -p <password>
       可选：--duration <秒数>（默认 43200，即 12h）
Update:
2026/05/11: 新增，BMC 冷/热重启稳定性压测（12h）
2026/05/11: 重启方式改为 IPMI 定义冷热重启，Redfish 仅做恢复检测
2026/05/11: 修复 warm 重启后 IPMI 未稳定即发下一轮问题：warm 初始等待延长至30s；
            IPMI 通后额外二次确认（15s 后再验一次）；BUFFER_SEC 120s

压测策略：
  在 12h 内循环执行随机 BMC Reset（冷/热重启随机选择），
  每轮重启后分三阶段验证恢复情况，并记录恢复耗时。

  重启方式（随机选择，均通过 ipmitool 下发）：
    - 热重启（warm）：ipmitool bmc reset warm — BMC 软重启，保留运行状态
    - 冷重启（cold）：ipmitool bmc reset cold — BMC 硬重启，完全断电重启

  注意：Redfish Manager.Reset 实测两台 BMC（ZTE/Inspur）均只有 ForceRestart，
        无法区分冷热，因此改用 ipmitool 定义冷热重启语义。

  每轮恢复检测（三阶段，均从重启命令发出时开始计时）：
    1. BMC IP ping 通    → 超时 300s（5min）
       冷重启初始等待 10s，热重启初始等待 30s（warm 断线极短，避免误判）
    2. Redfish 接口恢复  → 超时 600s（10min）（SDK get_manufacturer()）
    3. IPMI 接口恢复     → 超时 600s（10min）（ipmitool mc info）
       第一次通后额外等 15s 做二次确认，确保 IPMI session 真正稳定

  重启间隔：
    每轮恢复完成后等待 BUFFER_SEC（120s），再执行下一轮，确保 BMC 充分冷却。

PASS 标准（冷热重启分别统计，两者标准相同）：
  - 所有轮次重启命令发送成功
  - 每轮均满足业务恢复时间要求：
      · BMC IP ping 通   ≤ 5min（300s）
      · Redfish 接口恢复 ≤ 10min（600s）
      · IPMI 接口恢复    ≤ 10min（600s）
  - 任意轮次恢复失败（超时 or 命令报错）→ FAIL

CSV：每轮一行，含重启类型、各阶段耗时、轮次结果
JSON detail.cycle：每轮详细记录；summary：整体 PASS/FAIL
"""

import os
import time
import random
import subprocess
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient

# ── 业务恢复时间要求 ──────────────────────────────────────────────────────────
PING_TIMEOUT_SEC    = 300    # BMC IP ping 通要求：≤ 5min
REDFISH_TIMEOUT_SEC = 600    # Redfish 接口恢复要求：≤ 10min
IPMI_TIMEOUT_SEC    = 600    # IPMI 接口恢复要求：≤ 10min
POLL_INTERVAL_SEC   = 10     # 轮询间隔（秒）

# ── 重启方式（IPMI 定义冷热重启）────────────────────────────────────────────
# Redfish Manager.Reset 两台 BMC 均只有 ForceRestart，无法区分冷热，改用 ipmitool
RESET_TYPES = [
    ("warm", "热重启"),   # ipmitool bmc reset warm — BMC 软重启
    ("cold", "冷重启"),   # ipmitool bmc reset cold — BMC 硬重启
]
BUFFER_SEC         = 120   # 每轮恢复后等待缓冲（秒），再发下一次 Reset
                            # 120s 确保 BMC（尤其浪潮）IPMI session 完全冷却
PING_SKIP_COLD_SEC = 10    # 冷重启：Reset 命令发出后先等待此时间再开始 ping
PING_SKIP_WARM_SEC = 30    # 热重启：BMC 断线极短，等 30s 再开始检测，避免误判"已通"
IPMI_STABLE_WAIT   = 15    # IPMI 第一次通后额外等待（秒），再做二次确认验证 session 稳定

# ── 压测参数 ──────────────────────────────────────────────────────────────────
DEFAULT_DURATION_SEC = 12 * 3600   # 12h

def _ping_once(bmc_ip: str) -> bool:
    """ping 一次 BMC IP，返回是否成功"""
    ret = subprocess.call(
        ["ping", "-c", "1", "-W", "2", bmc_ip],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return ret == 0

def _wait_ping(bmc_ip: str, test: CommonFunction,
               timeout: int, skip: int = PING_SKIP_COLD_SEC) -> tuple:
    """等待 BMC IP ping 通（从调用时开始计时），返回 (success, elapsed_s)"""
    test.print_log("INFO", f"等待 {skip}s 后开始 ping BMC（超时 {timeout}s）...")
    time.sleep(skip)
    start = time.time()
    while time.time() - start < timeout:
        if _ping_once(bmc_ip):
            elapsed = int(time.time() - start) + skip
            test.print_log("INFO", f"BMC IP ping 通，耗时 {elapsed}s（含初始等待）")
            return True, elapsed
        time.sleep(POLL_INTERVAL_SEC)
    elapsed = int(time.time() - start) + skip
    test.print_log("ERROR", f"BMC IP ping 超时（{elapsed}s，要求 ≤ {timeout + skip}s）")
    return False, elapsed

def _wait_redfish(bmc_ip: str, username: str, password: str,
                  test: CommonFunction, reset_start: float, timeout: int) -> tuple:
    """等待 Redfish 恢复（从 reset_start 开始计时），返回 (success, elapsed_s, manufacturer)"""
    test.print_log("INFO", f"等待 Redfish 接口恢复（超时 {timeout}s）...")
    while time.time() - reset_start < timeout:
        try:
            client = RedfishClient(host=bmc_ip, username=username, password=password)
            manufacturer = client.get_manufacturer()
            client.close()
            elapsed = int(time.time() - reset_start)
            test.print_log("INFO", f"Redfish 恢复，耗时 {elapsed}s，Manufacturer={manufacturer}")
            return True, elapsed, manufacturer
        except Exception:
            pass
        time.sleep(POLL_INTERVAL_SEC)
    elapsed = int(time.time() - reset_start)
    test.print_log("ERROR", f"Redfish 超时（{elapsed}s，要求 ≤ {timeout}s）")
    return False, elapsed, None

def _wait_ipmi(bmc_ip: str, username: str, password: str,
               test: CommonFunction, reset_start: float, timeout: int) -> tuple:
    """等待 IPMI mc info 恢复并二次确认 session 稳定（从 reset_start 开始计时），返回 (success, elapsed_s)"""
    test.print_log("INFO", f"等待 IPMI 接口恢复（超时 {timeout}s）...")

    def _run_mc_info() -> bool:
        try:
            result = subprocess.run(
                ["ipmitool", "-I", "lanplus",
                 "-H", bmc_ip, "-U", username, "-P", password, "mc", "info"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=15
            )
            return result.returncode == 0 and bool(result.stdout.strip())
        except Exception:
            return False

    # 第一次确认：IPMI mc info 成功
    while time.time() - reset_start < timeout:
        if _run_mc_info():
            first_ok_elapsed = int(time.time() - reset_start)
            test.print_log("INFO",
                f"IPMI mc info 第一次通，耗时 {first_ok_elapsed}s，"
                f"等待 {IPMI_STABLE_WAIT}s 后做二次确认...")
            time.sleep(IPMI_STABLE_WAIT)
            # 第二次确认：验证 session 真正稳定，非偶发通过
            if _run_mc_info():
                elapsed = int(time.time() - reset_start)
                test.print_log("INFO", f"IPMI session 稳定确认，总耗时 {elapsed}s")
                return True, elapsed
            else:
                test.print_log("WARNING",
                    f"IPMI 二次确认失败（session 尚不稳定），继续轮询...")
        time.sleep(POLL_INTERVAL_SEC)

    elapsed = int(time.time() - reset_start)
    test.print_log("ERROR", f"IPMI 超时（{elapsed}s，要求 ≤ {timeout}s）")
    return False, elapsed

class BmcStress003BmcResetStress(BmcTestBase):
    """BMC 冷/热重启稳定性压测（12h）"""

    TEST_CASE_KEY = "BmcStress003BmcResetStress"

    def __init__(self, case: str):
        self.DURATION_SEC: int = DEFAULT_DURATION_SEC   # 在 super().__init__ 前预声明
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/stress/bmc_stress_003_bmc_reset_stress.json"),
        )
        self.command_check_result = "FAIL"

    def _load_extra_config(self, conf_section: dict) -> None:
        if self.DURATION_SEC == DEFAULT_DURATION_SEC:
            self.DURATION_SEC = int(conf_section.get("DurationSeconds", DEFAULT_DURATION_SEC))

    def _do_reset(self, test: CommonFunction, reset_mode: str, reset_label: str) -> bool:
        """通过 ipmitool 发送 BMC Reset 命令（warm/cold），返回是否成功"""
        test.print_log("INFO",
            f"ipmitool bmc reset {reset_mode}（{reset_label}）")
        try:
            result = subprocess.run(
                ["ipmitool", "-I", "lanplus",
                 "-H", self.BMC_IP, "-U", self.USERNAME, "-P", self.PASSWORD,
                 "bmc", "reset", reset_mode],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30
            )
            if result.returncode == 0:
                test.print_log("INFO", f"BMC {reset_label}（{reset_mode}）命令发送成功")
                return True
            else:
                err = result.stderr.decode(errors="replace").strip()[:120]
                test.print_log("ERROR", f"BMC Reset 命令返回非零：{err}")
                return False
        except subprocess.TimeoutExpired:
            test.print_log("ERROR", "BMC Reset 命令超时（>30s）")
            return False
        except Exception as e:
            test.print_log("ERROR", f"BMC Reset 命令异常：{str(e)[:120]}")
            return False

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO",
            f"压测时长：{self.DURATION_SEC}s（{self.DURATION_SEC/3600:.1f}h），"
            f"恢复后等待缓冲：{BUFFER_SEC}s")
        test.print_log("INFO", "测试开始")

        start_ts   = time.time()
        round_num  = 0
        all_pass   = True

        # 统计（按 warm/cold 分别记录）
        stats = {
            "warm": {"label": "热重启", "total": 0, "pass": 0, "fail": 0,
                     "ping_s": [], "redfish_s": [], "ipmi_s": []},
            "cold": {"label": "冷重启", "total": 0, "pass": 0, "fail": 0,
                     "ping_s": [], "redfish_s": [], "ipmi_s": []},
        }

        while time.time() - start_ts < self.DURATION_SEC:
            round_num += 1
            reset_mode, reset_label = random.choice(RESET_TYPES)
            elapsed_total = time.time() - start_ts
            test.print_log("INFO", "=" * 60)
            test.print_log("INFO",
                f"[第 {round_num} 轮] {reset_label}（bmc reset {reset_mode}） "
                f"已运行={elapsed_total:.0f}s / {self.DURATION_SEC}s")

            round_result = {
                "round":       round_num,
                "reset_mode":  reset_mode,
                "reset_label": reset_label,
            }
            round_pass = True

            # ── 1. 发送 Reset 命令 ────────────────────────────────────────
            reset_ok = self._do_reset(test, reset_mode, reset_label)
            reset_start = time.time()   # 从发出 Reset 命令开始计时
            if not reset_ok:
                test.print_log("ERROR", f"[第 {round_num} 轮] Reset 命令失败，跳过本轮恢复检测")
                round_pass = False
                round_result.update({
                    "reset_ok": False,
                    "ping_ok": None, "ping_s": None,
                    "redfish_ok": None, "redfish_s": None,
                    "ipmi_ok": None, "ipmi_s": None,
                    "result": "FAIL",
                    "fail_reason": "reset_command_failed",
                })
                all_pass = False
                stats[reset_mode]["total"] += 1
                stats[reset_mode]["fail"]  += 1
                # JSON 只记录失败轮次
                test.add_key_value_to_json(
                    self.result_json_path, "detail.cycle", value=round_result)
                with open(self.result_csv_path, "a", encoding="utf-8") as f:
                    f.write(f"{round_num},{reset_mode}({reset_label}),,,,,,,FAIL\n")
                continue

            round_result["reset_ok"] = True

            # ── 2. 等待 ping 通（≤ 5min） ─────────────────────────────────
            # warm 重启 BMC 断线极短，需等更长时间再 ping，避免还未真正断线就判"已通"
            ping_skip = PING_SKIP_WARM_SEC if reset_mode == "warm" else PING_SKIP_COLD_SEC
            ping_ok, ping_elapsed = _wait_ping(
                self.BMC_IP, test,
                timeout=PING_TIMEOUT_SEC,
                skip=ping_skip
            )
            round_result["ping_ok"] = ping_ok
            round_result["ping_s"]  = ping_elapsed
            if not ping_ok:
                round_pass = False

            # ── 3. 等待 Redfish 恢复（≤ 10min，从 reset_start 计时） ──────
            redfish_ok, redfish_elapsed, manufacturer = _wait_redfish(
                self.BMC_IP, self.USERNAME, self.PASSWORD,
                test, reset_start, REDFISH_TIMEOUT_SEC
            )
            round_result["redfish_ok"]      = redfish_ok
            round_result["redfish_s"]        = redfish_elapsed
            round_result["manufacturer"]     = manufacturer
            if not redfish_ok:
                round_pass = False

            # ── 4. 等待 IPMI 恢复（≤ 10min，从 reset_start 计时） ──────────
            ipmi_ok, ipmi_elapsed = _wait_ipmi(
                self.BMC_IP, self.USERNAME, self.PASSWORD,
                test, reset_start, IPMI_TIMEOUT_SEC
            )
            round_result["ipmi_ok"] = ipmi_ok
            round_result["ipmi_s"]  = ipmi_elapsed
            if not ipmi_ok:
                round_pass = False

            # ── 5. 本轮汇总 ───────────────────────────────────────────────
            round_result["result"] = "PASS" if round_pass else "FAIL"
            if not round_pass:
                all_pass = False

            stats[reset_mode]["total"]   += 1
            stats[reset_mode]["pass" if round_pass else "fail"] += 1
            if ping_ok:     stats[reset_mode]["ping_s"].append(ping_elapsed)
            if redfish_ok:  stats[reset_mode]["redfish_s"].append(redfish_elapsed)
            if ipmi_ok:     stats[reset_mode]["ipmi_s"].append(ipmi_elapsed)

            level = "INFO" if round_pass else "ERROR"
            test.print_log(level,
                f"[第 {round_num} 轮] {reset_label}（{reset_mode}）→ {round_result['result']} | "
                f"ping={ping_elapsed}s redfish={redfish_elapsed}s ipmi={ipmi_elapsed}s")

            # JSON 只记录失败轮次，成功轮次仅写 CSV
            if not round_pass:
                fail_reasons = []
                if not ping_ok:    fail_reasons.append("ping_timeout")
                if not redfish_ok: fail_reasons.append("redfish_timeout")
                if not ipmi_ok:    fail_reasons.append("ipmi_timeout")
                round_result["fail_reason"] = ",".join(fail_reasons)
                test.add_key_value_to_json(
                    self.result_json_path, "detail.cycle", value=round_result)

            with open(self.result_csv_path, "a", encoding="utf-8") as f:
                f.write(f"{round_num},{reset_mode}({reset_label}),"
                        f"{ping_ok},{ping_elapsed},"
                        f"{redfish_ok},{redfish_elapsed},"
                        f"{ipmi_ok},{ipmi_elapsed},"
                        f"{round_result['result']}\n")

            # ── 6. 下一轮等待 ─────────────────────────────────────────────
            remain = self.DURATION_SEC - (time.time() - start_ts)
            if remain <= 0:
                test.print_log("INFO", "压测时间已到，结束压测")
                break
            wait: float = min(float(BUFFER_SEC), remain)
            test.print_log("INFO", f"等待 {wait:.0f}s 缓冲后执行下一轮...")
            time.sleep(wait)

        # ── 最终统计 ──────────────────────────────────────────────────────────
        total_elapsed = time.time() - start_ts
        test.print_log("INFO", "=" * 60)
        test.print_log("INFO",
            f"压测完成 | 实际时长={total_elapsed:.0f}s ({total_elapsed/3600:.2f}h) | 总轮次={round_num}")

        def _avg(lst): return round(sum(lst) / len(lst), 1) if lst else "-"
        def _max(lst): return max(lst) if lst else "-"

        for mode, s in stats.items():
            test.print_log("INFO",
                f"  {s['label']}（{mode}）: 总={s['total']} PASS={s['pass']} FAIL={s['fail']} | "
                f"ping avg={_avg(s['ping_s'])}s max={_max(s['ping_s'])}s | "
                f"redfish avg={_avg(s['redfish_s'])}s max={_max(s['redfish_s'])}s | "
                f"ipmi avg={_avg(s['ipmi_s'])}s max={_max(s['ipmi_s'])}s")

        final = "PASS" if all_pass else "FAIL"
        level = "INFO" if all_pass else "ERROR"
        test.print_log(level, f"测试结束，结果：{final}")

        # ── 写结果（JSON 只写聚合统计，不含原始列表）────────────────────────
        stats_summary = {}
        for mode, s in stats.items():
            stats_summary[mode] = {
                "label":        s["label"],
                "total":        s["total"],
                "pass":         s["pass"],
                "fail":         s["fail"],
                "ping_avg_s":   _avg(s["ping_s"]),
                "ping_max_s":   _max(s["ping_s"]),
                "redfish_avg_s": _avg(s["redfish_s"]),
                "redfish_max_s": _max(s["redfish_s"]),
                "ipmi_avg_s":   _avg(s["ipmi_s"]),
                "ipmi_max_s":   _max(s["ipmi_s"]),
            }
        summary_block = {
            "duration_s":   round(total_elapsed, 0),
            "total_rounds": round_num,
            "result":       final,
            "stats":        stats_summary,
        }
        test.add_key_value_to_json(
            self.result_json_path, "summary", value=summary_block)

        self.command_check_result = final

if __name__ == "__main__":
    exit_code = 0
    test_obj = None
    try:
        test_obj = BmcStress003BmcResetStress(
            BmcStress003BmcResetStress.TEST_CASE_KEY
        )
        test_obj.run_test()
        if test_obj.command_check_result == "PASS":
            exit_code = 0
        elif test_obj.command_check_result == "FAIL":
            exit_code = 2
        else:
            exit_code = 1
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "键盘中断，提前终止")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"未处理异常：{str(e)}")
        traceback.print_exc()
        exit_code = 1
    finally:
        try:
            if test_obj and hasattr(test_obj, "exit_code_path"):
                with open(test_obj.exit_code_path, "w", encoding="utf-8") as f:
                    f.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码失败：{str(e)}")
    sys.exit(exit_code)
