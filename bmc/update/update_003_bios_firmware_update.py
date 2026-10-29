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
2026/10/16: SDK 1.3.0 适配 - 硬编码 URI 改为 SDK/ServiceRoot 动态发现（issue/8）

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
  - simple_update() 由 SDK 自动检测厂商并路由到对应 OEM 更新策略
"""

import argparse
import os
import subprocess
import sys
import time
import traceback
from typing import Any

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction, ping_once
from func.bmc_test_base import BmcTestBase
from func.system_selection import bind_system_id

from redfish_sdk import RedfishClient, RedfishException
from redfish_sdk.exceptions import RedfishTimeoutError
from redfish_sdk.managers.update_strategies import VendorDetector

# ── 超时 / 轮询常量 ──────────────────────────────────────────────────────────
TASK_TIMEOUT_SEC     = 1800   # wait_for_task() 超时（30min，固件刷写场景）
TASK_POLL_SEC        = 15     # wait_for_task() 轮询间隔（覆盖 SDK 默认5s）
SHUTDOWN_TIMEOUT_SEC = 300    # GracefulShutdown 等待服务器关机超时
POWEROFF_WAIT_SEC    = 30     # AC 断电后等待时间
POWERON_TIMEOUT_SEC  = 600    # AC 上电后等待 OS POST 完成超时
POLL_INTERVAL_SEC    = 10     # 恢复检测轮询间隔

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
        self.IMAGE_USER        = None   # 文件服务器(镜像源)用户名（部分厂商 SimpleUpdate 根级 Username）
        self.IMAGE_PASS        = None   # 文件服务器(镜像源)密码
        self.TARGETS           = None   # 固件目标 Targets（部分厂商必须显式指定，禁止自动推导）
        self.VENDOR            = None   # 厂商策略（None → SDK 自动探测）
        self.POWER_OFF_BEFORE  = None   # 是否下电升级（json PowerOffBeforeUpgrade 决定）
        self.POWER_OFF_WAIT    = 20     # 下电后等待秒数（json PowerOffWaitSec 决定）
        self.PRESERVE_CONF     = None   # 是否保留 BIOS 配置（json PreserveConf 决定，CLI 覆盖）
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
        parser.add_argument("--system-id",               type=str, default=None,
                            dest="system_id",             help="ComputerSystem Id or @odata.id")
        parser.add_argument("--image-uri",               type=str, default=None,
                            dest="image_uri",            help="新版本 BIOS 镜像 URI（HTTP/NFS/TFTP）")
        parser.add_argument("--default-image-uri",       type=str, default=None,
                            dest="default_image_uri",    help="默认版本 BIOS 镜像 URI（用于阶段B回刷）")
        parser.add_argument("--protocol",                type=str, default=None,
                            help="传输协议（HTTP/HTTPS/SFTP/NFS/SCP），默认取配置文件值")
        parser.add_argument("--bios-flash",              type=str, default=None,
                            dest="bios_flash",           help="BIOS Flash 目标（Flash1/Flash2/Both），默认取配置文件值")
        parser.add_argument("--image-user",              type=str, default=None,
                            dest="image_user",           help="文件服务器(镜像源)用户名，写入 SimpleUpdate 根级 Username")
        parser.add_argument("--image-pass",              type=str, default=None,
                            dest="image_pass",           help="文件服务器(镜像源)密码，写入 SimpleUpdate 根级 Password")
        parser.add_argument("--targets",                 type=str, default=None,
                            dest="targets",              help="固件目标 Targets（逗号分隔）；部分厂商必须显式指定，禁止自动推导")
        parser.add_argument("--vendor",                  type=str, default=None,
                            dest="vendor",               help="厂商策略（enginetech/inspur/zte/...），默认 SDK 自动探测")
        parser.add_argument("--preserve-conf",           action="store_const", const=True,
                            default=None, dest="preserve_conf",
                            help="保留 BIOS 配置升级（true）；不传则由 json PreserveConf 决定")
        args, _ = parser.parse_known_args()
        self.BMC_IP            = args.bmc_ip
        self.USERNAME          = args.user_name
        self.PASSWORD          = args.password
        self._cli_system_id    = (args.system_id or "").strip() or None
        self.IMAGE_URI         = args.image_uri
        self.DEFAULT_IMAGE_URI = args.default_image_uri
        self.PROTOCOL          = args.protocol
        self.BIOS_FLASH        = args.bios_flash
        self.IMAGE_USER        = args.image_user
        self.IMAGE_PASS        = args.image_pass
        self.TARGETS           = args.targets
        self.VENDOR            = args.vendor
        self.PRESERVE_CONF     = args.preserve_conf

    # ── 额外配置（仅暂存；厂商相关参数延迟到 client 就绪后动态解析）─────────

    def _load_extra_config(self, conf_section: dict) -> None:
        self._conf_section = conf_section

    def _resolve_vendor_config(self, test: CommonFunction) -> None:
        """client 就绪后解析厂商并应用配置段（issue/9，思路源自上游 PR #6）。

        厂商优先级：CLI --vendor > json 顶层 Vendor > SDK VendorDetector 自动探测；
        三者均未得到有效厂商（generic 视为未识别）→ 抛 ValueError 终止。
        参数取值优先级：CLI > 厂商段(vendors.{vendor}) > default 段 > 顶层。
        仅厂商相关参数（Targets/凭据/下电升级等）放厂商段，通用参数留顶层。
        """
        conf_section = getattr(self, "_conf_section", None) or {}
        vendor = self.VENDOR or conf_section.get("Vendor") or ""
        if not vendor:
            detected = None
            try:
                detected = VendorDetector.detect(self.client)
            except Exception as e:
                test.print_log("WARNING", f"厂商动态探测异常：{e}")
            if detected and detected != "generic":
                vendor = detected
                test.print_log("INFO", f"动态探测到厂商：{vendor}")
            else:
                raise ValueError(
                    "未指定且无法探测到有效厂商（Vendor）：请通过 --vendor 指定"
                    "（如 enginetech/inspur/zte），或确认 BMC 可被识别"
                    f"（探测结果：{detected}）。程序终止。"
                )
        vcfg = (conf_section.get("vendors") or {}).get(vendor, {})
        dcfg = conf_section.get("default") or {}

        def pick(attr: str, key: str, default=None):
            """参数取值优先级：CLI(self) > 厂商段 > default段 > 顶层"""
            val = getattr(self, attr, None)
            if val in (None, ""):
                val = vcfg.get(key)
            if val in (None, ""):
                val = dcfg.get(key)
            if val in (None, ""):
                val = conf_section.get(key)
            return val if val is not None else default

        self.SERVER_IP         = pick("SERVER_IP", "ServerIP", "")
        self.IMAGE_URI         = pick("IMAGE_URI", "ImageURI")
        self.DEFAULT_IMAGE_URI = pick("DEFAULT_IMAGE_URI", "DefaultImageURI")
        self.PROTOCOL          = pick("PROTOCOL", "Protocol", "HTTP")
        self.BIOS_FLASH        = pick("BIOS_FLASH", "BiosFlash", "Both")
        self.IMAGE_USER        = pick("IMAGE_USER", "ImageUser")
        self.IMAGE_PASS        = pick("IMAGE_PASS", "ImagePass")
        self.TARGETS           = pick("TARGETS", "Targets")
        self.POWER_OFF_BEFORE  = bool(pick("POWER_OFF_BEFORE", "PowerOffBeforeUpgrade", False))
        self.POWER_OFF_WAIT    = pick("POWER_OFF_WAIT", "PowerOffWaitSec", 20) or 20
        self.PRESERVE_CONF     = bool(pick("PRESERVE_CONF", "PreserveConf", False))
        if not self.VENDOR:
            self.VENDOR        = vendor

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

    # ── 辅助：确保服务器已下电（先判断 + 必要时 ForceOff + 轮询等 Off）────────

    def _ensure_power_off(self, test: CommonFunction, label: str, wait_sec: int) -> bool:
        """针对需下电升级的厂商：确保服务器进入 AC 断电(Off)状态。

        流程：先查 PowerState（已 Off 直接返回，避免重复下电）→ ForceOff →
        wait_sec 内轮询，变为 Off 提前退出 → 超时仍未 Off 返回 False。
        （issue/9，思路源自上游 PR #6 / commit 144b591）
        """
        try:
            cur_state = self.client.get_system().power_state or ""
        except Exception as e:
            test.print_log("WARNING", f"[{label}] 查询当前 PowerState 异常：{e}")
            cur_state = ""
        if cur_state == "Off":
            test.print_log("INFO", f"[{label}] 服务器已处于 Off 状态，无需重复下电")
            return True

        test.print_log("INFO", f"[{label}] 执行下电动作（ForceOff）...")
        try:
            self.client.post(self._reset_uri, {"ResetType": "ForceOff"})
        except RedfishException as e:
            # 已处于 Off 状态时可能返回错误，非致命
            test.print_log("WARNING", f"[{label}] 下电响应异常（可能已断电）：{e}")

        test.print_log("INFO", f"[{label}] 等待下电完成（超时 {wait_sec}s）...")
        deadline = time.time() + wait_sec
        while time.time() < deadline:
            try:
                state = self.client.get_system().power_state or ""
                test.print_log("INFO", f"[{label}] PowerState={state}")
                if state == "Off":
                    test.print_log("INFO",
                        f"[{label}] 服务器已下电（PowerState=Off），提前退出等待")
                    return True
            except Exception as e:
                test.print_log("WARNING", f"[{label}] 查询 PowerState 异常：{e}")
            time.sleep(POLL_INTERVAL_SEC)

        test.print_log("ERROR",
            f"[{label}] 下电超时（>{wait_sec}s）服务器仍未进入 Off 状态，"
            f"请检查 BMC 状态或供电链路")
        return False

    # ── 辅助：等待服务器上电后 ping 通带内 IP ────────────────────────────────

    def _wait_poweron_ping(self, test: CommonFunction, label: str) -> bool:
        """
        ping SERVER_IP 直到通或超时（跨平台，issue/9）。
        SERVER_IP 未配置则等待 600s 后直接返回 True（跳过 OS 启动确认，
        600s 与常见 OS 启动耗时对齐，原 120s 对存储/GPU 机型偏短）。
        """
        if not self.SERVER_IP:
            test.print_log("WARNING",
                f"[{label}] 未配置 ServerIP，跳过 OS 启动 ping 检测，等待 600s 后继续")
            time.sleep(600)
            return True
        test.print_log("INFO",
            f"[{label}] 等待服务器 OS 启动（ping {self.SERVER_IP}，超时 {POWERON_TIMEOUT_SEC}s）...")
        deadline = time.time() + POWERON_TIMEOUT_SEC
        while time.time() < deadline:
            if ping_once(self.SERVER_IP, timeout_sec=2):
                test.print_log("INFO",
                    f"[{label}] 服务器 {self.SERVER_IP} ping 通，OS 已启动")
                return True
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

        # ── 步骤1：simple_update()（按厂商路由，不写死策略）──────────────────
        # vendor 透传（issue/9）：None → SDK VendorDetector 自动探测；
        # targets 默认取 System 资源 @odata.id（SDK 动态发现，不硬编码），
        # conf/CLI 显式指定 Targets 时覆盖（部分厂商必须显式指定，禁止自动推导）
        sys_obj = self.client.get_system()
        systems_uri = sys_obj.odata_id
        # Reset target 由 System 资源 Actions 元数据动态发现（下电前置/下上电周期共用）
        actions = getattr(sys_obj, "Actions", None) or {}
        reset_action = actions.get("#ComputerSystem.Reset", {})
        reset_uri = reset_action.get("target", "") if isinstance(reset_action, dict) else ""
        self._reset_uri = reset_uri
        su_kwargs: dict[str, Any] = {
            "image_uri":         image_uri,
            "transfer_protocol": self.PROTOCOL,
            "vendor":            self.VENDOR,   # None → SDK 自动探测
            "targets":           [systems_uri],
            "flash_item":        "BIOS",         # inspur 等: Oem.Public.FlashItem
            "image_type":        "BIOS",         # enginetech: Oem.Public.ImageType
            "bios_flash":        self.BIOS_FLASH,  # zte 等: Oem.Public.BiosFlash
            "preserve_config":   self.PRESERVE_CONF,
        }
        # 文件服务器(镜像源)凭据 → 部分厂商写入 SimpleUpdate 根级 Username/Password
        if self.IMAGE_USER:
            su_kwargs["username"] = self.IMAGE_USER
        if self.IMAGE_PASS:
            su_kwargs["password"] = self.IMAGE_PASS
        # 显式 Targets 覆盖默认（逗号分隔字符串 → 列表）
        if self.TARGETS:
            su_kwargs["targets"] = (
                [t.strip() for t in self.TARGETS.split(",") if t.strip()]
                if isinstance(self.TARGETS, str) else list(self.TARGETS)
            )
        # ── 前置：按需下电（个别厂商 BIOS 需 AC 断电刷写，conf PowerOffBeforeUpgrade）──
        if self.POWER_OFF_BEFORE:
            if not reset_uri:
                raise RedfishException("System 响应中无 #ComputerSystem.Reset target（下电升级需要）")
            power_off_ok = self._ensure_power_off(test, phase_label, self.POWER_OFF_WAIT)
            if not power_off_ok:
                test.print_log("ERROR", f"[{phase_label}] 下电失败，无法继续 BIOS 升级")
                checks.append(("下电升级（服务器进入 Off 状态）", False))
                phase["checks"]       = _to_check_list(checks)
                phase["phase_result"] = "FAIL"
                return phase
            checks.append(("下电升级（服务器进入 Off 状态）", True))

        test.print_log("INFO",
            f"[{phase_label}] SDK simple_update — vendor={self.VENDOR or '自动探测'}, "
            f"flash_item=BIOS, bios_flash={self.BIOS_FLASH}, "
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
        # GracefulShutdown → 等关机（Reset target 已在步骤1动态发现：self._reset_uri）
        if not self._reset_uri:
            raise RedfishException("System 响应中无 #ComputerSystem.Reset target")
        reset_uri = self._reset_uri
        test.print_log("INFO", f"[{phase_label}] BIOS 已写入，触发 GracefulShutdown...")
        try:
            self.client.post(reset_uri, {"ResetType": "GracefulShutdown"})
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
            self.client.post(reset_uri, {"ResetType": "ForceOff"})
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
            self.client.post(reset_uri, {"ResetType": "On"})
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
            connect_timeout=15,
            read_timeout=30,
            retry_on_read_timeout=True,
            retry_5xx=1,
        )
        bind_system_id(self.client, self.SYSTEM_ID)
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

        detail         = {}
        phase_a_result = "FAIL"
        phase_b_result = "SKIP"
        version_before = "Unknown"

        try:
            self.client = RedfishClient(
                host=self.BMC_IP,
                username=self.USERNAME,
                password=self.PASSWORD,
                connect_timeout=15,
                read_timeout=20,
                retry_on_read_timeout=True,
                retry_5xx=1,
            )
            if not self.prepare_system_client():
                return
            # ── 动态解析厂商并应用配置段（client 已就绪，可动态探测）────────
            self._resolve_vendor_config(test)

            if not self.IMAGE_URI:
                test.print_log("ERROR",
                    "未指定 --image-uri（新版本 BIOS 镜像 URI），无法执行固件刷新，直接 FAIL")
                self.command_check_result = "FAIL"
                self._write_results({}, "FAIL", "SKIP", "Unknown")
                return

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

        except ValueError as e:
            # 配置类错误（如未指定厂商）：清晰提示，不打 traceback
            test.print_log("ERROR", str(e))
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
    except ValueError as e:
        # 配置类错误（如未指定厂商），仅输出清晰提示，不打 traceback
        CommonFunction.print_log("ERROR", str(e))
        exit_code = 2
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
