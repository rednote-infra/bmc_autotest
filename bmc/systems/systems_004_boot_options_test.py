#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/systems_004_boot_options_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/22: 新增
2026/06/23: 新模型改写为 redfish_sdk v1.1.0（get_boot_options/get_boot_option/set_boot_option_enabled）

校验策略：

  【旧模型 - BootSourceOverride】（SDK 原生支持）
  Boot.allowable_values 非空时走此路径：
  1. GET /redfish/v1/Systems/1，读取 BootSourceOverrideTarget 当前值和 AllowableValues
  2. 遍历每个 AllowableValue，通过 SDK change_boot_source() PATCH，回读验证
  3. 测试后恢复原始值

  【新模型 - BootOptions 集合】（v1.1.0 get_boot_options/set_boot_option_enabled）
  Boot.allowable_values 为空但存在 BootOptions 集合时走此路径：
  1. get_boot_options() 读取所有启动项成员（List[BootOption]）
  2. 遍历每个成员，set_boot_option_enabled() 切换（toggle→恢复），用 get_boot_option() 独立 GET 回读验证
  3. 只读实现（PATCH 404/405）记 SKIP，推动厂商补全

  PASS 条件：所有启动项操作回读均与期望一致（新旧模型均计 PASS）
  FAIL 条件：任意操作失败，或两种模型均无法获取启动项列表
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException, RedfishValidationError  # noqa: F401

# 必要启动项：切换失败 → ERROR + 计入 FAIL
REQUIRED_BOOT_TARGETS = {"None", "Pxe", "Hdd", "Cd", "BiosSetup"}

class BootOptionsTest(BmcTestBase):
    """Systems BootOptions 操作测试

    用例编号：Redfish_Systems_004
    检查项：动态读取 AllowableValues，遍历所有合法启动项并 PATCH 回读验证，测试后恢复原始值
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_004_boot_options_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        self.BOOT_MODE           = conf_section.get("BootMode",             "UEFI")
        self.BOOT_ENABLED        = conf_section.get("BootEnabled",          "Once")
        self.RESTORE_TARGET      = conf_section.get("RestoreTarget",        "None")  # "None" 表示恢复测试前值
        self.READ_BACK_DELAY     = conf_section.get("ReadBackDelaySeconds", 2)

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

    # ── 工具方法 ──────────────────────────────────────────────────────────────

    def _init_client(self):
        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

    def _detect_model(self):
        """检测 BMC 使用的启动项模型

        :return: (model, context)
          model="legacy"  → context=(current_target, allowable_values)
          model="new"     → context=boot_options_odata_id（集合 URL）
          model=None      → 两种模型均无法获取，测试中止
        """
        try:
            system = self.client.get_system()
            boot = system.boot
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"读取 System 信息失败：{str(e)}")
            return None, None

        # 旧模型：AllowableValues 内联在 Boot 块
        allowable = getattr(boot, "allowable_values", None) or []
        if allowable:
            current = getattr(boot, "boot_source_override_target", None)
            CommonFunction.print_log("INFO", f"检测到旧模型（BootSourceOverride），AllowableValues={allowable}")
            return "legacy", (current, allowable)

        # 新模型：BootOptions 集合资源（v1.1.0 get_boot_options）
        try:
            boot_options = self.client.get_boot_options()
        except RedfishException as e:
            CommonFunction.print_log("WARNING", f"get_boot_options() 调用失败：{str(e)}")
            boot_options = []
        if boot_options:
            CommonFunction.print_log(
                "INFO",
                f"检测到新模型（BootOptions 集合，共 {len(boot_options)} 项）"
            )
            return "new", boot_options

        CommonFunction.print_log("ERROR", "无法识别启动项模型：AllowableValues 为空且无 BootOptions 集合")
        return None, None

    # ── 旧模型（BootSourceOverride）─────────────────────────────────────────

    def _legacy_set_and_verify(self, target):
        """旧模型：PATCH BootSourceOverrideTarget 并回读验证。

        :return: "PASS" / "FAIL"（必要字段）/ "WARN_FAIL"（非必要字段失败，只 WARNING）
        """
        is_required = target in REQUIRED_BOOT_TARGETS
        log_level_fail = "ERROR" if is_required else "WARNING"
        CommonFunction.print_log("INFO", f"[旧模型] 切换启动项 → {target}")
        try:
            self.client.change_boot_source(
                target=target,
                mode=self.BOOT_MODE,
                enabled=self.BOOT_ENABLED,
            )
        except RedfishValidationError as e:
            CommonFunction.print_log(log_level_fail, f"PATCH {target} 被 SDK 校验拒绝：{str(e)}")
            return "FAIL" if is_required else "WARN_FAIL"
        except RedfishException as e:
            CommonFunction.print_log(log_level_fail, f"PATCH {target} 失败：{str(e)}")
            return "FAIL" if is_required else "WARN_FAIL"

        time.sleep(self.READ_BACK_DELAY)

        try:
            actual = getattr(self.client.get_system().boot, "boot_source_override_target", None)
        except RedfishException as e:
            CommonFunction.print_log(log_level_fail, f"回读失败：{str(e)}")
            return "FAIL" if is_required else "WARN_FAIL"

        if actual == target:
            CommonFunction.print_log("INFO", f"回读验证通过：BootSourceOverrideTarget={actual}")
            return "PASS"
        CommonFunction.print_log(log_level_fail, f"回读验证失败：期望={target}，实际={actual}")
        return "FAIL" if is_required else "WARN_FAIL"

    def _legacy_restore(self, restore_target):
        """旧模型：恢复启动项"""
        if not restore_target:
            return
        CommonFunction.print_log("INFO", f"[旧模型] 恢复启动项 → {restore_target}")
        try:
            self.client.change_boot_source(
                target=restore_target,
                mode=self.BOOT_MODE,
                enabled=self.BOOT_ENABLED,
            )
            CommonFunction.print_log("INFO", "启动项恢复完成")
        except RedfishException as e:
            CommonFunction.print_log("WARNING", f"恢复启动项失败：{str(e)}，请手动检查")

    def _run_legacy(self, current_target, allowable_values):
        """旧模型测试主流程，返回 (result_list, overall)。

        必要字段（REQUIRED_BOOT_TARGETS）失败 → overall=FAIL；
        非必要字段失败 → 记 WARNING，overall 不受影响。
        """
        CommonFunction.print_log("INFO", f"当前启动项：{current_target}，合法值列表：{allowable_values}")
        restore_target = (
            current_target
            if (not self.RESTORE_TARGET or self.RESTORE_TARGET == "None")
            else self.RESTORE_TARGET
        )
        result_list = []
        overall = "PASS"
        warn_fail_targets = []
        for target in allowable_values:
            result = self._legacy_set_and_verify(target)
            if result == "WARN_FAIL":
                # 非必要字段失败：日志已打 WARNING，记录但不计入 overall
                result_list.append((target, "WARNING"))
                warn_fail_targets.append(target)
            else:
                result_list.append((target, result))
                if result == "FAIL":
                    overall = "FAIL"
        if warn_fail_targets:
            CommonFunction.print_log(
                "WARNING",
                f"以下非必要启动项切换失败（不计入测试结果）：{warn_fail_targets}"
            )
        self._legacy_restore(restore_target)
        return result_list, overall

    # ── 新模型（BootOptions 集合，v1.1.0）────────────────────────────────────

    def _new_probe_writable(self, opt):
        """探测 BootOption 是否支持 PATCH（写回原值；404/405 → 只读）

        :return: True（可写）/ False（只读）
        """
        origin = opt.boot_option_enabled if opt.boot_option_enabled is not None else True
        try:
            self.client.set_boot_option_enabled(opt.id, origin)
            return True
        except RedfishException as e:
            msg = str(e)
            if "404" in msg or "405" in msg or "not found" in msg.lower() or "not allowed" in msg.lower():
                return False
            # 其他错误（如 400）视为可写但参数有误，仍返回 True 让后续正常报错
            return True

    def _new_set_and_verify(self, option_id, enabled):
        """新模型：set_boot_option_enabled 写入后用 get_boot_option(id) 独立 GET 回读验证

        独立 GET 回读（而非依赖写接口返回值）可更严格地确认 BMC 端实际生效，
        同时覆盖 get_boot_option 单资源接口。

        :param option_id: BootOption 的 ID
        :param enabled: True / False
        :return: "PASS" / "FAIL"
        """
        label = "启用" if enabled else "禁用"
        CommonFunction.print_log("INFO", f"[新模型] {label} → {option_id}")
        try:
            self.client.set_boot_option_enabled(option_id, enabled)
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"set_boot_option_enabled {option_id} 失败：{str(e)}")
            return "FAIL"

        # 独立 GET 回读（v1.1.0 get_boot_option 单资源接口）
        try:
            opt = self.client.get_boot_option(option_id)
        except RedfishException as e:
            CommonFunction.print_log("ERROR", f"get_boot_option({option_id}) 回读失败：{str(e)}")
            return "FAIL"

        actual = opt.boot_option_enabled if opt else None
        if actual == enabled:
            CommonFunction.print_log("INFO", f"回读验证通过（get_boot_option）：BootOptionEnabled={actual}")
            return "PASS"
        CommonFunction.print_log("ERROR", f"回读验证失败：期望={enabled}，实际={actual}")
        return "FAIL"

    def _run_new(self, boot_options):
        """新模型测试主流程：对每个 BootOption 执行 toggle→恢复 切换并回读，
        返回 (result_list, overall)"""
        if not boot_options:
            CommonFunction.print_log("ERROR", "BootOptions 集合为空")
            return [], "FAIL"

        CommonFunction.print_log("INFO", f"共 {len(boot_options)} 个 BootOption 成员")

        # 先探测是否支持写操作（写回首个成员原值）
        if not self._new_probe_writable(boot_options[0]):
            CommonFunction.print_log(
                "WARNING",
                "该 BMC 的 BootOptions 集合为只读实现（PATCH 返回 404/405），"
                "厂商未实现写操作接口，记为 SKIP。请推动厂商补全 BootOption PATCH 支持。"
            )
            result_list = [(opt.boot_option_reference or opt.id, "SKIP") for opt in boot_options]
            return result_list, "SKIP"

        result_list = []
        overall = "PASS"

        for opt in boot_options:
            ref = opt.boot_option_reference or opt.id
            origin_enabled = opt.boot_option_enabled
            if origin_enabled is None:
                origin_enabled = True
            CommonFunction.print_log(
                "INFO",
                f"测试 BootOption：{ref}（{opt.id}），当前 Enabled={origin_enabled}"
            )

            # 切换到相反值再切回，验证双向可写
            target_toggle = not origin_enabled
            r1 = self._new_set_and_verify(opt.id, target_toggle)
            r2 = self._new_set_and_verify(opt.id, origin_enabled)  # 恢复

            step_result = "PASS" if (r1 == "PASS" and r2 == "PASS") else "FAIL"
            result_list.append((ref, step_result))
            if step_result == "FAIL":
                overall = "FAIL"

        return result_list, overall

    # ── 测试主体 ───────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        self._init_client()

        model, context = self._detect_model()
        if model is None:
            CommonFunction.print_log("ERROR", "无法识别启动项模型，测试中止")
            self.command_check_result = "FAIL"
            test.add_key_value_to_json(
                self.result_json_path, "summary",
                value={"metrics": self.TEST_NAME, "value": "FAIL"}
            )
            return

        if model == "legacy":
            current_target, allowable_values = context
            result_list, overall = self._run_legacy(current_target, allowable_values)
        else:  # new
            result_list, overall = self._run_new(context)

        # 写结果
        for label, result in result_list:
            with open(self.result_csv_path, "a", encoding="utf-8") as f:
                f.write(f"{label},{result}\n")
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": label, "value": result}
            )

        self.command_check_result = overall
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"test_result,{overall}\n")
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": overall}
        )

        if overall == "PASS":
            CommonFunction.print_log("INFO", f"所有启动项测试通过（共 {len(result_list)} 项，模型={model}）")
        elif overall == "SKIP":
            CommonFunction.print_log("WARNING", f"启动项测试 SKIP（共 {len(result_list)} 项，模型={model}，厂商只读实现）")
        else:
            failed = [lbl for lbl, r in result_list if r == "FAIL"]
            CommonFunction.print_log("ERROR", f"以下必要启动项测试失败：{failed}")

        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{overall}")

# ── 主程序 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = BootOptionsTest("BootOptionsTest")
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
