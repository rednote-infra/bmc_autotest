#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/systems_005_power_control_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/22: 新增
2026/10/23: 修复真机验证反馈 - 原 120s 超时标准仅适用于通算机型，存储/GPU 机型 OS 启动
            耗时可能显著更长（最高到 600s 量级），沿用统一 120s 超时会对非通算机型产生
            误判；且原逻辑"等待超时即判 FAIL 并立即 ForceOff 复位、继续下一步"会在 OS
            尚未完全就绪时强行拔电，造成上一步残留状态污染下一步测试。
            改为三级阈值持续轮询判定（不再在中间点截断等待）：
              - <=120s 内就绪：PASS，无提示（通算机型标准）
              - 120s < 就绪用时 <=900s：PASS 但记 WARNING（存储/GPU 机型可接受，需人工关注）
              - >900s 仍未就绪：判 FAIL（此时才认为服务器确实无法进入 OS）
            只有真正判定 FAIL（超 900s）才触发 ForceOff 强制复位，避免对"慢但正常"的
            机型做破坏性操作。
2026/10/26: 新增 UNSUPPORTED 第三态（issue/9，思路源自上游 PR #14 / commit c33fb57，
            判定条件按评审收紧）——
            部分 BMC 不支持个别 ResetType（如 ForcePowerCycle，POST 返回 HTTP 400
            ActionParameterValueFormatError），机型差异不是 BMC 缺陷，不应判 FAIL
            也不应触发 ForceOff 复位。判定两级：
              1. AllowableValues 预检：BMC 显式声明 ResetType 白名单且不含该类型时
                 直接跳过（不发无效 POST）；
              2. POST 400 兜底：仅当 400 且错误明确指向 ResetType 参数不合法
                 （ActionParameterValueFormatError / ResetType 关键字）才归为
                 UNSUPPORTED，其余 400 仍判 FAIL（避免真缺陷被洗成跳过）。
            注意：运维团队必需的重启策略（ForceOff/On/GracefulShutdown/ForceRestart/
            ForcePowerCycle/PowerCycle）一个都不能少——预检仅影响"是否执行"，
            不删用例；UNSUPPORTED 步骤在汇总中单独 WARNING 提示推动厂商确认。
            另：GracefulShutdown 超时 FAIL 后追加 OS 可达性诊断（OS 拒绝关机 vs
            BMC 假成功），仅诊断不改判定，OS 侧问题转交 OS 团队。

校验策略：
  前置条件：确保服务器处于开机状态（若已关机则先 On，等 OS 就绪）

  测试序列（由配置 TestSequence 驱动，默认 7 步）：
    ForceOff        → 期望 PowerState=Off
    On              → 期望 PowerState=On + OS 就绪（三级阈值判定）
    GracefulShutdown→ 期望 PowerState=Off
    On              → 期望 PowerState=On + OS 就绪（三级阈值判定）
    ForceRestart    → 期望完整 Off→On + OS 就绪（三级阈值判定）
    ForcePowerCycle → 期望完整 Off→On + OS 就绪（三级阈值判定）
    PowerCycle      → 期望完整 Off→On + OS 就绪（三级阈值判定）

  三级阈值判定（适用于所有需要等待 OS 就绪的步骤）：
    - 实际就绪用时 <= WarningThresholdSeconds（默认 120s，通算机型标准）：PASS
    - WarningThresholdSeconds < 用时 <= HardTimeoutSeconds（默认 900s）：PASS 但记 WARNING
    - 用时 > HardTimeoutSeconds：FAIL（此时才触发 ForceOff 强制复位，继续下一步）

  PASS 条件：所有测试步骤通过（含 WARNING 的 PASS 仍计为 PASS，但需人工关注）
  FAIL 条件：任意步骤电源状态翻转超时，或 OS 就绪用时超过 HardTimeoutSeconds
"""

import os
import time
import subprocess
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException  # noqa: F401

class PowerControlTest(BmcTestBase):
    """Systems PowerControl 操作测试

    用例编号：Redfish_Systems_005
    检查项：覆盖关机/开机/重启全类 ResetType，验证电源状态变化和 OS 就绪
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_005_power_control_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        self._config_inband_ip          = conf_section.get("InbandIP", "")
        self.POWER_STATE_TIMEOUT        = conf_section.get("PowerStateTimeoutSeconds",     120)
        self.POWER_STATE_POLL_INTERVAL  = conf_section.get("PowerStatePollIntervalSeconds", 5)
        # 三级阈值：<=WARN 达标线（通算标准，无提示）；WARN~HARD 之间 PASS 但记 WARNING（存储/GPU 机型）；
        # >HARD 判 FAIL。兼容旧配置字段 OsReadyTimeoutSeconds（若存在且未显式配置 HardTimeoutSeconds，
        # 作为 HardTimeoutSeconds 的回退取值）。
        self.WARNING_THRESHOLD_SEC      = conf_section.get("WarningThresholdSeconds",       120)
        self.HARD_TIMEOUT_SEC           = conf_section.get(
            "HardTimeoutSeconds", conf_section.get("OsReadyTimeoutSeconds", 900)
        )
        self.OS_READY_POLL_INTERVAL     = conf_section.get("OsReadyPollIntervalSeconds",    10)
        self.TEST_SEQUENCE              = conf_section.get("TestSequence", [
            ["ForceOff",         "强制关机"],
            ["On",               "开机"],
            ["GracefulShutdown", "优雅关机"],
            ["On",               "开机"],
            ["ForceRestart",     "强制重启"],
            ["ForcePowerCycle",  "强制掉电重启"],
            ["PowerCycle",       "掉电重启"],
        ])

    def _init_inband_ip(self):
        """配置文件指定优先，否则从 BMC IP 末位去掉首字符 '1' 推导"""
        if self._config_inband_ip:
            self.INBAND_IP = self._config_inband_ip
            CommonFunction.print_log("DEBUG", f"带内 IP 来自配置：{self.INBAND_IP}")
            return
        try:
            parts = self.BMC_IP.split(".")
            last = parts[-1]
            if not last.startswith("1"):
                CommonFunction.print_log("WARNING", f"BMC IP 末位 {last} 不以 '1' 开头，带内 IP 推导可能有误")
            parts[-1] = last[1:]
            self.INBAND_IP = ".".join(parts)
            CommonFunction.print_log("DEBUG", f"带内 IP 推导为：{self.INBAND_IP}")
        except Exception as e:
            CommonFunction.print_log("WARNING", f"带内 IP 推导失败，OS 就绪检查将跳过 ping：{str(e)}")
            self.INBAND_IP = None

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

    # ── 电源状态工具 ──────────────────────────────────────────────────────────

    def _init_client(self):
        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

    def _get_power_state(self):
        """读取当前电源状态：'On' / 'Off' / 'NULL'"""
        try:
            system = self.client.get_system()
            state = getattr(system, "power_state", None)
            CommonFunction.print_log("DEBUG", f"当前电源状态：{state}")
            return state or "NULL"
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"读取电源状态失败：{str(e)}")
            return "NULL"

    def _ping_inband(self):
        """ping 带内 IP，True=通 / False=不通"""
        if not self.INBAND_IP:
            return False
        try:
            ret = subprocess.call(
                ["ping", "-c", "1", "-W", "3", self.INBAND_IP],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return ret == 0
        except Exception:
            return False

    def _wait_for_power_state(self, expected):
        """轮询等待 PowerState == expected，超时返回 False

        仅用于纯电源状态翻转场景（BMC 侧上报状态变化，通常很快，不涉及 OS 启动耗时），
        使用较短的 POWER_STATE_TIMEOUT 判定即可，不需要三级阈值。
        """
        max_polls = self.POWER_STATE_TIMEOUT // self.POWER_STATE_POLL_INTERVAL
        CommonFunction.print_log(
            "INFO",
            f"等待 PowerState={expected}（超时 {self.POWER_STATE_TIMEOUT}s，"
            f"间隔 {self.POWER_STATE_POLL_INTERVAL}s）"
        )
        for poll in range(int(max_polls)):
            state = self._get_power_state()
            if state == expected:
                CommonFunction.print_log("INFO", f"PowerState 已变为 {expected}，耗时约 {poll * self.POWER_STATE_POLL_INTERVAL}s")
                return True
            time.sleep(self.POWER_STATE_POLL_INTERVAL)
        CommonFunction.print_log("ERROR", f"等待 PowerState={expected} 超时（{self.POWER_STATE_TIMEOUT}s）")
        return False

    def _wait_restart_begin(self):
        """等待重启开始迹象（PowerState=Off 或带内 IP 不通），超时返回 False

        仅用于确认 reset 指令本身已生效（服务器确实开始了下电/重启动作），
        不涉及 OS 启动耗时，沿用较短的 POWER_STATE_TIMEOUT 即可——若这都超时，
        说明 reset 指令没有生效，属于真正的 FAIL，与机型无关。
        """
        CommonFunction.print_log("INFO", "等待服务器开始重启（PowerState=Off 或带内 IP 不通）")
        elapsed = 0
        while elapsed < self.POWER_STATE_TIMEOUT:
            state = self._get_power_state()
            if state == "Off" or not self._ping_inband():
                CommonFunction.print_log("INFO", f"检测到重启开始（state={state}），耗时约 {elapsed}s")
                return True
            time.sleep(self.POWER_STATE_POLL_INTERVAL)
            elapsed += self.POWER_STATE_POLL_INTERVAL
        CommonFunction.print_log("ERROR", f"等待重启开始超时（{self.POWER_STATE_TIMEOUT}s），reset 指令可能未生效")
        return False

    def _wait_ready_graded(self, require_power_on: bool = True):
        """三级阈值持续轮询，等待「PowerState=On 且带内 IP 通」（即 OS 真正就绪）

        与原实现的关键区别：不在 120s / 600s 等中间点截断判定，而是持续轮询到
        HARD_TIMEOUT_SEC（默认 900s），根据实际就绪耗时落入的区间给出三级结论：

        :param require_power_on: True 时需同时满足 PowerState==On；False 时仅判定 ping 通
            （用于 PowerState 已提前确认过 On 的场景，可省去重复查询）
        :return: (level, elapsed) — level ∈ {"PASS", "WARNING", "FAIL"}
        """
        if not self.INBAND_IP:
            CommonFunction.print_log("WARNING", "带内 IP 未配置，跳过 OS 就绪检查，等待 30s 替代")
            time.sleep(30)
            return "WARNING", 30

        CommonFunction.print_log(
            "INFO",
            f"持续等待 OS 就绪（ping {self.INBAND_IP}"
            f"{' 且 PowerState=On' if require_power_on else ''}，"
            f"达标线 {self.WARNING_THRESHOLD_SEC}s，硬超时 {self.HARD_TIMEOUT_SEC}s）"
        )
        elapsed = 0
        while elapsed <= self.HARD_TIMEOUT_SEC:
            power_ok = (self._get_power_state() == "On") if require_power_on else True
            if power_ok and self._ping_inband():
                if elapsed <= self.WARNING_THRESHOLD_SEC:
                    CommonFunction.print_log(
                        "INFO", f"OS 已就绪，耗时 {elapsed}s（≤{self.WARNING_THRESHOLD_SEC}s 达标线，PASS）"
                    )
                    return "PASS", elapsed
                else:
                    CommonFunction.print_log(
                        "WARNING",
                        f"OS 就绪耗时 {elapsed}s，超过通算机型标准 {self.WARNING_THRESHOLD_SEC}s，"
                        f"但未超硬超时 {self.HARD_TIMEOUT_SEC}s（存储/GPU 机型可接受，PASS 但需人工关注）"
                    )
                    return "WARNING", elapsed
            time.sleep(self.OS_READY_POLL_INTERVAL)
            elapsed += self.OS_READY_POLL_INTERVAL

        CommonFunction.print_log(
            "ERROR", f"等待 OS 就绪超过硬超时 {self.HARD_TIMEOUT_SEC}s，判定 FAIL"
        )
        return "FAIL", elapsed

    # ── 单步测试 ──────────────────────────────────────────────────────────────

    # 测试结果第三态：BMC 不支持该 ResetType（不在 AllowableValues 白名单，
    # 或 POST 返回明确指向 ResetType 参数不合法的 HTTP 400）。
    # UNSUPPORTED 不计入整体结果、不触发 ForceOff 复位——机型差异不是 BMC 缺陷
    # （issue/9，思路源自上游 PR #14 / commit c33fb57，判定条件已收紧）。
    UNSUPPORTED = "UNSUPPORTED"

    # HTTP 400 归类为 UNSUPPORTED 的关键字（收紧判定，避免把其他 400 真缺陷洗成跳过）：
    # ActionParameterValueFormatError 是 DMTF 对"参数值不合法"的标准错误码；
    # ResetType 关键字明确指向 ResetType 参数问题。
    _UNSUPPORTED_400_KEYWORDS = ("ActionParameterValueFormatError", "ResetType")

    def _get_supported_reset_types(self):
        """读取 BMC 声明的 ResetType 白名单（Actions.#ComputerSystem.Reset 的
        ResetType@Redfish.AllowableValues）。

        Returns:
            list：白名单（可能为空）；读取失败返回 None（未声明，走 POST 400 兜底）。
        """
        try:
            system = self.client.get_system()
            actions = getattr(system, "actions", None)
            reset_action = getattr(actions, "computer_system_reset", None) if actions else None
            if reset_action is None:
                return None
            # SDK 模型属性或 model_extra 均可能承载 AllowableValues
            allowable = getattr(reset_action, "reset_type_allowable_values", None)
            if allowable is None:
                extra = getattr(reset_action, "model_extra", None) or {}
                allowable = extra.get("ResetType@Redfish.AllowableValues")
            return list(allowable) if allowable else None
        except Exception as e:
            CommonFunction.print_log("WARNING", f"读取 ResetType AllowableValues 失败（走 POST 400 兜底）：{str(e)[:120]}")
            return None

    def _do_reset(self, reset_type):
        """发送 ResetType

        :return: True（成功）/ False（真实失败）/ UNSUPPORTED（BMC 不支持该 ResetType）
        """
        CommonFunction.print_log("INFO", f"发送电源操作：{reset_type}")
        try:
            self.client.reset(reset_type)
            CommonFunction.print_log("INFO", f"POST {reset_type} 成功")
            return True
        except RedfishException as e:
            detail = f"{getattr(e, 'message', '')} {getattr(e, 'body', '')}"
            # 收紧判定（较上游 c33fb57）：仅当 400 且错误明确指向 ResetType 参数
            # 不合法（ActionParameterValueFormatError / ResetType 关键字）才归为
            # UNSUPPORTED；其余 400（格式错误/权限等）仍判 FAIL，避免真缺陷被洗成跳过
            if (getattr(e, "status_code", None) == 400
                    and any(kw in detail for kw in self._UNSUPPORTED_400_KEYWORDS)):
                CommonFunction.print_log("WARNING", f"POST {reset_type} 被 BMC 拒绝（不支持该 ResetType）：{str(e)}")
                return self.UNSUPPORTED
            CommonFunction.print_log("ERROR", f"POST {reset_type} 失败：{str(e)}")
            return False

    def _test_reset_type(self, reset_type):
        """测试单个 ResetType：POST + 状态校验

        :return: (level, detail) — level ∈ {"PASS", "WARNING", "FAIL", "UNSUPPORTED"}
        """
        CommonFunction.print_log("INFO", f"开始测试：{reset_type}")
        try:
            # AllowableValues 预检（issue/9 评审确定）：BMC 显式声明白名单且不含
            # 该 ResetType 时直接跳过（不发无效 POST）；未声明则走 POST 400 兜底。
            # 注意：运维团队要求的重启策略（ForceOff/On/GracefulShutdown/ForceRestart/
            # ForcePowerCycle/PowerCycle）一个都不能少——预检仅影响"是否执行"，
            # 不删用例；BMC 未声明的类型仍会尝试 POST（由 BMC 实际行为裁决）。
            supported = self._get_supported_reset_types()
            if supported and reset_type not in supported:
                CommonFunction.print_log(
                    "WARNING",
                    f"{reset_type} 不在 BMC 声明的 ResetType 白名单 {supported} 中，"
                    f"跳过执行（记 UNSUPPORTED，不影响整体结果；该操作为运维必需用法，"
                    f"请推动厂商确认是否应支持）"
                )
                return self.UNSUPPORTED, f"不在 AllowableValues 白名单 {supported}"

            do_result = self._do_reset(reset_type)
            if do_result == self.UNSUPPORTED:
                return self.UNSUPPORTED, "POST 返回 HTTP 400（ResetType 参数不合法）"
            if not do_result:
                return "FAIL", "POST 失败"

            if reset_type == "ForceOff":
                ok = self._wait_for_power_state("Off")
                level = "PASS" if ok else "FAIL"
                detail = "" if ok else f"等待 PowerState=Off 超时（{self.POWER_STATE_TIMEOUT}s）"

            elif reset_type == "On":
                ok = self._wait_for_power_state("On")
                if not ok:
                    level, detail = "FAIL", f"等待 PowerState=On 超时（{self.POWER_STATE_TIMEOUT}s）"
                else:
                    level, elapsed = self._wait_ready_graded(require_power_on=False)
                    detail = f"OS 就绪耗时 {elapsed}s"

            elif reset_type == "GracefulShutdown":
                ok = self._wait_for_power_state("Off")
                level = "PASS" if ok else "FAIL"
                detail = "" if ok else f"等待 PowerState=Off 超时（{self.POWER_STATE_TIMEOUT}s）"
                if not ok:
                    # 诊断增强（issue/9，思路源自上游 PR #14）：GracefulShutdown 依赖
                    # OS 侧配合（登录会话/服务未退出时 OS 会拒绝关机）。超时后探测
                    # OS 是否仍可达，帮助定位是 OS 拒绝关机还是 BMC 假成功——
                    # 仅诊断，不改判定（超时判 FAIL 本身正确，OS 问题转交 OS 团队）
                    os_alive = self._ping_inband()
                    diag = ("OS 仍可达（疑似 OS 拒绝关机，如有登录会话/未退出的服务请先退出后重试，"
                            "OS 侧问题转交 OS 团队）" if os_alive
                            else "OS 不可达（疑似 BMC 已受理但状态上报异常，需 BMC 侧排查）")
                    detail = f"{detail}；诊断：{diag}"
                    CommonFunction.print_log("WARNING", f"GracefulShutdown 超时诊断：{diag}")

            elif reset_type in ("ForceRestart", "ForcePowerCycle", "PowerCycle"):
                if not self._wait_restart_begin():
                    level, detail = "FAIL", "未检测到重启开始迹象，reset 指令可能未生效"
                else:
                    level, elapsed = self._wait_ready_graded(require_power_on=True)
                    detail = f"重启完成耗时 {elapsed}s"

            else:
                CommonFunction.print_log("ERROR", f"未知 ResetType：{reset_type}，跳过")
                return "FAIL", "未知 ResetType"

            CommonFunction.print_log(
                "INFO" if level != "FAIL" else "ERROR", f"{reset_type} 测试结果：{level}（{detail}）"
            )
            return level, detail

        except Exception as e:
            CommonFunction.print_log("ERROR", f"{reset_type} 测试异常：{str(e)}")
            return "FAIL", str(e)

    def _ensure_server_on(self):
        """确保服务器处于开机且 OS 就绪状态"""
        state = self._get_power_state()
        if state == "On" and self._ping_inband():
            CommonFunction.print_log("INFO", "服务器已开机且 OS 就绪，无需操作")
            return True
        if state != "On":
            CommonFunction.print_log("INFO", f"服务器当前 {state}，发送 On 开机")
            # is not True 防御写法（issue/9，配套 UNSUPPORTED 第三态）：
            # _do_reset 返回 UNSUPPORTED（BMC 不支持 On，极罕见）时同样视为
            # 前置失败，不能因 truthy 判断静默跳过
            if self._do_reset("On") is not True:
                return False
            if not self._wait_for_power_state("On"):
                return False
        level, _ = self._wait_ready_graded(require_power_on=False)
        return level != "FAIL"

    # ── 测试主体 ───────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        self._init_client()
        if not self.prepare_system_client():
            return
        self._init_inband_ip()

        # 前置条件：确保开机
        CommonFunction.print_log("INFO", "检查服务器初始状态")
        if not self._ensure_server_on():
            CommonFunction.print_log("ERROR", "无法确认服务器处于开机状态，测试中止")
            self.command_check_result = "FAIL"
            test.add_key_value_to_json(
                self.result_json_path, "summary",
                value={"metrics": self.TEST_NAME, "value": "FAIL"}
            )
            return

        result_list = []
        overall = "PASS"

        for i, (reset_type, description) in enumerate(self.TEST_SEQUENCE, 1):
            CommonFunction.print_log("INFO", f"电源操作测试第 {i} 步：{description}（{reset_type}）")
            level, detail = self._test_reset_type(reset_type)
            result_list.append((reset_type, level, detail))

            # 结果映射：FAIL → FAIL；PASS/WARNING → PASS（WARNING 仅提示耗时超通算标准）；
            # UNSUPPORTED → PASS（机型差异不是 BMC 缺陷，不影响整体结果，汇总时单独提示）
            csv_result = "FAIL" if level == "FAIL" else "PASS"
            with open(self.result_csv_path, "a", encoding="utf-8") as f:
                f.write(f"{reset_type},{csv_result},{level},{detail}\n")
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": reset_type, "value": {"result": csv_result, "level": level, "detail": detail}}
            )

            if level == "FAIL":
                overall = "FAIL"
                # 真正判定 FAIL（超硬超时仍未就绪，或电源状态翻转超时/指令未生效）后，
                # 才强制关机使服务器回到已知状态，避免下一步指令叠加；
                # WARNING（慢但正常）/ UNSUPPORTED（机型差异）不触发此破坏性操作。
                current = self._get_power_state()
                if current != "Off":
                    CommonFunction.print_log("WARNING", f"第 {i} 步 FAIL 后服务器仍为 {current}，发送 ForceOff 复位")
                    self._do_reset("ForceOff")
                    self._wait_for_power_state("Off")

        self.command_check_result = overall
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"test_result,{overall}\n")
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": overall}
        )

        warn_steps = [rt for rt, lv, _ in result_list if lv == "WARNING"]
        unsupported_steps = [rt for rt, lv, _ in result_list if lv == self.UNSUPPORTED]
        if overall == "PASS" and not warn_steps and not unsupported_steps:
            CommonFunction.print_log("INFO", f"所有电源操作测试通过（共 {len(result_list)} 步）")
        elif overall == "PASS" and warn_steps:
            CommonFunction.print_log(
                "WARNING",
                f"所有电源操作测试通过，但以下步骤 OS 就绪耗时超过通算机型标准 "
                f"{self.WARNING_THRESHOLD_SEC}s（未超硬超时 {self.HARD_TIMEOUT_SEC}s），"
                f"需人工关注是否符合被测机型预期：{warn_steps}"
            )
        else:
            failed = [rt for rt, lv, _ in result_list if lv == "FAIL"]
            CommonFunction.print_log("ERROR", f"以下电源操作测试失败：{failed}")

        if unsupported_steps:
            # UNSUPPORTED 单独提示（不影响整体结果）：这些是运维必需的重启策略，
            # BMC 未支持需推动厂商确认（issue/9 评审要求：用例一个都不能少）
            CommonFunction.print_log(
                "WARNING",
                f"以下 ResetType BMC 未支持（已跳过执行，不影响整体结果；"
                f"均为运维必需用法，请推动厂商确认是否应支持）：{unsupported_steps}"
            )

        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{overall}")

# ── 主程序 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = PowerControlTest("PowerControlTest")
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
