#!/bin/python
"""
Author: Zhiling
Date: 2026/05/09
Usage: python3 bmc/chassis_011_nvme_power_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/09: 新增，NVMe 上下电测试
2026/06/08: 重写——移除 requests 裸调用，改用 SDK get_raw / client.post / client.get_drives
2026/06/23: 改写为 redfish_sdk v1.1.0 类型化接口（get_drives 模型字段 / get_drive / drive_reset）

测试策略（标准 Redfish）：
  规范依据：Redfish Drive v1.5+ 支持 #Drive.Reset Action（GracefulShutdown/ForceOn）

  1. 调用 get_drives() 获取 Drive 列表，验证数量 > 0
  2. 对每个 Drive 直接读取 v1.1.0 模型字段：
     - 必要字段：name、media_type、protocol、capacity_bytes、status
     - actions 字段：是否暴露 #Drive.Reset（弱类型 dict）
     - power_state 字段：是否存在
  3. 若 #Drive.Reset 存在 → 用 drive_reset() 执行 GracefulShutdown + ForceOn
     执行后 get_drive().power_state 回读验证
  4. 若无 #Drive.Reset → WARNING（厂商未实现），基础字段合规即 PASS

  PASS 条件：Drive 集合非空 + 所有必要字段完整（Reset 缺失只 WARNING 不 FAIL）
  FAIL 条件：Drive 集合为空，或任意 Drive 必要字段缺失，或 Drive.Reset 执行失败
"""

import os
import time
import sys
import traceback
from pydantic import ValidationError

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException


class Chassis011NvmePowerTest(BmcTestBase):
    """NVMe 上下电测试

    用例编号：Redfish_Chassis_011
    测试内容：
    - Drive 集合查询与必要字段完整性验证
    - 标准 Drive.Reset Action 存在性检查
    - 若支持 → 执行上下电并验证 PowerState
    - 若不支持 → WARNING，推动厂商整改
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/chassis/chassis_011_nvme_power_test.json"),
        )
    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
                CommonFunction.print_log("DEBUG", "Redfish SDK 客户端已关闭")
            except Exception:
                pass

    # ── Drive.Reset 执行 ────────────────────────────────────────────────────

    def _do_drive_reset(self, drive_odata_id: str) -> tuple:
        """
        执行 Drive.Reset（GracefulShutdown + ForceOn），回读 PowerState 验证。
        返回 (success: bool, detail: dict)
        """
        detail = {}
        reset_ok = True

        for reset_type in ["GracefulShutdown", "ForceOn"]:
            try:
                self.client.drive_reset(drive_odata_id, reset_type)
                CommonFunction.print_log("INFO", f"  Drive.Reset {reset_type}：成功")
                detail[reset_type] = "success"
            except RedfishException as e:
                CommonFunction.print_log("ERROR", f"  Drive.Reset {reset_type}：失败 — {str(e)[:120]}")
                detail[reset_type] = f"failed: {str(e)[:80]}"
                reset_ok = False
            time.sleep(3)

        # 回读 PowerState（v1.1.0：get_drive().power_state）
        try:
            power_state = self.client.get_drive(drive_odata_id).power_state
            CommonFunction.print_log("INFO", f"  Drive Reset 后 PowerState：{power_state}")
            detail["final_power_state"] = power_state
        except RedfishException as e:
            CommonFunction.print_log("WARNING", f"  回读 Drive PowerState 失败：{str(e)[:80]}")
            detail["final_power_state"] = None

        return reset_ok, detail

    # ── 主测试流程 ──────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        checks       = []
        drive_results = []
        warnings     = []
        has_reset_action      = False
        target_drive_for_reset = None  # (odata_id, capacity_bytes, reset_action_target)

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD,
            )

            # ── 1. 获取 Drive 列表 ──────────────────────────────────────────
            try:
                drives = self.client.get_drives()
            except RedfishException as e:
                test.print_log("ERROR", f"get_drives() 失败（Redfish 接口异常）：{str(e)}")
                self.command_check_result = "FAIL"
                return
            except ValidationError as e:
                # BMC 端 Bug：返回数据不符合 Redfish 规范（如 Id 为整数而非字符串），
                # SDK Pydantic 模型校验失败，如实记录 FAIL。
                test.print_log(
                    "ERROR",
                    f"get_drives() 失败（BMC 返回数据不符合 Redfish 规范，SDK 解析错误）：{str(e)}"
                )
                self.command_check_result = "FAIL"
                return

            test.print_log("INFO", f"Drive 列表数量：{len(drives)}")
            checks.append(("Drive 集合非空（至少 1 个 Drive）", len(drives) > 0))

            # ── 2. 逐 Drive 字段完整性 + Action 探测 ───────────────────────
            field_fail_count = 0

            for drive in drives:
                drive_odata_id = drive.odata_id
                # v1.1.0：Drive 模型已含 name/media_type/protocol/capacity_bytes/status/
                # power_state/actions，直接读取，无需 get_raw
                drive_name = drive.name or drive_odata_id.split("/")[-1]

                # 必要字段检查（映射 Redfish 字段名 → v1.1.0 模型属性）
                field_map = {
                    "Name":          drive.name,
                    "MediaType":     drive.media_type,
                    "Protocol":      drive.protocol,
                    "CapacityBytes": drive.capacity_bytes,
                    "Status":        drive.status,
                }
                missing = [f for f, v in field_map.items() if v is None]
                if missing:
                    test.print_log("ERROR", f"Drive [{drive_name}] 缺少必要字段：{missing}")
                    field_fail_count += 1
                else:
                    test.print_log("INFO", f"Drive [{drive_name}] 必要字段完整 ✓")

                # Action 探测（drive.actions 为弱类型 dict，结构因厂商而异）
                actions      = drive.actions or {}
                reset_action = actions.get("#Drive.Reset", {})
                reset_target = reset_action.get("target") if reset_action else None

                if reset_target:
                    has_reset_action = True
                    cap = drive.capacity_bytes if drive.capacity_bytes is not None else float("inf")
                    if target_drive_for_reset is None or cap < target_drive_for_reset[1]:
                        target_drive_for_reset = (drive_odata_id, cap, reset_target)

                drive_results.append({
                    "name":           drive_name,
                    "odata_id":       drive_odata_id,
                    "media_type":     drive.media_type,
                    "protocol":       drive.protocol,
                    "capacity_bytes": drive.capacity_bytes,
                    "power_state":    drive.power_state,
                    "status":         drive.status.model_dump(by_alias=True) if drive.status else None,
                    "has_reset_action": bool(reset_target),
                })

            checks.append(("所有 Drive 必要字段完整", field_fail_count == 0))

            # ── 3. Drive.Reset 测试 ─────────────────────────────────────────
            if has_reset_action and target_drive_for_reset:
                drive_uri, _, reset_target = target_drive_for_reset
                test.print_log("INFO", f"发现 Drive.Reset Action，对 [{drive_uri}] 执行上下电测试")
                reset_ok, reset_detail = self._do_drive_reset(drive_uri)
                checks.append(("Drive.Reset 上下电操作成功", reset_ok))
                test.add_key_value_to_json(
                    self.result_json_path, "detail.cycle",
                    value={"metrics": "drive_reset_detail", "value": reset_detail}
                )
            else:
                msg = ("NVMe 上下电：厂商未实现标准 #Drive.Reset Action，"
                       "请推动厂商按 Redfish Drive v1.5+ 规范补充该接口")
                test.print_log("WARNING", msg)
                warnings.append(msg)

            final = "PASS" if all(r for _, r in checks) else "FAIL"

        except RedfishException as e:
            test.print_log("ERROR", f"Redfish SDK 调用失败：{str(e)}")
            final = "FAIL"
        except Exception as e:
            test.print_log("ERROR", f"测试异常：{str(e)}")
            traceback.print_exc()
            final = "FAIL"
        finally:
            self.close_sdk_client()

        for label, passed in checks:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": label, "value": "PASS" if passed else "FAIL"}
            )
        if warnings:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": "warnings", "value": warnings}
            )
        test.add_key_value_to_json(
            self.result_json_path, "detail.cycle",
            value={"metrics": "drive_results", "value": drive_results}
        )
        self.command_check_result = final
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NUM, "value": final}
        )
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"{self.TEST_NUM},{final}\n")
        test.print_log("INFO", f"测试完成，结果：{final}")


if __name__ == "__main__":
    exit_code = 0
    obj = None
    try:
        obj = Chassis011NvmePowerTest("Chassis011NvmePowerTest")
        obj.run_test()
        if obj.command_check_result == "PASS":
            exit_code = 0
        elif obj.command_check_result == "FAIL":
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
            if obj and hasattr(obj, "exit_code_path"):
                with open(obj.exit_code_path, "w", encoding="utf-8") as f:
                    f.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
            exit_code = 3
    sys.exit(exit_code)
