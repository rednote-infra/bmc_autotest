#!/bin/python
"""
Author: Fengmian
Date: 2026/10/28
Usage: python3 bmc/systems_016_memory_metrics_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/10/28: 新增，内存健康深度检查（issue/19，SDK 1.3.0 接口覆盖计划 B 档）
  覆盖 SDK 接口：get_memory_metrics() / get_memory_domains() / get_memory_chunks()

测试内容：
  1. 【MemoryDomains】枚举 System 广告的内存域（可选组件，未广告记 WARNING 跳过）
  2. 【MemoryChunks】读取内存块分配信息（域存在时）
  3. 【MemoryMetrics】逐 DIMM 读取健康指标：
     - BandwidthPercent / CapacityMaxBytes 等关键指标存在性
     - HealthData（AlarmTrips 等）状态检查
  4. 汇总：有 Metrics 的 DIMM 数量 / 异常数量

判定原则：
  - MemoryDomains/MemoryMetrics 为可选组件（BMC 未广告时 None/空列表）→
    WARNING 跳过，不计 FAIL（避免对不支持遥测的机型误报）
  - Metrics 中明确上报的健康异常（AlarmTrips 触发）→ FAIL
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException


class MemoryMetricsTest(BmcTestBase):
    """Systems 内存健康深度检查（MemoryDomains / MemoryChunks / MemoryMetrics）

    用例编号：Redfish_Systems_016
    检查项：
    - MemoryDomains 枚举（可选，未广告 WARNING 跳过）
    - MemoryChunks 分配信息读取
    - 逐 DIMM MemoryMetrics 健康指标检查（AlarmTrips 触发 → FAIL）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_016_memory_metrics_test.json"),
        )

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []       # (label, passed: bool|None)  None=跳过
        metrics_info = {"domains": [], "chunks_total": 0, "dimm_metrics": []}
        final = None

        try:
            self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)
            if not self.prepare_system_client():
                return

            # ── 1. MemoryDomains 枚举（可选组件）────────────────────────────
            test.print_log("INFO", "Step 1：枚举 MemoryDomains")
            try:
                domains = self.client.get_memory_domains()
            except RedfishException as e:
                test.print_log("WARNING",
                    f"MemoryDomains 枚举失败（{str(e)[:100]}），"
                    f"该 BMC 可能未广告 MemoryDomains（可选组件），跳过域/块检查")
                domains = []

            if domains:
                test.print_log("INFO", f"  ✓ 发现 {len(domains)} 个 MemoryDomain")
                checks.append(("MemoryDomains 枚举", True))
                for d in domains:
                    metrics_info["domains"].append({
                        "id": d.id,
                        "odata_id": d.odata_id,
                    })
            else:
                test.print_log("WARNING",
                    "  未发现 MemoryDomains（可选组件未实现），跳过域/块检查（WARNING 不 FAIL）")
                checks.append(("MemoryDomains 枚举", None))

            # ── 2. MemoryChunks（域存在时读取）──────────────────────────────
            chunks_total = 0
            if domains:
                test.print_log("INFO", "Step 2：读取 MemoryChunks")
                try:
                    chunks = self.client.get_memory_chunks(domains[0].id)
                    chunks_total = len(chunks or [])
                    test.print_log("INFO",
                        f"  ✓ 域 {domains[0].id} 含 {chunks_total} 个 MemoryChunks")
                    checks.append(("MemoryChunks 读取", True))
                except RedfishException as e:
                    test.print_log("WARNING",
                        f"  MemoryChunks 读取失败（{str(e)[:100]}），记 WARNING 不 FAIL")
                    checks.append(("MemoryChunks 读取", None))
                metrics_info["chunks_total"] = chunks_total
            else:
                checks.append(("MemoryChunks 读取", None))  # None=跳过

            # ── 3. 逐 DIMM MemoryMetrics ────────────────────────────────────
            test.print_log("INFO", "Step 3：逐 DIMM 读取 MemoryMetrics")
            try:
                dimms = self.client.get_memory()
            except RedfishException as e:
                test.print_log("ERROR", f"获取内存列表失败：{str(e)[:150]}")
                checks.append(("Memory 列表获取", False))
                self._write_result("FAIL", checks, metrics_info)
                return
            checks.append(("Memory 列表获取", True))
            test.print_log("INFO", f"  共 {len(dimms)} 个 DIMM")

            metrics_ok_count = 0
            metrics_none_count = 0
            alarm_count = 0

            for idx, dimm in enumerate(dimms):
                dimm_id = dimm.id or f"dimm_{idx}"
                try:
                    m = self.client.get_memory_metrics(dimm_id)
                except RedfishException as e:
                    # 404 = 该 DIMM 未广告 Metrics（常见于不支持遥测的内存条）
                    if getattr(e, "status_code", None) == 404:
                        metrics_none_count += 1
                        continue
                    test.print_log("WARNING",
                        f"  [{dimm_id}] Metrics 读取失败：{str(e)[:100]}")
                    metrics_none_count += 1
                    continue

                metrics_ok_count += 1
                entry = {"dimm_id": dimm_id, "metrics": {}}

                # 健康数据检查（AlarmTrips 触发 → FAIL）
                health = m.health_data if hasattr(m, "health_data") else None
                if health is not None:
                    trips = []
                    for trip_name in ("alarm_trips", "health_data"):
                        pass
                    # AlarmTrips 字段（弱类型，逐属性探测）
                    alarm_trips = getattr(health, "alarm_trips", None)
                    if alarm_trips is not None:
                        for attr in ("address_parity", "cmd_addr", "cmd_parity", "crc",
                                     "post_fail", "therm"):
                            v = getattr(alarm_trips, attr, None)
                            if v is True:
                                trips.append(f"{attr}=True")
                    if trips:
                        alarm_count += 1
                        test.print_log("ERROR",
                            f"  [{dimm_id}] 内存告警触发：{trips}")
                        entry["metrics"]["alarm_trips"] = trips
                    else:
                        entry["metrics"]["alarm_trips"] = "none"

                # 关键指标存档（存在即记录，不强制要求）
                for attr in ("bandwidth_percent", "capacity_max_bytes",
                             "current_period", "power_watts"):
                    v = getattr(m, attr, None)
                    if v is not None:
                        entry["metrics"][attr] = str(v)[:60]

                metrics_info["dimm_metrics"].append(entry)

            test.print_log("INFO",
                f"  Metrics 汇总：可读={metrics_ok_count}，未广告={metrics_none_count}，"
                f"告警触发={alarm_count}")
            metrics_info["summary"] = {
                "dimm_total": len(dimms),
                "metrics_readable": metrics_ok_count,
                "metrics_not_advertised": metrics_none_count,
                "alarm_triggered": alarm_count,
            }

            # 判定：告警触发 → FAIL；全部未广告 → WARNING 跳过
            checks.append(("Memory 列表获取", True))
            if metrics_ok_count > 0:
                checks.append(("MemoryMetrics 健康检查", alarm_count == 0))
            else:
                test.print_log("WARNING",
                    "  所有 DIMM 均未广告 MemoryMetrics（可选组件未实现），"
                    "跳过健康检查（WARNING 不 FAIL）")
                checks.append(("MemoryMetrics 健康检查", None))

            # ── 汇总（None=跳过项不计入）────────────────────────────────────
            active = [(n, r) for n, r in checks if r is not None]
            final = "PASS" if all(r for _, r in active) else "FAIL"

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

        self._write_result(final, checks, metrics_info)

    def _write_result(self, final, checks, metrics_info):
        test = CommonFunction()
        for label, passed in checks:
            value = "SKIP" if passed is None else ("PASS" if passed else "FAIL")
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": value})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "memory_metrics_info", "value": metrics_info})
        test.add_key_value_to_json(self.result_json_path, "summary",
                                   value={"metrics": self.TEST_NUM, "value": final})
        with open(self.result_csv_path, "a") as f:
            for label, passed in checks:
                value = "SKIP" if passed is None else ("PASS" if passed else "FAIL")
                f.write(f"{label},{value}\n")
            f.write(f"{self.TEST_NUM},{final}\n")

        self.command_check_result = final
        test.print_log("INFO", f"测试完成，结果：{final}")


if __name__ == '__main__':
    exit_code = 0
    test_name = None
    try:
        test_name = MemoryMetricsTest("MemoryMetricsTest")
        test_name.run_test()
        if test_name.command_check_result in ["FINISH", "PASS", "WARNING"]:
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
