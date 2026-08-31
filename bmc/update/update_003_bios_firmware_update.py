#!/bin/python
"""
Author: Fengmian
Date: 2026/05/12
Usage:
  python3 bmc/update/update_003_bios_firmware_update.py
      -i <bmc_ip> -u <username> -p <password>
      --image-uri <http://nginx_host/firmware/bios_new.bin>
      [--default-image-uri <http://nginx_host/firmware/bios_default.bin>]
      [--protocol HTTP|HTTPS|SFTP|NFS|SCP]
      [--bios-flash Flash1|Flash2|Both]

Update:
2026/05/12: 新增，BIOS 固件版本刷新功能测试（正向 + 自动恢复）
2026/05/12: 支持 HTTP 协议（nginx）；加入自动恢复（阶段B）
2026/06/09: 重构 — 使用 SDK 原生 wait_for_task() 替代自定义轮询；
            参数解析改为 override _add_args()，适配 BmcTestBase 统一规范；
            类型注解对齐 update_002

测试内容（正向 + 自动恢复）：
  阶段A 正向刷新：
    1. 记录刷新前 BIOS 版本
    2. 通过 simple_update()（SDK 自动适配厂商策略）触发刷写，FlashItem=BIOS
    3. 从响应提取 Task ID，调用 SDK wait_for_task() 等待完成（超时 1800s）
    4. BIOS 刷新需完整下上电生效：
         GracefulShutdown → 等关机（超时 300s）
         → ForceOff（AC 断电）→ 等 30s
         → On（AC 上电）→ ping 带内 ServerIP（超时 600s，等 OS 启动）
    5. 读取刷新后 BIOS 版本（GET /Systems/1 → BiosVersion），验证版本已更新

  阶段B 自动恢复（无论阶段A结果均执行）：
    6. 使用 --default-image-uri 指定的默认版本镜像回刷
    7. 重复步骤3~5（wait_for_task + 完整下上电）
    8. 验证版本已回到默认版本（与刷新前一致）

最终 PASS 标准：
  - 阶段A（正向）：Task完成 + 完整下上电 + 版本已更新
  - 阶段B（恢复）：Task完成 + 完整下上电 + 版本已回默认
  - 若 --default-image-uri 未指定则跳过阶段B（WARNING）

注意：
  - BIOS 版本从 GET /redfish/v1/Systems/1 的 BiosVersion 字段读取
  - ServerIP 为服务器带内 IP（非 BMC IP），未配置则跳过 OS ping 检测，等待 120s
  - SDK wait_for_task() 覆盖超时为 1800s，轮询间隔 15s
  - simple_update() 自动检测厂商并路由到对应 OEM 策略（浪潮/中兴/联想等）
"""

import argparse
import os
import subprocess
import sys
import time
import traceback
from typing import Any

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.exceptions import RedfishTimeoutError

# ── 超时 / 轮询常量 ──────────────────────────────────────────────────────────
TASK_TIMEOUT_SEC     = 1800   # wait_for_task() 超时（30min，固件刷写场景）
TASK_POLL_SEC        = 15     # wait_for_task() 轮询间隔（覆盖 SDK 默认5s）
SHUTDOWN_TIMEOUT_SEC = 300    # GracefulShutdown 等待服务器关机超时
POWEROFF_WAIT_SEC    = 30     # AC 断电后等待时间
POWERON_TIMEOUT_SEC  = 600    # AC 上电后等待 OS POST 完成超时
POLL_INTERVAL_SEC    = 10     # 恢复检测轮询间隔

SIMPLE_UPDATE_TARGETS = ["/redfish/v1/Systems/1"]
SYSTEMS_URI           = "/redfish/v1/Systems/1"
RESET_URI             = "/redfish/v1/Systems/1/Actions/ComputerSystem.Reset"


class Update003BiosFirmwareUpdate(BmcTestBase):
    """BIOS 固件版本刷新功能测试（正向 + 自动恢复）

    用例编号：Redfish_UpdateService_003
    检查项：
      - simple_update() 请求发送成功
      - wait_for_task() 终态为 Completed
      - 完整下上电周期（GracefulShutdown → ForceOff → On）
      - 服务器 OS 启动（ping 带内 ServerIP）
      - 刷新后版本已更新（阶段A）/ 已回默认（阶段B）
    """

    TEST_CASE_KEY = "Update003BiosFirmwareUpdate"

    CSV_HEADER = "phase,phase_result,version_before,version_after\n"

    def __init__(self, case: str):
        # 先初始化扩展参数（_add_args override 会在 super().__init__ 中调用）
        self.IMAGE_URI         = None
        self.DEFAULT_IMAGE_URI = None
        self.PROTOCOL          = None
        self.BIOS_FLASH        = None
        self.SERVER_IP         = ""
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/update/update_003_bios_firmware_update.json"),
        )
        self.command_check_result = "FAIL"

    # ── 参数解析（override：增加 BIOS 升级专用参数）──────────────────────────

    def _add_args(self) -> None:
        parser = argparse.ArgumentParser(description="BIOS 固件版本刷新功能测试")
        parser.add_argument("-i", "--bmc_ip",            type=str, help="BMC IP 地址")
        parser.add_argument("-u", "--user_name",         type=str, help="BMC 用户名")
        parser.add_argument("-p", "--password",          type=str, help="BMC 密码")
        parser.add_argument("--image-uri",               type=str, default=None,
                            dest="image_uri",            help="新版本 BIOS 镜像 URI（HTTP/NFS/TFTP）")
        parser.add_argument("--default-image-uri",       type=str, default=None,
                            dest="default_image_uri",    help="默认版本 BIOS 镜像 URI（用于阶段B回刷）")
        parser.add_argument("--protocol",                type=str, default=None,
                            help="传输协议（HTTP/HTTPS/SFTP/NFS/SCP），默认取配置文件值")
        parser.add_argument("--bios-flash",              type=str, default=None,
                            dest="bios_flash",           help="BIOS Flash 目标（Flash1/Flash2/Both），默认取配置文件值")
        args, _ = parser.parse_known_args()
        self.BMC_IP            = args.bmc_ip
        self.USERNAME          = args.user_name
        self.PASSWORD          = args.password
        self.IMAGE_URI         = args.image_uri
        self.DEFAULT_IMAGE_URI = args.default_image_uri
        self.PROTOCOL          = args.protocol
        self.BIOS_FLASH        = args.bios_flash

    # ── 额外配置（从 JSON 补填命令行未指定的参数）───────────────────────────

    def _load_extra_config(self, conf_section: dict) -> None:
        self.SERVER_IP = conf_section.get("ServerIP", "")
        if self.IMAGE_URI         is None:
            self.IMAGE_URI         = conf_section.get("ImageURI") or None
        if self.DEFAULT_IMAGE_URI is None:
            self.DEFAULT_IMAGE_URI = conf_section.get("DefaultImageURI") or None
        if not self.PROTOCOL:
            self.PROTOCOL          = conf_section.get("Protocol", "HTTP")
        if not self.BIOS_FLASH:
            self.BIOS_FLASH        = conf_section.get("BiosFlash", "Both")

    # ── 辅助：获取当前 BIOS 版本 ─────────────────────────────────────────────

    def _get_bios_version(self, test: CommonFunction) -> str:
        """Get BiosVersion from System resource via SDK."""
        try:
            sys_obj = self.client.get_system()
            return sys_obj.bios_version or "Unknown"
        except Exception as e:
            test.print_log("WARNING", f"获取 BIOS 版本失败：{e}")
            return "Unknown"

    # ── 辅助：等待服务器关机（PowerState=Off）────────────────────────────────

    def _wait_poweroff(self, test: CommonFunction, label: str) -> bool:
        """Poll System PowerState until Off or timeout."""
        test.print_log("INFO",
            f"[{label}] 等待服务器关机（超时 {SHUTDOWN_TIMEOUT_SEC}s）...")
        deadline = time.time() + SHUTDOWN_TIMEOUT_SEC
        while time.time() < deadline:
            try:
                sys_obj = self.client.get_system()
                state = sys_obj.power_state or ""
                test.print_log("INFO", f"[{label}] PowerState={state}")
                if state == "Off":
                    test.print_log("INFO", f"[{label}] 服务器已关机")
                    return True
            except Exception as e:
                test.print_log("WARNING", f"[{label}] 查询 PowerState 异常：{e}")
            time.sleep(POLL_INTERVAL_SEC)
        test.print_log("ERROR",
            f"[{label}] 服务器关机超时（>{SHUTDOWN_TIMEOUT_SEC}s）")
        return False

    # ── 辅助：等待服务器上电后 ping 通带内 IP ────────────────────────────────

    def _wait_poweron_ping(self, test: CommonFunction, label: str) -> bool:
        """
        ping SERVER_IP 直到通或超时。
        SERVER_IP 未配置则等待 120s 后直接返回 True（跳过 OS 启动确认）。
        """
        if not self.SERVER_IP:
            test.print_log("WARNING",
                f"[{label}] 未配置 ServerIP，跳过 OS 启动 ping 检测，等待 120s 后继续")
            time.sleep(120)
            return True
        test.print_log("INFO",
            f"[{label}] 等待服务器 OS 启动（ping {self.SERVER_IP}，超时 {POWERON_TIMEOUT_SEC}s）...")
        deadline = time.time() + POWERON_TIMEOUT_SEC
        while time.time() < deadline:
            try:
                r = subprocess.run(
                    ["ping", "-c", "1", "-W", "2", self.SERVER_IP],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
                if r.returncode == 0:
                    test.print_log("INFO",
                        f"[{label}] 服务器 {self.SERVER_IP} ping 通，OS 已启动")
                    return True
            except Exception:
                pass
            time.sleep(POLL_INTERVAL_SEC)
        test.print_log("ERROR",
            f"[{label}] 服务器上电 ping 超时（>{POWERON_TIMEOUT_SEC}s）")
        return False

    # ── 核心：执行一次完整 BIOS 刷写流程 ────────────────────────────────────

    def _do_flash(self, test: CommonFunction, image_uri: str, phase_label: str) -> dict:
        """
        执行完整 BIOS 刷写流程：
          1. simple_update() 触发刷写
          2. SDK wait_for_task() 等待 Task 完成
          3. GracefulShutdown → 等关机 → ForceOff → 等待 → On（完整下上电）
          4. ping 带内 ServerIP，确认 OS 启动
          5. 重建连接，读取刷后 BIOS 版本

        返回 phase_detail dict：
          phase_label, image_uri, task, power_cycle, version_after, checks, phase_result
        """
        phase: dict[str, Any]          = {"phase_label": phase_label, "image_uri": image_uri}
        checks: list[tuple[str, bool]] = []

        # ── 步骤1：simple_update() ──────────────────────────────────────────
        su_kwargs: dict[str, Any] = {
            "image_uri":         image_uri,
            "transfer_protocol": self.PROTOCOL,
            "targets":           SIMPLE_UPDATE_TARGETS,
            "flash_item":        "BIOS",
            "bios_flash":        self.BIOS_FLASH,
        }
        test.print_log("INFO",
            f"[{phase_label}] SDK simple_update — flash_item=BIOS, "
            f"bios_flash={self.BIOS_FLASH}, protocol={self.PROTOCOL}, image_uri={image_uri}")
        try:
            resp = self.client.simple_update(**su_kwargs)
            test.print_log("INFO", f"[{phase_label}] SimpleUpdate 响应：{resp}")
            checks.append(("SimpleUpdate 请求发送成功", True))
        except RedfishException as e:
            test.print_log("ERROR", f"[{phase_label}] SimpleUpdate 失败：{e}")
            checks.append(("SimpleUpdate 请求发送成功", False))
            phase["update_error"] = str(e)
            phase["checks"]       = _to_check_list(checks)
            phase["phase_result"] = "FAIL"
            return phase

        # ── 步骤2：SDK wait_for_task() ──────────────────────────────────────
        task_id  = None
        task_uri = getattr(resp, "odata_id", None) or ""
        if "/Tasks/" in task_uri:
            task_id = task_uri.split("/Tasks/")[-1].rstrip("/")
            test.print_log("INFO", f"[{phase_label}] 获取到 Task ID：{task_id}")

        if task_id:
            test.print_log("INFO",
                f"[{phase_label}] 调用 SDK wait_for_task("
                f"task_id={task_id}, poll_interval={TASK_POLL_SEC}, timeout={TASK_TIMEOUT_SEC})...")
            try:
                final_task = self.client.wait_for_task(
                    task_id=task_id,
                    poll_interval=TASK_POLL_SEC,
                    timeout=TASK_TIMEOUT_SEC,
                )
                task_state = getattr(final_task, "task_state", None) or "Unknown"
                percent    = getattr(final_task, "percent_complete", None)
                test.print_log("INFO",
                    f"[{phase_label}] Task 完成，终态：{task_state}，进度：{percent}%")

                task_ok = (task_state in ("Completed", "OK"))
                if not task_ok:
                    test.print_log("ERROR",
                        f"[{phase_label}] Task 终态异常：{task_state}")
                checks.append((f"Task 完成（终态={task_state}）", task_ok))
                phase["task"] = {"id": task_id, "final_state": task_state, "percent": percent}

            except RedfishTimeoutError as e:
                test.print_log("ERROR",
                    f"[{phase_label}] wait_for_task 超时（>{TASK_TIMEOUT_SEC}s）：{e}")
                checks.append(("Task 完成（超时）", False))
                phase["task"]         = {"id": task_id, "final_state": "Timeout"}
                phase["checks"]       = _to_check_list(checks)
                phase["phase_result"] = "FAIL"
                return phase
            except Exception as e:
                test.print_log("ERROR",
                    f"[{phase_label}] wait_for_task 异常：{e}")
                checks.append(("Task 完成（异常）", False))
                phase["task"]         = {"id": task_id, "final_state": "Exception"}
                phase["checks"]       = _to_check_list(checks)
                phase["phase_result"] = "FAIL"
                return phase

            if not task_ok:
                phase["checks"]       = _to_check_list(checks)
                phase["phase_result"] = "FAIL"
                return phase
        else:
            # 部分厂商不返回 Task，BIOS 写入时间较长，直接等待
            test.print_log("WARNING",
                f"[{phase_label}] 未获取到 Task ID，等待 120s 后继续（BIOS 写入较慢）...")
            time.sleep(120)

        # ── 步骤3：完整下上电周期 ────────────────────────────────────────────
        # GracefulShutdown → 等关机
        test.print_log("INFO", f"[{phase_label}] BIOS 已写入，触发 GracefulShutdown...")
        try:
            self.client.post(RESET_URI, {"ResetType": "GracefulShutdown"})
        except RedfishException as e:
            test.print_log("ERROR", f"[{phase_label}] GracefulShutdown 失败：{e}")
            checks.append(("GracefulShutdown 发送成功", False))
            phase["checks"]       = _to_check_list(checks)
            phase["phase_result"] = "FAIL"
            return phase

        shutdown_ok = self._wait_poweroff(test, phase_label)
        checks.append(("服务器关机成功（PowerState=Off）", shutdown_ok))
        if not shutdown_ok:
            phase["checks"]       = _to_check_list(checks)
            phase["phase_result"] = "FAIL"
            return phase

        # ForceOff（AC 断电）→ 冷却等待
        test.print_log("INFO", f"[{phase_label}] AC 断电（ForceOff）...")
        try:
            self.client.post(RESET_URI, {"ResetType": "ForceOff"})
        except RedfishException as e:
            # 已处于 Off 状态时可能返回错误，非致命
            test.print_log("WARNING",
                f"[{phase_label}] ForceOff 响应异常（可能已断电）：{e}")

        test.print_log("INFO",
            f"[{phase_label}] 等待 {POWEROFF_WAIT_SEC}s AC 断电冷却...")
        time.sleep(POWEROFF_WAIT_SEC)

        # On（AC 上电）
        test.print_log("INFO", f"[{phase_label}] AC 上电（On）...")
        try:
            self.client.post(RESET_URI, {"ResetType": "On"})
            checks.append(("AC 上电命令发送成功", True))
        except RedfishException as e:
            test.print_log("ERROR", f"[{phase_label}] AC 上电失败：{e}")
            checks.append(("AC 上电命令发送成功", False))
            phase["checks"]       = _to_check_list(checks)
            phase["phase_result"] = "FAIL"
            return phase

        phase["power_cycle"] = {
            "graceful_shutdown": "PASS" if shutdown_ok else "FAIL",
            "force_off":         "PASS",
            "power_on":          "PASS",
        }

        # ── 步骤4：ping 带内 ServerIP，确认 OS 已启动 ───────────────────────
        poweron_ok = self._wait_poweron_ping(test, phase_label)
        checks.append(("服务器 OS 启动成功（ping 通带内 IP）", poweron_ok))
        if not poweron_ok:
            # ping 超时不立即返回 FAIL，仍尝试读版本（部分环境无带内 IP）
            test.print_log("WARNING",
                f"[{phase_label}] 服务器 ping 超时，尝试继续读取 BIOS 版本...")

        # ── 步骤5：重建连接，读刷后 BIOS 版本 ──────────────────────────────
        try:
            self.client.close()
        except Exception:
            pass
        self.client = RedfishClient(
            host=self.BMC_IP,
            username=self.USERNAME,
            password=self.PASSWORD,
        )
        version_after = self._get_bios_version(test)
        test.print_log("INFO", f"[{phase_label}] 刷新后 BIOS 版本：{version_after}")
        phase["version_after"] = version_after

        phase["checks"]       = _to_check_list(checks)
        phase["phase_result"] = "PASS" if all(r for _, r in checks) else "FAIL"
        return phase

    # ── 测试主入口 ───────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        if not self.IMAGE_URI:
            test.print_log("ERROR",
                "未指定 --image-uri（新版本 BIOS 镜像 URI），无法执行固件刷新，直接 FAIL")
            self.command_check_result = "FAIL"
            self._write_results({}, "FAIL", "SKIP", "Unknown")
            return

        detail         = {}
        phase_a_result = "FAIL"
        phase_b_result = "SKIP"
        version_before = "Unknown"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD,
            )

            # ── 记录刷新前版本 ────────────────────────────────────────────
            version_before = self._get_bios_version(test)
            test.print_log("INFO", f"刷新前 BIOS 版本（默认版本）：{version_before}")
            detail["version_before"] = version_before

            # ══════════════════════════════════════════════════════════════
            # 阶段 A：正向刷新
            # ══════════════════════════════════════════════════════════════
            test.print_log("INFO", "=" * 60)
            test.print_log("INFO", "阶段A：正向刷新（刷入新版本）")
            test.print_log("INFO", "=" * 60)

            phase_a         = self._do_flash(test, self.IMAGE_URI, "阶段A-正向")
            detail["phase_a"] = phase_a
            phase_a_result  = phase_a.get("phase_result", "FAIL")
            version_after_a = phase_a.get("version_after", "Unknown")

            if phase_a_result == "PASS":
                if version_after_a != version_before:
                    test.print_log("INFO",
                        f"阶段A PASS：BIOS 版本已更新（{version_before} → {version_after_a}）")
                else:
                    test.print_log("WARNING",
                        f"阶段A：版本未变更（{version_before} = {version_after_a}），"
                        "镜像可能与当前版本一致，标记为 FAIL")
                    phase_a_result = "FAIL"
                    detail["phase_a"]["phase_result"] = "FAIL"
            else:
                test.print_log("ERROR", "阶段A FAIL")

            # ══════════════════════════════════════════════════════════════
            # 阶段 B：自动恢复（无论阶段A结果均执行）
            # ══════════════════════════════════════════════════════════════
            test.print_log("INFO", "=" * 60)
            test.print_log("INFO", "阶段B：自动恢复（回刷默认版本）")
            test.print_log("INFO", "=" * 60)

            if not self.DEFAULT_IMAGE_URI:
                test.print_log("WARNING",
                    "未指定 --default-image-uri，跳过阶段B。"
                    "请人工确保 BIOS 固件版本恢复至默认版本！")
                phase_b_result = "SKIP"
                detail["phase_b"] = {
                    "phase_result": "SKIP",
                    "reason": "DefaultImageURI 未配置",
                }
            else:
                phase_b         = self._do_flash(test, self.DEFAULT_IMAGE_URI, "阶段B-恢复")
                detail["phase_b"] = phase_b
                phase_b_result  = phase_b.get("phase_result", "FAIL")
                version_after_b = phase_b.get("version_after", "Unknown")

                if phase_b_result == "PASS":
                    if version_after_b == version_before:
                        test.print_log("INFO",
                            f"阶段B PASS：BIOS 版本已恢复至默认（{version_after_b}）")
                    else:
                        test.print_log("WARNING",
                            f"阶段B：版本未恢复为默认（期望={version_before}，"
                            f"实际={version_after_b}），标记为 FAIL")
                        phase_b_result = "FAIL"
                        detail["phase_b"]["phase_result"] = "FAIL"
                else:
                    test.print_log("ERROR",
                        "阶段B FAIL（BIOS 固件未恢复至默认版本，需人工介入！）")

        except Exception as e:
            test.print_log("ERROR", f"未处理异常：{e}")
            traceback.print_exc()
        finally:
            try:
                self.client.close()
            except Exception:
                pass

        # ── 最终结果判定 ─────────────────────────────────────────────────
        if phase_b_result == "SKIP":
            final = "PASS" if phase_a_result == "PASS" else "FAIL"
            test.print_log("WARNING", "注意：阶段B（自动恢复）已跳过，请手动确认 BIOS 版本")
        else:
            final = "PASS" if (phase_a_result == "PASS" and phase_b_result == "PASS") else "FAIL"

        test.print_log("INFO", "=" * 60)
        test.print_log(
            "INFO" if phase_a_result == "PASS" else "ERROR",
            f"阶段A（正向）：{phase_a_result}"
        )
        test.print_log(
            "INFO" if phase_b_result in ("PASS", "SKIP") else "ERROR",
            f"阶段B（恢复）：{phase_b_result}"
        )
        test.print_log(
            "INFO" if final == "PASS" else "ERROR",
            f"最终结果：{final}"
        )

        self._write_results(detail, phase_a_result, phase_b_result, version_before)
        self.command_check_result = final

    # ── 结果写入辅助 ─────────────────────────────────────────────────────────

    def _write_results(
        self,
        detail: dict[str, Any],
        phase_a_result: str,
        phase_b_result: str,
        version_before: str,
    ) -> None:
        test = CommonFunction()
        detail["summary"] = {
            "version_before": version_before,
            "phase_a_result": phase_a_result,
            "phase_b_result": phase_b_result,
        }
        final = self.command_check_result
        test.add_key_value_to_json(self.result_json_path, "detail", value=detail)
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": final}
        )

        version_after_a = detail.get("phase_a", {}).get("version_after", "")
        version_after_b = detail.get("phase_b", {}).get("version_after", "")
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"A,{phase_a_result},{version_before},{version_after_a}\n")
            f.write(f"B,{phase_b_result},{version_after_a},{version_after_b}\n")

        exit_code = 0 if final == "PASS" else 2
        with open(self.exit_code_path, "w", encoding="utf-8") as f:
            f.write(str(exit_code))
        self.exit_code = exit_code


# ── 模块级辅助 ───────────────────────────────────────────────────────────────

def _to_check_list(checks: list[tuple[str, bool]]) -> list[dict[str, str]]:
    """将 [(label, bool), ...] 转换为 [{"label": ..., "result": "PASS"/"FAIL"}, ...]"""
    return [{"label": label, "result": "PASS" if ok else "FAIL"} for label, ok in checks]


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = 1
    obj = None
    try:
        obj = Update003BiosFirmwareUpdate(Update003BiosFirmwareUpdate.TEST_CASE_KEY)
        obj.run_test()
        exit_code = getattr(obj, "exit_code", 0 if obj.command_check_result == "PASS" else 2)
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "检测到键盘中断，提前终止")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"发生未处理异常：{e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        try:
            if obj and hasattr(obj, "exit_code_path"):
                with open(obj.exit_code_path, "w", encoding="utf-8") as f:
                    f.write(str(exit_code))
        except Exception as e:
            CommonFunction.print_log("ERROR", f"写入退出码文件失败：{e}")
            exit_code = 3
    sys.exit(exit_code)
