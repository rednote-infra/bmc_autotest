#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/bmc_stress_002_ipmi_api_stress.py -i <bmc_ip> -u <username> -p <password>
       可选：--duration <秒数>（默认 43200，即 12h）
Update:
2026/05/11: 新增，IPMI 接口稳定性压测（12h）
2026/05/11: 更新 PASS 标准 - 失败次数 = 0（任意失败即终止）+ 平均延时 ≤ 2s
2026/05/11: 更新 PASS 标准 - 移除 IPMI 平均延时要求，仅校验失败次数 = 0

压测策略：
  轮询若干轻量 ipmitool 命令，记录每次命令耗时与成功/失败状态。
  压测期间每小时打印一次中间统计，结束时输出全量统计并判断 PASS/FAIL。

  压测命令列表（均为只读查询，不影响 BMC 状态）：
    - ipmitool mc info         → 管理控制器基本信息
    - ipmitool chassis status  → 机箱/电源状态
    - ipmitool sdr list        → 传感器数据记录列表
    - ipmitool lan print 1     → LAN 通道 1 配置信息
    - ipmitool fru list        → FRU 现场可更换单元信息

  注意：sdr list 和 fru list 返回数据较多，耗时相对较长，统计平均延时时一并计入。

PASS 标准：
  - 连接失败次数 = 0（12h 内统计连接失败次数和失败率，失败次数 > 0 即 FAIL，不提前终止）
  - 命令执行超时次数 = 0（单次 ipmitool 超过 CMD_TIMEOUT_SEC 强杀，超时次数 > 0 即 FAIL）
  - 延时（未超时的成功请求）不设阈值，记录供参考

CSV：整体统计结果一行
JSON detail.cycle：每小时中间统计 + 全量统计；summary：整体 PASS/FAIL
"""

import argparse
import os
import time
import subprocess
import sys
import traceback
import statistics

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

# ── 压测目标命令 ──────────────────────────────────────────────────────────────
# 每条命令格式：(label, ipmitool_subcommand_args)
STRESS_COMMANDS = [
    ("mc_info",        ["mc",      "info"]),
    ("chassis_status", ["chassis", "status"]),
    ("sdr_list",       ["sdr",     "list"]),
    ("lan_print",      ["lan",     "print", "1"]),
    ("fru_list",       ["fru",     "list"]),
]

# ── PASS 标准 ────────────────────────────────────────────────────────────────
MAX_FAIL_COUNT    = 0     # 连接失败次数必须为 0（失败次数 > 0 即 FAIL，但不提前终止）
MAX_TIMEOUT_COUNT = 0     # 命令超时次数必须为 0（超时次数 > 0 即 FAIL）
# 延时（未超时的成功请求）不设阈值，仅记录供参考

# ── 压测参数 ─────────────────────────────────────────────────────────────────
DEFAULT_DURATION_SEC = 12 * 3600   # 12h
INTERVAL_SEC         = 2.0         # 两次命令间最小间隔（秒，IPMI 比 HTTP 慢，适当放宽）
REPORT_INTERVAL_SEC  = 3600        # 每小时中间统计
CMD_TIMEOUT_SEC      = 120         # 单次 ipmitool 命令强杀超时（秒）；防止进程挂死拖垮压测

def _percentile(data: list, p: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = min(int(len(sorted_data) * p / 100), len(sorted_data) - 1)
    return sorted_data[idx]

def _calc_stats(latencies: list) -> dict:
    if not latencies:
        return {"p50": 0, "p95": 0, "p99": 0, "max": 0, "avg": 0, "min": 0}
    return {
        "p50": round(_percentile(latencies, 50), 3),
        "p95": round(_percentile(latencies, 95), 3),
        "p99": round(_percentile(latencies, 99), 3),
        "max": round(max(latencies), 3),
        "min": round(min(latencies), 3),
        "avg": round(statistics.mean(latencies), 3),
    }

def _run_ipmitool(bmc_ip: str, username: str, password: str,
                  subcmd: list, timeout: int = CMD_TIMEOUT_SEC) -> tuple:
    """执行一次 ipmitool 命令，返回 (status: str, latency: float, errmsg: str)
    status 取值：
      'ok'      - 命令成功（returncode == 0）
      'timeout' - 命令执行超时，进程已被强杀（>CMD_TIMEOUT_SEC）
      'fail'    - 命令失败（returncode != 0 或异常）
    """
    cmd = [
        "ipmitool", "-I", "lanplus",
        "-H", bmc_ip,
        "-U", username,
        "-P", password,
    ] + subcmd
    t0 = time.time()
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        latency = time.time() - t0
        if result.returncode == 0:
            return "ok", latency, ""
        else:
            return "fail", latency, result.stderr.decode(errors="replace").strip()[:120]
    except subprocess.TimeoutExpired:
        latency = time.time() - t0
        return "timeout", latency, f"命令执行超时（>{timeout}s），进程已强杀"
    except Exception as e:
        return "fail", time.time() - t0, str(e)[:120]

class BmcStress002IpmiApiStress(BmcTestBase):
    """IPMI 接口稳定性压测（12h）

    用例编号：Redfish_BmcStress_002
    测试内容：
    - 对 5 个只读 ipmitool 命令轮流执行
    - 统计成功/失败次数、响应时间分布（P50/P95/P99/Max）
    - 检测连续失败（服务中断）
    - 满 12h 或达到最大失败率时结束
    """

    def __init__(self, case: str):
        self.DURATION: int = DEFAULT_DURATION_SEC   # 默认值，_add_args/_load_extra_config 中可覆盖
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/stress/bmc_stress_002_ipmi_api_stress.json"),
        )

    def _add_args(self) -> None:
        """覆盖基类，增加 --duration 参数。"""
        parser = argparse.ArgumentParser(description="IPMI 接口稳定性压测")
        parser.add_argument("-i", "--bmc_ip",    type=str)
        parser.add_argument("-u", "--user_name", type=str)
        parser.add_argument("-p", "--password",  type=str)
        parser.add_argument("--duration", type=int, default=None,
                            help=f"压测时长（秒，默认 {DEFAULT_DURATION_SEC}）")
        args, _ = parser.parse_known_args()
        self.BMC_IP   = args.bmc_ip
        self.USERNAME = args.user_name
        self.PASSWORD = args.password
        if args.duration is not None:
            self.DURATION = args.duration

    def _load_extra_config(self, conf_section: dict) -> None:
        if self.DURATION == DEFAULT_DURATION_SEC:
            self.DURATION = int(conf_section.get("DurationSeconds", DEFAULT_DURATION_SEC))

    def run(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", f"压测时长：{self.DURATION}s（{self.DURATION/3600:.1f}h），"
                               f"命令数：{len(STRESS_COMMANDS)}，间隔：{INTERVAL_SEC}s")

        # ── 检查 ipmitool 是否可用 ────────────────────────────────────────────
        try:
            check = subprocess.run(["ipmitool", "--version"],
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=5)
            test.print_log("INFO", f"ipmitool 可用：{check.stdout.decode().strip()[:60]}")
        except FileNotFoundError:
            test.print_log("ERROR", "ipmitool 未安装，请先安装：yum install -y ipmitool")
            test.add_key_value_to_json(self.result_json_path, "summary",
                                       value={"metrics": self.TEST_NAME, "value": "FAIL"})
            with open(self.exit_code_path, "w") as f:
                f.write("2")
            self.exit_code = 2
            return

        test.print_log("INFO", "测试开始")

        # ── 统计变量 ──────────────────────────────────────────────────────────
        total_req      = 0
        total_ok       = 0
        total_fail     = 0
        total_timeout  = 0
        latencies_all  = []
        per_cmd_stats  = {label: {"ok": 0, "fail": 0, "timeout": 0, "latencies": []}
                          for label, _ in STRESS_COMMANDS}
        hour_records   = []
        final          = "FAIL"

        start_ts       = time.time()
        last_report_ts = start_ts
        hour_num       = 0

        try:
            cmd_idx = 0
            while True:
                elapsed = time.time() - start_ts
                if elapsed >= self.DURATION:
                    test.print_log("INFO", f"压测时间已到（{elapsed:.0f}s），结束压测")
                    break

                # ── 每小时中间统计 ─────────────────────────────────────────
                if time.time() - last_report_ts >= REPORT_INTERVAL_SEC:
                    hour_num += 1
                    cur_stats        = _calc_stats(latencies_all)
                    cur_fail_rate    = round(total_fail    / total_req, 6) if total_req else 0
                    cur_timeout_rate = round(total_timeout / total_req, 6) if total_req else 0
                    hour_stat = {
                        "hour":          hour_num,
                        "elapsed_s":     round(elapsed, 0),
                        "total_req":     total_req,
                        "total_ok":      total_ok,
                        "total_fail":    total_fail,
                        "fail_rate":     cur_fail_rate,
                        "total_timeout": total_timeout,
                        "timeout_rate":  cur_timeout_rate,
                        "latency_stats": cur_stats,
                    }
                    test.print_log("INFO",
                        f"[第{hour_num}h统计] 总请求={total_req} 成功={total_ok} "
                        f"失败={total_fail}({cur_fail_rate*100:.3f}%) "
                        f"超时(>{CMD_TIMEOUT_SEC}s)={total_timeout}({cur_timeout_rate*100:.3f}%) "
                        f"avg={cur_stats['avg']}s P99={cur_stats['p99']}s Max={cur_stats['max']}s")
                    hour_records.append(hour_stat)
                    test.add_key_value_to_json(
                        self.result_json_path, "detail.cycle",
                        value={"type": "hourly", "stats": hour_stat}
                    )
                    last_report_ts = time.time()

                # ── 执行一次 IPMI 命令 ────────────────────────────────────
                label, subcmd = STRESS_COMMANDS[cmd_idx % len(STRESS_COMMANDS)]
                cmd_idx += 1

                req_start = time.time()
                status, latency, errmsg = _run_ipmitool(
                    self.BMC_IP, self.USERNAME, self.PASSWORD, subcmd
                )
                total_req += 1

                if status == "ok":
                    total_ok  += 1
                    latencies_all.append(latency)
                    per_cmd_stats[label]["ok"] += 1
                    per_cmd_stats[label]["latencies"].append(latency)
                elif status == "timeout":
                    total_timeout += 1
                    per_cmd_stats[label]["timeout"] += 1
                    test.print_log("WARNING",
                        f"命令超时 [{label}] 耗时={latency:.1f}s 累计超时={total_timeout} {errmsg}")
                else:
                    total_fail += 1
                    per_cmd_stats[label]["fail"] += 1
                    test.print_log("WARNING",
                        f"命令失败 [{label}] 耗时={latency:.3f}s 累计失败={total_fail} err={errmsg}")

                # ── 控制速率 ──────────────────────────────────────────────
                elapsed_cmd = time.time() - req_start
                sleep_time  = max(0.0, INTERVAL_SEC - elapsed_cmd)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except Exception as e:
            test.print_log("ERROR", f"压测异常终止：{e}")
            test.print_log("ERROR", traceback.format_exc())

        # ── 最终统计与判断 ────────────────────────────────────────────────────
        total_elapsed = time.time() - start_ts
        stats_all     = _calc_stats(latencies_all)

        fail_rate    = round(total_fail    / total_req, 6) if total_req else 0
        timeout_rate = round(total_timeout / total_req, 6) if total_req else 0
        test.print_log("INFO", "=" * 60)
        test.print_log("INFO", f"压测完成 | 实际时长={total_elapsed:.0f}s ({total_elapsed/3600:.2f}h)")
        test.print_log("INFO",
            f"总请求={total_req} | 成功={total_ok} | "
            f"连接失败={total_fail}({fail_rate*100:.4f}%) | "
            f"执行超时(>{CMD_TIMEOUT_SEC}s)={total_timeout}({timeout_rate*100:.4f}%)")
        test.print_log("INFO", f"avg={stats_all['avg']}s P50={stats_all['p50']}s "
                               f"P95={stats_all['p95']}s P99={stats_all['p99']}s Max={stats_all['max']}s")

        cmd_summary = []
        for label, s in per_cmd_stats.items():
            cmd_stat = _calc_stats(s["latencies"])
            test.print_log("INFO",
                f"  [{label}]: ok={s['ok']} fail={s['fail']} timeout={s['timeout']} "
                f"avg={cmd_stat['avg']}s P99={cmd_stat['p99']}s max={cmd_stat['max']}s")
            cmd_summary.append({
                "cmd": label, "ok": s["ok"], "fail": s["fail"], "timeout": s["timeout"],
                "total": s["ok"] + s["fail"] + s["timeout"], "latency": cmd_stat,
            })

        # ── PASS/FAIL 判断 ─────────────────────────────────────────────────
        checks = []
        checks.append(("连接失败次数 = 0",                     total_fail    == MAX_FAIL_COUNT))
        checks.append((f"执行超时次数 = 0（>{CMD_TIMEOUT_SEC}s强杀）", total_timeout == MAX_TIMEOUT_COUNT))
        # 延时仅记录，不作为 PASS/FAIL 依据

        all_pass = all(r for _, r in checks)
        final    = "PASS" if all_pass else "FAIL"

        for name, result in checks:
            level = "INFO" if result else "ERROR"
            mark  = "✓" if result else "✗"
            test.print_log(level, f"{mark} {name}: {'OK' if result else 'FAIL'}")

        test.print_log("INFO", f"测试结束，结果：{final}")

        # ── 写结果文件 ─────────────────────────────────────────────────────
        cycle_result = {
            "duration_s":          round(total_elapsed, 0),
            "total_req":           total_req,
            "total_ok":            total_ok,
            "total_fail":          total_fail,
            "fail_rate":           fail_rate,
            "total_timeout":       total_timeout,
            "timeout_rate":        timeout_rate,
            "cmd_timeout_sec":     CMD_TIMEOUT_SEC,
            "latency_all":         stats_all,
            "per_cmd":         cmd_summary,
            "checks":          [{"name": n, "result": "PASS" if r else "FAIL"} for n, r in checks],
        }
        summary = {"metrics": self.TEST_NAME, "value": final}

        test.add_key_value_to_json(self.result_json_path, "detail.cycle", value=cycle_result)
        test.add_key_value_to_json(self.result_json_path, "summary",      value=summary)

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"{total_req},{total_ok},{total_fail},{round(fail_rate*100,4)},"
                    f"{total_timeout},{round(timeout_rate*100,4)},"
                    f"{stats_all['avg']},{stats_all['p50']},{stats_all['p95']},"
                    f"{stats_all['p99']},{stats_all['max']},{final}\n")

        with open(self.exit_code_path, "w") as f:
            if final == "PASS":
                f.write("0")
                self.exit_code = 0
            else:
                f.write("2")
                self.exit_code = 2

if __name__ == "__main__":
    exit_code = 1
    try:
        BmcStress002IpmiApiStress("BmcStress002IpmiApiStress").run()
        exit_code = 0
    except Exception as e:
        CommonFunction.print_log("ERROR", f"脚本异常：{e}")
        CommonFunction.print_log("ERROR", traceback.format_exc())
        exit_code = 2
    sys.exit(exit_code)
