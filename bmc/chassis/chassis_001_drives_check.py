#!/bin/python
"""
Author: Fengmian
Date: 2026/04/21
Usage: python3 bmc/chassis_001_drives_check.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/21: 新增
2026/04/21: 补全缺失字段，区分必要/非必要字段校验策略
2026/04/21: 改用 redfish-python-sdk 重构，通过 RedfishClient.get_drives() 获取磁盘资源
2026/04/21: CSV/JSON 备份每块盘的信息；非必要字段异常只 WARNING 不计入整体结果

校验策略：
  必要字段（为空/异常 → FAIL + ERROR，计入整体结果）：
    Model、Revision、CapacityBytes、Protocol、SerialNumber、Manufacturer、
    Status.Health、CapableSpeedGbs、NegotiatedSpeedGbs
  非必要字段（为空或异常 → WARNING 告警，不计入整体结果）：
    MediaType、HotspareType、FailurePredicted、PredictedMediaLifeLeftPercent、
    StatusIndicator、Location、Status.State
  INFO — 打印 Model / Revision / SerialNumber / Manufacturer / Location 供人工存档
"""

import os
import time
import sys
import traceback
from pydantic import ValidationError

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

# ── 枚举白名单 ──────────────────────────────────────────────────────────────
VALID_MEDIA_TYPE       = {"SSD", "HDD", "SMR"}
VALID_PROTOCOL         = {"NVMe", "SAS", "SATA", "AHCI", "PCIe", "FC", "FCoE", "NVMeOverFabrics", "iSCSI"}
VALID_HEALTH           = {"OK", "Warning", "Critical"}
VALID_STATE            = {"Enabled", "Disabled", "Absent", "UnavailableOffline", "Deferring",
                          "Quiesced", "Updating", "Starting"}
VALID_HOTSPARE_TYPE    = {"None", "Global", "Chassis", "Dedicated"}
VALID_STATUS_INDICATOR = {"OK", "Fail", "Rebuild", "PredictiveFailureAnalysis",
                           "Hotspare", "InACriticalArray", "InAFailedArray"}

class DrivesInfoCheck(BmcTestBase):
    """Chassis Drives 集合资源信息检查

    用例编号：Redfish_Chassis_001
    检查项：
      1. Drives 集合非空（数量 > 0）
      2. 每块盘的字段校验（必要字段 / 非必要字段分层）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_001_drives_check.json"),
        )
    def _load_extra_config(self, conf_section: dict) -> None:
        """加载测试配置"""
    # ── 日志系统初始化 ───────────────────────────────────────────────────────

    def _init_sdk_client(self):
        """初始化 redfish-python-sdk 客户端，整个脚本生命周期内复用同一连接"""
        CommonFunction.print_log("DEBUG", "开始初始化 Redfish SDK 客户端")
        self.client = RedfishClient(
            host=self.BMC_IP,
            username=self.USERNAME,
            password=self.PASSWORD,
        )
        CommonFunction.print_log("DEBUG", "Redfish SDK 客户端初始化完成")

    def close_sdk_client(self):
        """关闭 SDK 客户端连接"""
        if self.client:
            try:
                self.client.close()
                CommonFunction.print_log("DEBUG", "Redfish SDK 客户端已关闭")
            except Exception as e:
                CommonFunction.print_log("WARNING", f"关闭 SDK 客户端时出错: {str(e)}")

    # ── 单块盘校验 ───────────────────────────────────────────────────────────

    def _check_single_drive(self, drive) -> tuple:
        """对单块 Drive 执行分层校验

        :param drive: SDK 返回的 Drive 对象
        :return: (all_pass: bool, drive_info: dict)
                 all_pass   — 仅由必要字段决定
                 drive_info — 盘的关键信息，供 CSV / JSON 备份
        """
        drive_name = drive.name or drive.odata_id or "unknown"
        all_pass = True

        # ══════════════════════════════════════════════════════════════
        # 必要字段校验（FAIL + ERROR，计入整体结果）
        # ══════════════════════════════════════════════════════════════

        # ── 1. Model ────────────────────────────────────────────────────────
        if not drive.model or (isinstance(drive.model, str) and drive.model.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {drive_name}：Model 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：Model = {drive.model}")

        # ── 2. Revision ─────────────────────────────────────────────────────
        if not drive.revision or (isinstance(drive.revision, str) and drive.revision.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {drive_name}：Revision 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：Revision = {drive.revision}")

        # ── 3. SerialNumber ─────────────────────────────────────────────────
        if not drive.serial_number or (isinstance(drive.serial_number, str) and drive.serial_number.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {drive_name}：SerialNumber 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：SerialNumber = {drive.serial_number}")

        # ── 4. Manufacturer ─────────────────────────────────────────────────
        if not drive.manufacturer or (isinstance(drive.manufacturer, str) and drive.manufacturer.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {drive_name}：Manufacturer 为空或 None")
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：Manufacturer = {drive.manufacturer}")

        # ── 5. CapacityBytes > 0 ────────────────────────────────────────────
        if drive.capacity_bytes is None:
            CommonFunction.print_log("ERROR", f"[Drive] {drive_name}：CapacityBytes 为 None")
            all_pass = False
        elif not isinstance(drive.capacity_bytes, int) or drive.capacity_bytes <= 0:
            CommonFunction.print_log(
                "ERROR",
                f"[Drive] {drive_name}：CapacityBytes = {drive.capacity_bytes}，应为正整数"
            )
            all_pass = False
        else:
            capacity_gb = round(drive.capacity_bytes / (1024 ** 3), 1)
            CommonFunction.print_log(
                "INFO",
                f"[Drive] {drive_name}：CapacityBytes = {drive.capacity_bytes} ({capacity_gb} GB)"
            )

        # ── 6. Protocol（枚举白名单）────────────────────────────────────────
        if not drive.protocol or (isinstance(drive.protocol, str) and drive.protocol.strip() == ""):
            CommonFunction.print_log("ERROR", f"[Drive] {drive_name}：Protocol 为空或 None")
            all_pass = False
        elif drive.protocol not in VALID_PROTOCOL:
            CommonFunction.print_log(
                "ERROR",
                f"[Drive] {drive_name}：Protocol = '{drive.protocol}'，不在允许值 {VALID_PROTOCOL} 内"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：Protocol = {drive.protocol}，合法")

        # ── 7. Status.Health 必须为 "OK" ────────────────────────────────────
        health = drive.status.health if drive.status else None
        if health is None:
            CommonFunction.print_log("ERROR", f"[Drive] {drive_name}：Status.Health 为 None")
            all_pass = False
        elif health not in VALID_HEALTH:
            CommonFunction.print_log(
                "ERROR",
                f"[Drive] {drive_name}：Status.Health = '{health}'，不在允许值 {VALID_HEALTH} 内"
            )
            all_pass = False
        elif health != "OK":
            CommonFunction.print_log(
                "ERROR",
                f"[Drive] {drive_name}：Status.Health = '{health}'，磁盘健康状态异常"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：Status.Health = {health}，健康正常")

        # ── 8. CapableSpeedGbs > 0 ──────────────────────────────────────────
        capable_speed = drive.capable_speed_gbs
        if capable_speed is None:
            CommonFunction.print_log("ERROR", f"[Drive] {drive_name}：CapableSpeedGbs 为 None")
            all_pass = False
        elif not isinstance(capable_speed, (int, float)) or capable_speed <= 0:
            CommonFunction.print_log(
                "ERROR",
                f"[Drive] {drive_name}：CapableSpeedGbs = {capable_speed}，应为正数"
            )
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：CapableSpeedGbs = {capable_speed} Gbs")

        # ── 9. NegotiatedSpeedGbs > 0 且必须 == CapableSpeedGbs ─────────────
        negotiated_speed = drive.negotiated_speed_gbs
        if negotiated_speed is None:
            CommonFunction.print_log("ERROR", f"[Drive] {drive_name}：NegotiatedSpeedGbs 为 None")
            all_pass = False
        elif not isinstance(negotiated_speed, (int, float)) or negotiated_speed <= 0:
            CommonFunction.print_log(
                "ERROR",
                f"[Drive] {drive_name}：NegotiatedSpeedGbs = {negotiated_speed}，应为正数"
            )
            all_pass = False
        elif capable_speed is not None and isinstance(capable_speed, (int, float)) and capable_speed > 0:
            if negotiated_speed != capable_speed:
                CommonFunction.print_log(
                    "ERROR",
                    f"[Drive] {drive_name}：NegotiatedSpeedGbs = {negotiated_speed} != "
                    f"CapableSpeedGbs = {capable_speed}，链路降速，存在故障风险"
                )
                all_pass = False
            else:
                CommonFunction.print_log(
                    "INFO",
                    f"[Drive] {drive_name}：NegotiatedSpeedGbs = {negotiated_speed} Gbs，与 CapableSpeedGbs 一致"
                )

        # ══════════════════════════════════════════════════════════════
        # 非必要字段校验（为空或异常 → WARNING 告警，不计入整体结果）
        # ══════════════════════════════════════════════════════════════

        # ── 10. MediaType（枚举白名单）──────────────────────────────────────
        if not drive.media_type or (isinstance(drive.media_type, str) and drive.media_type.strip() == ""):
            CommonFunction.print_log("WARNING", f"[Drive] {drive_name}：MediaType 为空或 None")
        elif drive.media_type not in VALID_MEDIA_TYPE:
            CommonFunction.print_log(
                "WARNING",
                f"[Drive] {drive_name}：MediaType = '{drive.media_type}'，不在允许值 {VALID_MEDIA_TYPE} 内"
            )
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：MediaType = {drive.media_type}，合法")

        # ── 11. HotspareType（枚举白名单）───────────────────────────────────
        if not drive.hotspare_type or (isinstance(drive.hotspare_type, str) and drive.hotspare_type.strip() == ""):
            CommonFunction.print_log("WARNING", f"[Drive] {drive_name}：HotspareType 为空或 None")
        elif drive.hotspare_type not in VALID_HOTSPARE_TYPE:
            CommonFunction.print_log(
                "WARNING",
                f"[Drive] {drive_name}：HotspareType = '{drive.hotspare_type}'，不在允许值 {VALID_HOTSPARE_TYPE} 内"
            )
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：HotspareType = {drive.hotspare_type}")

        # ── 12. FailurePredicted 必须为 False ───────────────────────────────
        if drive.failure_predicted is None:
            CommonFunction.print_log("WARNING", f"[Drive] {drive_name}：FailurePredicted 为 None")
        elif drive.failure_predicted is True:
            CommonFunction.print_log(
                "WARNING",
                f"[Drive] {drive_name}：FailurePredicted = True，磁盘存在故障预测，请立即排查"
            )
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：FailurePredicted = False，无故障预测")

        # ── 13. PredictedMediaLifeLeftPercent 在 0~100 ──────────────────────
        life_left = drive.predicted_media_life_left_percent
        if life_left is None:
            CommonFunction.print_log("WARNING", f"[Drive] {drive_name}：PredictedMediaLifeLeftPercent 为 None")
        elif not isinstance(life_left, (int, float)) or not (0 <= life_left <= 100):
            CommonFunction.print_log(
                "WARNING",
                f"[Drive] {drive_name}：PredictedMediaLifeLeftPercent = {life_left}，应在 0~100 范围内"
            )
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：PredictedMediaLifeLeftPercent = {life_left}%")

        # ── 14. StatusIndicator（枚举白名单）────────────────────────────────
        if not drive.status_indicator or (isinstance(drive.status_indicator, str) and drive.status_indicator.strip() == ""):
            CommonFunction.print_log("WARNING", f"[Drive] {drive_name}：StatusIndicator 为空或 None")
        elif drive.status_indicator not in VALID_STATUS_INDICATOR:
            CommonFunction.print_log(
                "WARNING",
                f"[Drive] {drive_name}：StatusIndicator = '{drive.status_indicator}'，"
                f"不在允许值 {VALID_STATUS_INDICATOR} 内"
            )
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：StatusIndicator = {drive.status_indicator}")

        # ── 15. Location（列表非空，打印 Info 存档）─────────────────────────
        if drive.location is None:
            CommonFunction.print_log("WARNING", f"[Drive] {drive_name}：Location 为 None")
        elif not isinstance(drive.location, list) or len(drive.location) == 0:
            CommonFunction.print_log("WARNING", f"[Drive] {drive_name}：Location 为空列表")
        else:
            for loc_item in drive.location:
                loc_info = loc_item.info if hasattr(loc_item, "info") else None
                if loc_info:
                    CommonFunction.print_log("INFO", f"[Drive] {drive_name}：Location.Info = {loc_info}")
                else:
                    CommonFunction.print_log("WARNING", f"[Drive] {drive_name}：Location 条目中 Info 字段为空")

        # ── 16. Status.State（枚举白名单）───────────────────────────────────
        state = drive.status.state if drive.status else None
        if not state or (isinstance(state, str) and state.strip() == ""):
            CommonFunction.print_log("WARNING", f"[Drive] {drive_name}：Status.State 为空或 None")
        elif state not in VALID_STATE:
            CommonFunction.print_log(
                "WARNING",
                f"[Drive] {drive_name}：Status.State = '{state}'，不在允许值 {VALID_STATE} 内"
            )
        else:
            CommonFunction.print_log("INFO", f"[Drive] {drive_name}：Status.State = {state}")

        # ── 整理盘信息字典（供 CSV / JSON 备份）────────────────────────────
        drive_info = {
            "name":                drive_name,
            "manufacturer":        drive.manufacturer or "",
            "model":               drive.model or "",
            "revision":            drive.revision or "",
            "serial_number":       drive.serial_number or "",
            "protocol":            drive.protocol or "",
            "capacity_bytes":      drive.capacity_bytes,
            "capable_speed_gbs":   capable_speed,
            "negotiated_speed_gbs": negotiated_speed,
            "health":              health or "",
            "check_result":        "PASS" if all_pass else "FAIL",
        }

        return all_pass, drive_info

    # ── 主检查流程 ───────────────────────────────────────────────────────────

    def drives_info_check(self) -> tuple:
        """执行 Drives 集合资源信息检查

        :return: (final_result: str, drive_info_list: list)
        """
        CommonFunction.print_log("INFO", "开始通过 SDK 获取 Chassis Drives 集合资源")

        try:
            drive_list = self.client.get_drives()
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"获取 Drives 集合失败（Redfish 接口异常）：{str(e)}")
            return "FAIL", []
        except ValidationError as e:
            # BMC 端 Bug：返回数据不符合 Redfish 规范（如 Id 为整数而非字符串），
            # SDK Pydantic 模型校验失败，无法解析响应，如实记录 FAIL。
            CommonFunction.print_log(
                "ERROR",
                f"获取 Drives 集合失败（BMC 返回数据不符合 Redfish 规范，SDK 解析错误）：{str(e)}"
            )
            return "FAIL", []
        except Exception as e:
            CommonFunction.print_log(
                "ERROR",
                f"获取 Drives 集合时发生未预期异常：{type(e).__name__}: {str(e)}"
            )
            return "FAIL", []

        if not drive_list:
            CommonFunction.print_log("ERROR", "Drives 集合为空（磁盘数量为 0），检查失败")
            return "FAIL", []

        CommonFunction.print_log("INFO", f"共获取到 {len(drive_list)} 块磁盘，开始逐盘校验")

        results = []
        drive_info_list = []
        for drive in drive_list:
            passed, drive_info = self._check_single_drive(drive)
            results.append(passed)
            drive_info_list.append(drive_info)

        final = "PASS" if all(results) else "FAIL"
        failed_cnt = sum(1 for r in results if not r)
        if final == "PASS":
            CommonFunction.print_log(
                "INFO", f"Drives 集合信息检查全部通过，共 {len(drive_list)} 块磁盘"
            )
        else:
            CommonFunction.print_log(
                "ERROR",
                f"Drives 集合信息检查失败，{failed_cnt}/{len(drive_list)} 块磁盘必要字段存在异常"
            )
        return final, drive_info_list

    # ── 测试入口 ─────────────────────────────────────────────────────────────

    def run_test(self):
        """调用 SDK 执行 Drives 信息检查"""
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        drive_check_result, drive_info_list = self.drives_info_check()
        self.command_check_result = drive_check_result

        # CSV：每行记录一块盘的关键信息
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for info in drive_info_list:
                f.write(
                    f'"{info["name"]}",'
                    f'"{info["manufacturer"]}",'
                    f'"{info["model"]}",'
                    f'"{info["revision"]}",'
                    f'"{info["serial_number"]}",'
                    f'"{info["protocol"]}",'
                    f'{info["capacity_bytes"]},'
                    f'{info["capable_speed_gbs"]},'
                    f'{info["negotiated_speed_gbs"]},'
                    f'"{info["health"]}",'
                    f'{info["check_result"]}\n'
                )

        # JSON detail.cycle：每块盘一条记录
        for info in drive_info_list:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": info["name"], "value": info}
            )

        # JSON summary：整体测试结果
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
        checker = DrivesInfoCheck("DrivesInfoCheck")
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
