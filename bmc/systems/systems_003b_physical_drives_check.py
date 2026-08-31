#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/systems_003b_physical_drives_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增
2026/06/23: 改写为 redfish_sdk v1.1.0 get_drive(odata_id)（取代 get_raw + Drive.model_validate）

数据来源（SAS 卡管理的 HDD，非 Chassis/Drives NVMe/SSD）：
  get_storages() → List[Storage]
  每个 Storage.drives: List[Link]  ← @odata.id 链接，非完整对象
  对每条 Link 用 get_drive(odata_id) 获取 Drive 对象（v1.1.0）

校验策略（与 chassis_001_drives_check 一致）：
  必要字段（FAIL + ERROR）：
    Model、Revision、CapacityBytes（> 0）、Protocol、SerialNumber、Manufacturer、
    CapableSpeedGbs、NegotiatedSpeedGbs（必须 == CapableSpeedGbs）、Status.Health
  非必要字段（WARNING）：
    MediaType、HotspareType、FailurePredicted、PredictedMediaLifeLeftPercent、
    StatusIndicator、Location

CSV：每行一块物理盘
JSON detail.cycle：每块盘一条记录；summary：整体 PASS/FAIL
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401
from redfish_sdk.models.drive import Drive

VALID_HEALTH = {"OK", "Warning", "Critical"}
VALID_STATUS_INDICATOR = {
    "OK", "Fail", "Rebuild", "PredictiveFailureAnalysis",
    "Hotspare", "InACriticalArray", "InAFailedArray"
}

class PhysicalDrivesInfoCheck(BmcTestBase):
    """Systems Storage 物理盘信息检查（SAS 卡管理的 HDD）

    用例编号：Redfish_Systems_003b
    检查项：通过 Storage.drives Link 获取每块物理盘，逐一校验关键字段
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_003b_physical_drives_check.json"),
        )
    def _init_sdk_client(self):
        CommonFunction.print_log("DEBUG", "开始初始化 Redfish SDK 客户端")
        self.client = RedfishClient(
            host=self.BMC_IP,
            username=self.USERNAME,
            password=self.PASSWORD,
        )
        CommonFunction.print_log("DEBUG", "Redfish SDK 客户端初始化完成")

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
                CommonFunction.print_log("DEBUG", "Redfish SDK 客户端已关闭")
            except Exception as e:
                CommonFunction.print_log("WARNING", f"关闭 SDK 客户端时出错: {str(e)}")

    # ── 单块物理盘校验 ────────────────────────────────────────────────────────

    def _check_drive(self, drive: Drive, storage_id: str) -> tuple:
        drive_id = drive.id or drive.serial_number or "unknown"
        label = f"{storage_id}/{drive_id}"
        all_pass = True

        # ══ 必要字段 ══

        # 1. Manufacturer
        if not drive.manufacturer or (isinstance(drive.manufacturer, str) and drive.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {label}：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：Manufacturer = {drive.manufacturer}")

        # 2. Model
        if not drive.model or (isinstance(drive.model, str) and drive.model.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {label}：Model 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：Model = {drive.model}")

        # 3. Revision
        if not drive.revision or (isinstance(drive.revision, str) and drive.revision.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {label}：Revision 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：Revision = {drive.revision}")

        # 4. SerialNumber
        if not drive.serial_number or (isinstance(drive.serial_number, str) and drive.serial_number.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {label}：SerialNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：SerialNumber = {drive.serial_number}")

        # 5. CapacityBytes > 0
        if drive.capacity_bytes is None:
            CommonFunction.print_log("ERROR", f"[Drive] {label}：CapacityBytes 为 None")
            all_pass = False
        elif not isinstance(drive.capacity_bytes, int) or drive.capacity_bytes <= 0:
            CommonFunction.print_log("ERROR", f"[Drive] {label}：CapacityBytes = {drive.capacity_bytes}，应为正整数")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：CapacityBytes = {drive.capacity_bytes}")

        # 6. Protocol
        if not drive.protocol or (isinstance(drive.protocol, str) and drive.protocol.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {label}：Protocol 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：Protocol = {drive.protocol}")

        # 7. CapableSpeedGbs
        if drive.capable_speed_gbs is None:
            CommonFunction.print_log("ERROR", f"[Drive] {label}：CapableSpeedGbs 为 None")
            all_pass = False
        elif not isinstance(drive.capable_speed_gbs, (int, float)) or drive.capable_speed_gbs <= 0:
            CommonFunction.print_log("ERROR", f"[Drive] {label}：CapableSpeedGbs = {drive.capable_speed_gbs}，应为正数")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：CapableSpeedGbs = {drive.capable_speed_gbs} Gbps")

        # 8. NegotiatedSpeedGbs 必须严格等于 CapableSpeedGbs
        if drive.negotiated_speed_gbs is None:
            CommonFunction.print_log("ERROR", f"[Drive] {label}：NegotiatedSpeedGbs 为 None")
            all_pass = False
        elif not isinstance(drive.negotiated_speed_gbs, (int, float)) or drive.negotiated_speed_gbs <= 0:
            CommonFunction.print_log("ERROR", f"[Drive] {label}：NegotiatedSpeedGbs = {drive.negotiated_speed_gbs}，应为正数")
            all_pass = False
        elif drive.capable_speed_gbs and drive.negotiated_speed_gbs != drive.capable_speed_gbs:
            CommonFunction.print_log(
                "ERROR",
                f"[Drive] {label}：NegotiatedSpeedGbs({drive.negotiated_speed_gbs}) != "
                f"CapableSpeedGbs({drive.capable_speed_gbs})，链路降速，疑似故障"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：NegotiatedSpeedGbs = {drive.negotiated_speed_gbs} Gbps（与 CapableSpeedGbs 一致）")

        # 9. Status.Health 必须为 "OK"
        health = drive.status.health if drive.status else None
        if health is None:
            CommonFunction.print_log("ERROR", f"[Drive] {label}：Status.Health 为 None")
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log(
                "ERROR",
                f"[Drive] {label}：Status.Health = '{health}'，不在允许值 {VALID_HEALTH} 内"
            )
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log("ERROR", f"[Drive] {label}：Status.Health = '{health}'，硬盘健康状态异常")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：Status.Health = {health}，健康正常")

        # ══ 非必要字段 ══

        # 10. MediaType
        if not drive.media_type or (isinstance(drive.media_type, str) and drive.media_type.strip() == ""):
            CommonFunction.print_log("WARNING", f"[Drive] {label}：MediaType 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：MediaType = {drive.media_type}")

        # 11. HotspareType
        if not drive.hotspare_type or (isinstance(drive.hotspare_type, str) and drive.hotspare_type.strip() == ""):
            CommonFunction.print_log("WARNING", f"[Drive] {label}：HotspareType 为空或 None")
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：HotspareType = {drive.hotspare_type}")

        # 12. FailurePredicted
        if drive.failure_predicted is None:
            CommonFunction.print_log("WARNING", f"[Drive] {label}：FailurePredicted 为 None")
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：FailurePredicted = {drive.failure_predicted}")

        # 13. PredictedMediaLifeLeftPercent
        if drive.predicted_media_life_left_percent is None:
            CommonFunction.print_log("WARNING", f"[Drive] {label}：PredictedMediaLifeLeftPercent 为 None")
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：PredictedMediaLifeLeftPercent = {drive.predicted_media_life_left_percent}%")

        # 14. StatusIndicator（枚举白名单）
        if not drive.status_indicator or (isinstance(drive.status_indicator, str) and drive.status_indicator.strip() == ""):
            CommonFunction.print_log("WARNING", f"[Drive] {label}：StatusIndicator 为空或 None")
        elif drive.status_indicator not in VALID_STATUS_INDICATOR:
            CommonFunction.print_log(
                "WARNING",
                f"[Drive] {label}：StatusIndicator = '{drive.status_indicator}'，不在白名单 {VALID_STATUS_INDICATOR} 内"
            )
        else:
            CommonFunction.print_log("INFO", f"[Drive] {label}：StatusIndicator = {drive.status_indicator}")

        # 15. Location
        if not drive.location:
            CommonFunction.print_log("WARNING", f"[Drive] {label}：Location 为空或 None")
        else:
            for loc in drive.location:
                CommonFunction.print_log("INFO", f"[Drive] {label}：Location.Info = {loc.info}")

        drive_info = {
            "storage_id":         storage_id,
            "drive_id":           drive_id,
            "manufacturer":       drive.manufacturer or "",
            "model":              drive.model or "",
            "revision":           drive.revision or "",
            "serial_number":      drive.serial_number or "",
            "capacity_bytes":     drive.capacity_bytes,
            "protocol":           drive.protocol or "",
            "capable_speed_gbs":  drive.capable_speed_gbs,
            "negotiated_speed_gbs": drive.negotiated_speed_gbs,
            "health":             health or "",
            "check_result":       "PASS" if all_pass else "FAIL",
        }
        return all_pass, drive_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def physical_drives_info_check(self) -> tuple:
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 Storage 数据（SAS 物理盘）")
        try:
            storage_list = self.client.get_storages()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Storage 数据失败：{str(e)}")
            return "FAIL", []

        if not storage_list:
            CommonFunction.print_log("ERROR", "Storage 列表为空，检查失败")
            return "FAIL", []

        all_results = []
        drive_info_list = []

        for storage in storage_list:
            storage_id = storage.id or "unknown"
            drive_links = storage.drives or []
            if not drive_links:
                CommonFunction.print_log("WARNING", f"[Storage] {storage_id}：Drives 列表为空，跳过")
                continue

            CommonFunction.print_log("INFO", f"[Storage] {storage_id}：发现 {len(drive_links)} 条 Drive 链接，开始逐一获取")
            for link in drive_links:
                odata_id = link.odata_id
                if not odata_id:
                    CommonFunction.print_log("WARNING", f"[Storage] {storage_id}：某条 Drive Link 的 @odata.id 为空，跳过")
                    continue
                try:
                    # v1.1.0：get_drive(odata_id) 直接返回 Drive 对象
                    # （取代 get_raw + Drive.model_validate）
                    drive = self.client.get_drive(odata_id)
                except RedfishException as e:
                    CommonFunction.print_log("ERROR", f"[Storage] {storage_id}：获取 Drive {odata_id} 失败：{str(e)}")
                    all_results.append(False)
                    continue
                except Exception as e:
                    CommonFunction.print_log("ERROR", f"[Storage] {storage_id}：解析 Drive {odata_id} 失败：{str(e)}")
                    all_results.append(False)
                    continue

                passed, drive_info = self._check_drive(drive, storage_id)
                all_results.append(passed)
                drive_info_list.append(drive_info)

        if not all_results:
            CommonFunction.print_log("ERROR", "未找到任何物理盘，检查失败")
            return "FAIL", []

        final = "PASS" if all(all_results) else "FAIL"
        failed_cnt = sum(1 for r in all_results if not r)
        if final == "PASS":
            CommonFunction.print_log("INFO", f"物理盘信息检查全部通过，共 {len(all_results)} 块")
        else:
            CommonFunction.print_log(
                "ERROR",
                f"物理盘信息检查失败，{failed_cnt}/{len(all_results)} 块盘存在必要字段异常"
            )
        return final, drive_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, drive_info_list = self.physical_drives_info_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in drive_info_list:
                f.write(
                    f'"{info["storage_id"]}",'
                    f'"{info["drive_id"]}",'
                    f'"{info["manufacturer"]}",'
                    f'"{info["model"]}",'
                    f'"{info["revision"]}",'
                    f'"{info["serial_number"]}",'
                    f'{info["capacity_bytes"]},'
                    f'"{info["protocol"]}",'
                    f'{info["capable_speed_gbs"]},'
                    f'{info["negotiated_speed_gbs"]},'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        for info in drive_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'Drive_{info["storage_id"]}_{info["drive_id"]}', "value": info}
            )

        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result}
        )
        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{self.command_check_result}")

# ── 主程序 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = PhysicalDrivesInfoCheck("PhysicalDrivesInfoCheck")
        checker.run_test()
        if checker.command_check_result == "PASS":
            exit_code = 0
        elif checker.command_check_result == "FAIL":
            exit_code = 2
        else:
            exit_code = 1
        start_time = time.time()
        while (time.time() - start_time) < 5:
            time.sleep(1)
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "检测到键盘中断(Ctrl+C)，提前终止程序")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"发生未处理异常: {str(e)}")
        traceback.print_exc()
        exit_code = 1
    finally:
        try:
            checker.close_sdk_client()
        except Exception:
            pass
        try:
            with open(checker.exit_code_path, "w", encoding="utf-8") as f:
                f.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
