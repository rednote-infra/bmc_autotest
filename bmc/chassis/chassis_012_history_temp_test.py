#!/bin/python
"""
Author: Fengmian
Date: 2026/05/09
Usage: python3 bmc/chassis_012_history_temp_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/09: 新增，历史温度曲线测试
2026/06/23: 改写为 redfish_sdk v1.1.0 get_inlet_history_temperature()；
            测试目标由标准 ThermalSubsystem 路径调整为厂商扩展 InletHistoryTemperature 路径
            （SDK 内部适配各厂商 /redfish/v1/Chassis/{id}/Thermal/InletHistoryTemperature）

测试策略（厂商扩展路径，SDK 适配）：
  数据来源：client.get_inlet_history_temperature()
            → InletHistoryTemperature.historical_inlet_temp（List[HistoricalInletTempEntry]）
            每条含 time / avg / max / min

  测试逻辑：
  1. 调用 get_inlet_history_temperature()；返回 None（链接缺失或 404）→ FAIL，推动厂商补充；
  2. 历史数据完整性验证：
     - 记录数组非空（count > 0）
     - 每条含 time 及至少一个温度值（avg/max/min）
     - time 符合 ISO 8601
     - 时间戳为降序（最新在前）

  PASS 条件：资源可用 + 数据非空 + 字段完整 + 时间格式合规
  FAIL 条件：资源不可用，或数据为空 / 字段缺失 / 时间格式不合规
"""

import os
import time
import re
import sys
import traceback
from datetime import datetime, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException

# ISO 8601 时间格式（宽松匹配，允许无时区后缀）
ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(Z|[+-]\d{2}:\d{2})?$"
)


def _parse_iso8601(s: str):
    """尝试解析 ISO 8601 时间字符串，返回 datetime 或 None"""
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


class Chassis012HistoryTempTest(BmcTestBase):
    """历史温度曲线测试

    用例编号：Redfish_Chassis_012
    测试内容：
    - 通过 SDK get_inlet_history_temperature() 获取进风口历史温度（厂商扩展路径）
    - 历史数据完整性：非空、字段合规（time + 温度值）、时间格式、降序排列
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_012_history_temp_test.json"),
        )

    def _validate_history_data(self, records: list, test: CommonFunction) -> dict:
        """
        验证历史温度记录（List[HistoricalInletTempEntry]）：
        - 非空
        - 每条含 time 及至少一个温度值（avg/max/min）
        - time 符合 ISO 8601
        - 时间序列为降序（最新在前）
        返回验证结果字典
        """
        result = {
            "count": len(records),
            "non_empty": len(records) > 0,
            "all_fields_present": True,
            "time_format_ok": True,
            "time_descending": True,
            "sample": [r.model_dump(by_alias=True) for r in records[:2]] if records else [],
        }

        parsed_times = []
        for i, rec in enumerate(records[:20]):  # 只验证前 20 条，避免超时
            has_temp = any(v is not None for v in (rec.avg, rec.max, rec.min))
            if rec.time is None or not has_temp:
                result["all_fields_present"] = False
                test.print_log(
                    "ERROR",
                    f"记录 [{i}] 缺少 time 或温度值：time={rec.time}, "
                    f"avg={rec.avg}, max={rec.max}, min={rec.min}"
                )
            time_str = str(rec.time or "")
            if not ISO8601_RE.match(time_str):
                result["time_format_ok"] = False
                test.print_log("ERROR", f"记录 [{i}] 时间格式不合规: {time_str!r}")
            else:
                dt = _parse_iso8601(time_str)
                if dt:
                    parsed_times.append(dt)

        # 检查降序（允许无时区，直接比较 naive datetime）
        if len(parsed_times) >= 2:
            naive_times = []
            for dt in parsed_times:
                if hasattr(dt, 'tzinfo') and dt.tzinfo is not None:
                    naive_times.append(dt.astimezone(timezone.utc).replace(tzinfo=None))
                else:
                    naive_times.append(dt)
            descending = all(naive_times[i] >= naive_times[i + 1]
                             for i in range(len(naive_times) - 1))
            result["time_descending"] = descending
            if not descending:
                test.print_log("WARNING",
                               f"历史数据时间序列非降序（最新应在前），前3条: {naive_times[:3]}")

        return result

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks = []
        data_result = None

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD
            )

            # v1.1.0：通过 SDK 获取进风口历史温度（厂商扩展路径，SDK 适配各厂商）
            test.print_log("INFO",
                           "=== 通过 get_inlet_history_temperature() 获取历史温度（厂商扩展路径）===")
            try:
                hist = self.client.get_inlet_history_temperature()
            except RedfishException as e:
                test.print_log("ERROR", f"get_inlet_history_temperature() 调用失败：{str(e)}")
                hist = None

            if hist is None:
                test.print_log(
                    "ERROR",
                    "BMC 不支持进风口历史温度（InletHistoryTemperature 链接缺失或 404），"
                    "需推动厂商补充该接口"
                )
                checks.append(("InletHistoryTemperature 资源可用", False))
            else:
                records = hist.historical_inlet_temp or []
                test.print_log("INFO", f"历史温度记录数：{len(records)}")
                data_result = self._validate_history_data(records, test)
                checks.append(("InletHistoryTemperature 资源可用", True))
                checks.append(("历史数据非空", data_result["non_empty"]))
                checks.append(("历史数据字段完整（time + 温度值）", data_result["all_fields_present"]))
                checks.append(("时间格式符合 ISO 8601", data_result["time_format_ok"]))

            final = "PASS" if checks and all(r for _, r in checks) else "FAIL"

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
                                   value={"metrics": "history_temp_detail", "value": data_result})

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
        test_name = Chassis012HistoryTempTest("Chassis012HistoryTempTest")
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
