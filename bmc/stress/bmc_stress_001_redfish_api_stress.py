#!/bin/python
"""
Author: Fengmian
Date: 2026/05/11
Usage: python3 bmc/bmc_stress_001_redfish_api_stress.py -i <bmc_ip> -u <username> -p <password>
       可选：--duration <秒数>（默认 43200，即 12h）
Update:
2026/05/11: 新增，Redfish API 稳定性压测（12h）
2026/05/11: 更新 PASS 标准 - 失败率 = 0%（任意失败即终止）+ 平均延时 ≤ 2s

压测策略：
  轮询若干轻量 GET 接口，记录每次请求耗时与成功/失败状态。
  压测期间每小时打印一次中间统计，结束时输出全量统计并判断 PASS/FAIL。

  压测接口列表（均为只读 GET，不影响 BMC 状态）：
    - GET /redfish/v1/Systems/1       → 系统基本信息
    - GET /redfish/v1/Managers/1      → 管理器基本信息
    - GET /redfish/v1/Chassis/1       → 机箱基本信息
    - GET /redfish/v1/Chassis/1/Thermal → 温度/风扇信息
    - GET /redfish/v1/Chassis/1/Power  → 电源信息

PASS 标准：
  - 连接失败次数 = 0（12h 内统计连接失败次数和失败率，失败次数 > 0 即 FAIL，不提前终止）
  - 单次请求超时次数 = 0（单次响应时间 > 2s 计为超时，统计超时率；超时次数 > 0 即 FAIL）

CSV：整体统计结果一行
JSON detail.cycle：每小时中间统计 + 全量统计；summary：整体 PASS/FAIL
"""

import argparse
import os
import time
import sys
import traceback
import statistics

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

# ── 压测目标接口 ──────────────────────────────────────────────────────────────
STRESS_URIS = [
    "/redfish/v1/Systems/1",
    "/redfish/v1/Managers/1",
    "/redfish/v1/Chassis/1",
    "/redfish/v1/Chassis/1/Thermal",
    "/redfish/v1/Chassis/1/Power",
]

# ── PASS 标准 ────────────────────────────────────────────────────────────────
MAX_FAIL_COUNT    = 0     # 连接失败次数必须为 0（失败次数 > 0 即 FAIL，但不提前终止）
LATENCY_THRESHOLD = 2.0   # 单次请求超时阈值（秒），超过即计入超时次数

# ── 压测参数 ─────────────────────────────────────────────────────────────────
DEFAULT_DURATION_SEC = 12 * 3600   # 12h
INTERVAL_SEC         = 1.0         # 两次请求间最小间隔（秒）
REPORT_INTERVAL_SEC  = 3600        # 每小时中间统计

def _percentile(data: list, p: float) -> float:
    """计算百分位数（p 为 0~100）"""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    idx = min(idx, len(sorted_data) - 1)
    return sorted_data[idx]

def _calc_stats(latencies: list) -> dict:
    """计算请求耗时统计"""
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

class BmcStress001RedfishApiStress(BmcTestBase):
    """Redfish API 稳定性压测（12h）

    用例编号：Redfish_BmcStress_001
    测试内容：
    - 对 5 个只读 GET 接口轮流发起请求
    - 统计成功/失败次数、响应时间分布（P50/P95/P99/Max/avg）
    - 任意请求失败立即终止并记录 FAIL
    - 满 12h 无失败后判断平均延时
    """

    def __init__(self, case: str):
        self.DURATION: int = DEFAULT_DURATION_SEC   # 默认值，_add_args/_load_extra_config 中可覆盖
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/stress/bmc_stress_001_redfish_api_stress.json"),
        )

    def _add_args(self) -> None:
        """覆盖基类，增加 --duration 参数。"""
        parser = argparse.ArgumentParser(description="Redfish API 稳定性压测")
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
        # 仅当未从命令行显式指定时，从配置文件读取（否则保留命令行值）
        if self.DURATION == DEFAULT_DURATION_SEC:
            self.DURATION = int(conf_section.get("DurationSeconds", DEFAULT_DURATION_SEC))

    def run(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", f"压测时长：{self.DURATION}s（{self.DURATION/3600:.1f}h），"
                                f"接口数：{len(STRESS_URIS)}，间隔：{INTERVAL_SEC}s")
        test.print_log("INFO", "测试开始")

        # ── 统计变量 ──────────────────────────────────────────────────────────
        total_req     = 0
        total_ok      = 0
        total_fail    = 0
        total_timeout = 0              # 单次延时 > LATENCY_THRESHOLD 的次数
        latencies_all = []             # 全量成功请求耗时
        per_uri_stats = {u: {"ok": 0, "fail": 0, "timeout": 0, "latencies": []} for u in STRESS_URIS}
        hour_records  = []             # 每小时中间统计
        final         = "FAIL"

        start_ts       = time.time()
        last_report_ts = start_ts
        hour_num       = 0

        try:
            test.print_log("INFO", f"正在连接 BMC {self.BMC_IP} ...")
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD,
            )
            test.print_log("INFO", "BMC 连接成功，开始压测...")

            uri_idx = 0
            while True:
                elapsed = time.time() - start_ts
                if elapsed >= self.DURATION:
                    test.print_log("INFO", f"压测时间已到（{elapsed:.0f}s），结束压测")
                    break

                # ── 每小时中间统计 ─────────────────────────────────────────
                if time.time() - last_report_ts >= REPORT_INTERVAL_SEC:
                    hour_num += 1
                    cur_stats    = _calc_stats(latencies_all)
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
                        f"超时(>{LATENCY_THRESHOLD}s)={total_timeout}({cur_timeout_rate*100:.3f}%) "
                        f"avg={cur_stats['avg']}s P99={cur_stats['p99']}s Max={cur_stats['max']}s")
                    hour_records.append(hour_stat)
                    test.add_key_value_to_json(
                        self.result_json_path, "detail.cycle",
                        value={"type": "hourly", "stats": hour_stat}
                    )
                    last_report_ts = time.time()

                # ── 发起一次请求 ──────────────────────────────────────────
                uri = STRESS_URIS[uri_idx % len(STRESS_URIS)]
                uri_idx += 1
                req_start = time.time()
                try:
                    self.client.get_raw(uri)
                    latency = time.time() - req_start
                    total_req += 1
                    total_ok  += 1
                    latencies_all.append(latency)
                    per_uri_stats[uri]["ok"] += 1
                    per_uri_stats[uri]["latencies"].append(latency)
                    # 超时统计（连接成功但响应慢）
                    if latency > LATENCY_THRESHOLD:
                        total_timeout += 1
                        per_uri_stats[uri]["timeout"] += 1
                        test.print_log("WARNING",
                            f"单次超时 [{uri}] latency={latency:.3f}s > {LATENCY_THRESHOLD}s"
                            f"（累计超时={total_timeout}）")
                except Exception as e:
                    latency = time.time() - req_start
                    total_req  += 1
                    total_fail += 1
                    per_uri_stats[uri]["fail"] += 1
                    test.print_log("WARNING",
                        f"请求失败 [{uri}] 耗时={latency:.3f}s 累计失败={total_fail} err={str(e)[:120]}")

                # ── 控制速率 ──────────────────────────────────────────────
                elapsed_req = time.time() - req_start
                sleep_time  = max(0.0, INTERVAL_SEC - elapsed_req)
                if sleep_time > 0:
                    time.sleep(sleep_time)

        except Exception as e:
            test.print_log("ERROR", f"压测异常终止：{e}")
            test.print_log("ERROR", traceback.format_exc())
        finally:
            if self.client:
                try:
                    self.client.close()
                except Exception:
                    pass

        # ── 最终统计与判断 ────────────────────────────────────────────────────
        total_elapsed = time.time() - start_ts
        stats_all     = _calc_stats(latencies_all)
        fail_rate     = round(total_fail / total_req, 6) if total_req else 0
        timeout_rate  = round(total_timeout / total_req, 6) if total_req else 0

        test.print_log("INFO", "=" * 60)
        test.print_log("INFO", f"压测完成 | 实际时长={total_elapsed:.0f}s ({total_elapsed/3600:.2f}h)")
        test.print_log("INFO",
            f"总请求={total_req} | 成功={total_ok} | "
            f"连接失败={total_fail}({fail_rate*100:.4f}%)")
        test.print_log("INFO",
            f"单次超时(>{LATENCY_THRESHOLD}s)={total_timeout} "
            f"超时率={timeout_rate*100:.4f}%")
        test.print_log("INFO", f"avg={stats_all['avg']}s P50={stats_all['p50']}s "
                               f"P95={stats_all['p95']}s P99={stats_all['p99']}s Max={stats_all['max']}s")

        # ── 逐条打印每个 URI 统计 ──────────────────────────────────────────
        uri_summary = []
        for uri, s in per_uri_stats.items():
            uri_stat = _calc_stats(s["latencies"])
            test.print_log("INFO",
                f"  {uri}: ok={s['ok']} fail={s['fail']} timeout={s['timeout']} "
                f"avg={uri_stat['avg']}s P99={uri_stat['p99']}s max={uri_stat['max']}s")
            uri_summary.append({
                "uri": uri, "ok": s["ok"], "fail": s["fail"], "timeout": s["timeout"],
                "total": s["ok"] + s["fail"], "latency": uri_stat,
            })

        # ── PASS/FAIL 判断 ─────────────────────────────────────────────────
        checks = []
        checks.append(("连接失败次数 = 0",
                        total_fail == MAX_FAIL_COUNT))
        checks.append((f"单次超时次数 = 0（每次响应 ≤ {LATENCY_THRESHOLD}s）",
                        total_timeout == 0))

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
            "latency_threshold_s": LATENCY_THRESHOLD,
            "latency_all":         stats_all,
            "per_uri":         uri_summary,
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
        BmcStress001RedfishApiStress("BmcStress001RedfishApiStress").run()
        exit_code = 0
    except Exception as e:
        CommonFunction.print_log("ERROR", f"脚本异常：{e}")
        CommonFunction.print_log("ERROR", traceback.format_exc())
        exit_code = 2
    sys.exit(exit_code)
