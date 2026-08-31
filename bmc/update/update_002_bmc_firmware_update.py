#!/bin/python
"""
Author: Fengmian
Date: 2026/05/12
Usage:
  python3 bmc/update/update_002_bmc_firmware_update.py
      -i <bmc_ip> -u <username> -p <password>
      --image-uri <http://nginx_host/firmware/bmc_new.bin>
      [--default-image-uri <http://nginx_host/firmware/bmc_default.bin>]
      [--protocol HTTP|HTTPS|SFTP|NFS|SCP]
      [--preserve-conf]

Update:
2026/05/12: 新增，BMC 固件版本刷新功能测试（正向 + 自动恢复）
2026/05/12: 支持 HTTP 协议（nginx）；加入自动恢复（阶段B）
2026/06/09: 重构 — 使用 SDK 原生 wait_for_task() 替代自定义轮询；
            参数解析改为 override _add_args()，适配 BmcTestBase 统一规范

测试内容（正向 + 自动恢复）：
  阶段A 正向刷新：
    1. 记录刷新前 BMC 固件版本
    2. 通过 simple_update()（SDK 自动适配厂商策略）触发刷写，FlashItem=BMC
    3. 从响应提取 Task ID，调用 SDK wait_for_task() 等待完成（超时 1800s）
    4. 触发 BMC Manager.Reset，三阶段恢复（ping → Redfish → IPMI）
    5. 读取刷新后固件版本，验证版本已更新

  阶段B 自动恢复（无论阶段A结果均执行）：
    6. 使用 --default-image-uri 指定的默认版本镜像回刷
    7. 重复步骤3~5（wait_for_task + BMC Reset + 三阶段恢复）
    8. 验证版本已回到默认版本（与刷新前一致）

最终 PASS 标准：
  - 阶段A（正向）：Task完成 + BMC三阶段恢复 + 版本已更新
  - 阶段B（恢复）：Task完成 + BMC三阶段恢复 + 版本已回默认
  - 若 --default-image-uri 未指定则跳过阶段B，仅阶段A结果决定最终值

注意：
  - SDK wait_for_task() 内部已封装轮询逻辑（默认 poll_interval=5s，timeout=600s）；
    本脚本覆写超时为 1800s（固件刷写场景需要），轮询间隔改为 15s
  - task_state 终态参考 SDK 文档：Completed / Exception / Killed 等
  - 版本备份在 result JSON detail.version_before 字段，回刷失败须人工介入
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
TASK_TIMEOUT_SEC    = 1800   # wait_for_task() 超时（30min，固件刷写场景）
TASK_POLL_SEC       = 15     # wait_for_task() 轮询间隔（覆盖 SDK 默认5s）
PING_SKIP_SEC       = 30     # BMC Reset 后等待 ping 检测开始前的静默期
PING_TIMEOUT_SEC    = 300    # ping 恢复超时
REDFISH_TIMEOUT_SEC = 600    # Redfish 恢复超时
IPMI_TIMEOUT_SEC    = 600    # IPMI 恢复超时
POLL_INTERVAL_SEC   = 10     # 恢复检测轮询间隔
IPMI_STABLE_WAIT    = 15     # IPMI 第一次通后的二次确认等待

SIMPLE_UPDATE_TARGETS = ["/redfish/v1/Managers/1"]


class Update002BmcFirmwareUpdate(BmcTestBase):
    """BMC 固件版本刷新功能测试（正向 + 自动恢复）

    用例编号：Redfish_UpdateService_002
    检查项：
      - simple_update() 请求发送成功
      - wait_for_task() 终态为 Completed
      - BMC 重启后三阶段（ping/Redfish/IPMI）均恢复
      - 刷新后版本已更新（阶段A）/ 已回默认（阶段B）
    """

    TEST_CASE_KEY = "Update002BmcFirmwareUpdate"

    CSV_HEADER = "phase,phase_result,version_before,version_after\n"

    def __init__(self, case: str):
        # 先初始化扩展参数（_add_args override 会在 super().__init__ 中调用）
        self.IMAGE_URI         = None
        self.DEFAULT_IMAGE_URI = None
        self.PROTOCOL          = None
        self.PRESERVE_CONF     = False
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/update/update_002_bmc_firmware_update.json"),
        )
        self.command_check_result = "FAIL"

    # ── 参数解析（override：增加固件升级专用参数）────────────────────────────

    def _add_args(self) -> None:
        parser = argparse.ArgumentParser(description="BMC 固件版本刷新功能测试")
        parser.add_argument("-i", "--bmc_ip",            type=str, help="BMC IP 地址")
        parser.add_argument("-u", "--user_name",         type=str, help="BMC 用户名")
        parser.add_argument("-p", "--password",          type=str, help="BMC 密码")
        parser.add_argument("--image-uri",               type=str, default=None,
                            dest="image_uri",            help="新版本固件镜像 URI（HTTP/NFS/TFTP）")
        parser.add_argument("--default-image-uri",       type=str, default=None,
                            dest="default_image_uri",    help="默认版本固件镜像 URI（用于阶段B回刷）")
        parser.add_argument("--protocol",                type=str, default=None,
                            help="传输协议（HTTP/HTTPS/SFTP/NFS/SCP），默认取配置文件值")
        parser.add_argument("--preserve-conf",           action="store_true", default=False,
                            dest="preserve_conf",        help="刷写时保留配置（PreserveConf）")
        args, _ = parser.parse_known_args()
        self.BMC_IP            = args.bmc_ip
        self.USERNAME          = args.user_name
        self.PASSWORD          = args.password
        self.IMAGE_URI         = args.image_uri
        self.DEFAULT_IMAGE_URI = args.default_image_uri
        self.PROTOCOL          = args.protocol
        self.PRESERVE_CONF     = args.preserve_conf

    # ── 额外配置（从 JSON 补填命令行未指定的参数）───────────────────────────

    def _load_extra_config(self, conf_section: dict) -> None:
        if self.IMAGE_URI         is None:
            self.IMAGE_URI         = conf_section.get("ImageURI") or None
        if self.DEFAULT_IMAGE_URI is None:
            self.DEFAULT_IMAGE_URI = conf_section.get("DefaultImageURI") or None
        if not self.PROTOCOL:
            self.PROTOCOL          = conf_section.get("Protocol", "HTTP")

    # ── 辅助：获取当前 BMC 固件版本 ─────────────────────────────────────────

    def _get_bmc_version(self, test: CommonFunction) -> str:
        """Get active BMC version from FirmwareInventory via SDK."""
        try:
            items = self.client.get_firmware_inventory()
            # Priority: Active + BMC keywords
            for item in items:
                item_id = (item.id or "").lower()
                if "bmc" in item_id and "active" in item_id:
                    return item.version or "Unknown"
            # Fallback: BMC keyword only
            for item in items:
                if "bmc" in (item.id or "").lower():
                    return item.version or "Unknown"
        except Exception as e:
            test.print_log("WARNING", f"获取 BMC 版本失败：{e}")
        return "Unknown"

    # ── 辅助：BMC Reset + 三阶段恢复等待 ────────────────────────────────────

    def _bmc_reset_and_wait(self, test: CommonFunction) -> bool:
        """
        发送 Manager.Reset(ForceRestart)，依次等待：
          阶段1 — ping 通（超时 PING_TIMEOUT_SEC）
          阶段2 — Redfish 可登录（超时 REDFISH_TIMEOUT_SEC）
          阶段3 — IPMI mc info 两次确认稳定（超时 IPMI_TIMEOUT_SEC）
        全部通过返回 True，任意阶段超时返回 False。
        """
        test.print_log("INFO", "发送 BMC Manager.Reset（ForceRestart）...")
        try:
            self.client.post(
                "/redfish/v1/Managers/1/Actions/Manager.Reset",
                {"ResetType": "ForceRestart"}
            )
        except Exception as e:
            # 连接中断属正常（BMC 重启会断开连接）
            test.print_log("INFO", f"BMC Reset 已发送，连接中断属正常：{e}")

        reset_start = time.time()
        test.print_log("INFO", f"等待 {PING_SKIP_SEC}s 静默后开始 ping 检测...")
        time.sleep(PING_SKIP_SEC)

        # ── 阶段1：ping ──────────────────────────────────────────────────────
        test.print_log("INFO", f"[阶段1] 等待 ping 通（超时 {PING_TIMEOUT_SEC}s）...")
        ping_ok = False
        while time.time() - reset_start < PING_TIMEOUT_SEC:
            try:
                r = subprocess.run(
                    ["ping", "-c", "1", "-W", "2", self.BMC_IP],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
                if r.returncode == 0:
                    ping_ok = True
                    test.print_log("INFO",
                        f"[阶段1] ping 通，耗时 {int(time.time() - reset_start)}s")
                    break
            except Exception:
                pass
            time.sleep(POLL_INTERVAL_SEC)
        if not ping_ok:
            test.print_log("ERROR",
                f"[阶段1] ping 超时（>{PING_TIMEOUT_SEC}s），BMC 未恢复网络")
            return False

        # ── 阶段2：Redfish 可登录 ────────────────────────────────────────────
        test.print_log("INFO", f"[阶段2] 等待 Redfish 恢复（超时 {REDFISH_TIMEOUT_SEC}s）...")
        redfish_ok = False
        while time.time() - reset_start < REDFISH_TIMEOUT_SEC:
            try:
                tmp = RedfishClient(
                    host=self.BMC_IP,
                    username=self.USERNAME,
                    password=self.PASSWORD,
                    connect_timeout=10,
                    read_timeout=30,
                )
                mfr = tmp.get_manufacturer()
                tmp.close()
                if mfr:
                    redfish_ok = True
                    test.print_log("INFO",
                        f"[阶段2] Redfish 恢复（manufacturer={mfr}），"
                        f"耗时 {int(time.time() - reset_start)}s")
                    break
            except Exception:
                pass
            time.sleep(POLL_INTERVAL_SEC)
        if not redfish_ok:
            test.print_log("ERROR",
                f"[阶段2] Redfish 恢复超时（>{REDFISH_TIMEOUT_SEC}s）")
            return False

        # ── 阶段3：IPMI 二次确认稳定 ─────────────────────────────────────────
        test.print_log("INFO", f"[阶段3] 等待 IPMI 恢复（超时 {IPMI_TIMEOUT_SEC}s）...")

        def _mc_info_ok() -> bool:
            try:
                r = subprocess.run(
                    ["ipmitool", "-I", "lanplus", "-H", self.BMC_IP,
                     "-U", self.USERNAME, "-P", self.PASSWORD, "mc", "info"],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15,
                )
                return r.returncode == 0 and bool(r.stdout.strip())
            except Exception:
                return False

        ipmi_ok = False
        while time.time() - reset_start < IPMI_TIMEOUT_SEC:
            if _mc_info_ok():
                test.print_log("INFO",
                    f"[阶段3] IPMI 第一次通，等 {IPMI_STABLE_WAIT}s 二次确认...")
                time.sleep(IPMI_STABLE_WAIT)
                if _mc_info_ok():
                    ipmi_ok = True
                    test.print_log("INFO",
                        f"[阶段3] IPMI session 稳定，耗时 {int(time.time() - reset_start)}s")
                    break
                test.print_log("WARNING", "[阶段3] IPMI 二次确认失败，继续轮询...")
            time.sleep(POLL_INTERVAL_SEC)
        if not ipmi_ok:
            test.print_log("ERROR",
                f"[阶段3] IPMI 恢复超时（>{IPMI_TIMEOUT_SEC}s）")
            return False

        return True

    # ── 核心：执行一次完整刷写流程 ───────────────────────────────────────────

    def _do_flash(self, test: CommonFunction, image_uri: str, phase_label: str) -> dict:
        """
        执行完整刷写流程：
          1. simple_update() 触发刷写
          2. SDK wait_for_task() 等待 Task 完成（TASK_TIMEOUT_SEC / TASK_POLL_SEC）
          3. BMC Reset + 三阶段恢复
          4. 重建连接，读取刷后版本

        返回 phase_detail dict：
          phase_label, image_uri, task, recovery, version_after, checks, phase_result
        """
        phase: dict[str, Any]  = {"phase_label": phase_label, "image_uri": image_uri}
        checks: list[tuple[str, bool]] = []

        # ── 步骤1：simple_update() ──────────────────────────────────────────
        su_kwargs: dict[str, Any] = {
            "image_uri":         image_uri,
            "transfer_protocol": self.PROTOCOL,
            "targets":           SIMPLE_UPDATE_TARGETS,
            "flash_item":        "BMC",
        }
        if self.PRESERVE_CONF:
            su_kwargs["preserve_config"] = True

        test.print_log("INFO",
            f"[{phase_label}] SDK simple_update — flash_item=BMC, "
            f"protocol={self.PROTOCOL}, image_uri={image_uri}")
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
        # 从响应的 odata_id 中提取 Task ID（格式：/redfish/v1/TaskService/Tasks/<id>）
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
                    f"[{phase_label}] Task 完成，终态：{task_state}，"
                    f"进度：{percent}%")

                # DMTF task_state 终态：Completed 为成功，其余视为失败
                task_ok = (task_state in ("Completed", "OK"))
                if not task_ok:
                    test.print_log("ERROR",
                        f"[{phase_label}] Task 终态异常：{task_state}")
                checks.append((f"Task 完成（终态={task_state}）", task_ok))
                phase["task"] = {"id": task_id, "final_state": task_state, "percent": percent}

            except RedfishTimeoutError as e:
                test.print_log("ERROR",
                    f"[{phase_label}] wait_for_task 超时（>{TASK_TIMEOUT_SEC}s）：{e}")
                checks.append((f"Task 完成（超时）", False))
                phase["task"] = {"id": task_id, "final_state": "Timeout"}
                phase["checks"]       = _to_check_list(checks)
                phase["phase_result"] = "FAIL"
                return phase
            except Exception as e:
                test.print_log("ERROR",
                    f"[{phase_label}] wait_for_task 异常：{e}")
                checks.append(("Task 完成（异常）", False))
                phase["task"] = {"id": task_id, "final_state": "Exception"}
                phase["checks"]       = _to_check_list(checks)
                phase["phase_result"] = "FAIL"
                return phase

            if not task_ok:
                phase["checks"]       = _to_check_list(checks)
                phase["phase_result"] = "FAIL"
                return phase
        else:
            # 未获取到 Task ID（部分厂商不返回 Task，直接等待）
            test.print_log("WARNING",
                f"[{phase_label}] 未获取到 Task ID，等待 120s 后进入重启检测...")
            time.sleep(120)

        # ── 步骤3：BMC Reset + 三阶段恢复 ───────────────────────────────────
        # 关闭旧连接，确保状态干净
        try:
            self.client.close()
        except Exception:
            pass
        self.client = RedfishClient(
            host=self.BMC_IP,
            username=self.USERNAME,
            password=self.PASSWORD,
        )

        recover_ok = self._bmc_reset_and_wait(test)
        checks.append(("BMC 重启后三阶段恢复成功", recover_ok))
        phase["recovery"] = {"result": "PASS" if recover_ok else "FAIL"}
        if not recover_ok:
            phase["checks"]       = _to_check_list(checks)
            phase["phase_result"] = "FAIL"
            return phase

        # ── 步骤4：重建连接，读刷后版本 ─────────────────────────────────────
        self.client = RedfishClient(
            host=self.BMC_IP,
            username=self.USERNAME,
            password=self.PASSWORD,
        )
        version_after = self._get_bmc_version(test)
        test.print_log("INFO", f"[{phase_label}] 刷新后 BMC 版本：{version_after}")
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
                "未指定 --image-uri（新版本固件镜像 URI），无法执行固件刷新，直接 FAIL")
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
            version_before = self._get_bmc_version(test)
            test.print_log("INFO", f"刷新前 BMC 版本（默认版本）：{version_before}")
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
                        f"阶段A PASS：版本已更新（{version_before} → {version_after_a}）")
                else:
                    test.print_log("WARNING",
                        f"阶段A：版本未变更（{version_before} = {version_after_a}），"
                        "镜像可能与当前版本一致，标记为 FAIL")
                    phase_a_result = "FAIL"
                    detail["phase_a"]["phase_result"] = "FAIL"
            else:
                test.print_log("ERROR", f"阶段A FAIL")

            # ══════════════════════════════════════════════════════════════
            # 阶段 B：自动恢复（无论阶段A结果均执行）
            # ══════════════════════════════════════════════════════════════
            test.print_log("INFO", "=" * 60)
            test.print_log("INFO", "阶段B：自动恢复（回刷默认版本）")
            test.print_log("INFO", "=" * 60)

            if not self.DEFAULT_IMAGE_URI:
                test.print_log("WARNING",
                    "未指定 --default-image-uri，跳过阶段B。"
                    "请人工确保 BMC 固件版本恢复至默认版本！")
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
                            f"阶段B PASS：版本已恢复至默认（{version_after_b}）")
                    else:
                        test.print_log("WARNING",
                            f"阶段B：版本未恢复为默认（期望={version_before}，"
                            f"实际={version_after_b}），标记为 FAIL")
                        phase_b_result = "FAIL"
                        detail["phase_b"]["phase_result"] = "FAIL"
                else:
                    test.print_log("ERROR",
                        "阶段B FAIL（BMC 固件未恢复至默认版本，需人工介入！）")

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
            test.print_log("WARNING", "注意：阶段B（自动恢复）已跳过，请手动确认 BMC 版本")
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

def _to_check_list(checks: list) -> list:
    """将 [(label, bool), ...] 转换为 [{"label": ..., "result": "PASS"/"FAIL"}, ...]"""
    return [{"label": label, "result": "PASS" if ok else "FAIL"} for label, ok in checks]


# ── 入口 ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = 1
    obj = None
    try:
        obj = Update002BmcFirmwareUpdate(Update002BmcFirmwareUpdate.TEST_CASE_KEY)
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
