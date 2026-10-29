#!/bin/python
"""
Author: Fengmian
Date: 2026/10/28
Usage: python3 bmc/systems_015_secure_boot_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/10/28: 新增，SecureBoot 安全启动测试（issue/19，SDK 1.3.0 接口覆盖计划 A 档）
  覆盖 SDK 接口：get_secure_boot() / update_secure_boot() / reset_secure_boot_keys()

测试内容：
  1. 【查询】读取 SecureBoot 资源（SecureBootEnable 当前状态）
  2. 【默认值校验】记录初始状态（不作硬性要求，仅存档——安全基线是否默认开启
     由交付规范决定，不同机型可能不同）
  3. 【写操作】切换 SecureBootEnable（开→关 或 关→开），读回验证生效
  4. 【恢复】恢复初始状态，读回验证
  5. 【ResetKeys 探测】检查 ResetKeys Action 的可发现性（只探测不执行——
     重置密钥属破坏性操作，会导致已部署的密钥对失效，默认不执行；
     如需验证可配置 ResetKeysType 执行）

前置条件：
  - System 资源广告 SecureBoot 链接（部分机型/配置未启用时资源不存在，
    此时记 WARNING 跳过而非 FAIL——SecureBoot 为可选组件）

PASS 标准：
  - SecureBoot 资源存在：查询/切换/恢复全部成功
  - 资源不存在：WARNING 跳过（不计 FAIL）
FAIL 标准：
  - 资源存在但切换/恢复失败
"""

import os
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

from redfish_sdk import RedfishClient, RedfishException


class SecureBootTest(BmcTestBase):
    """Systems SecureBoot 安全启动测试

    用例编号：Redfish_Systems_015
    检查项：
    - SecureBoot 资源可查询（SecureBootEnable 状态存档）
    - SecureBootEnable 切换生效（开↔关）
    - 初始状态恢复
    - ResetKeys Action 可发现性（只探测不执行）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_015_secure_boot_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        # 切换后的目标状态：None = 自动取反（当前开则切关，反之亦然）
        self.TARGET_STATE = conf_section.get("TargetState")  # True/False/None
        # ResetKeys 验证模式：probe（只探测，默认）/ execute（实际执行，破坏性）
        self.RESET_KEYS_MODE = conf_section.get("ResetKeysMode", "probe")
        # 实际执行 ResetKeys 时使用的类型（须经 BMC AllowableValues 校验）
        self.RESET_KEYS_TYPE = conf_section.get("ResetKeysType", "ResetAll")

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

        checks = []   # (label, passed: bool|None)  None=跳过
        sb_info = {}
        final = None

        try:
            self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)
            if not self.prepare_system_client():
                return

            # ── 1. 查询 SecureBoot 资源 ─────────────────────────────────────
            test.print_log("INFO", "Step 1：查询 SecureBoot 资源")
            try:
                sb = self.client.get_secure_boot()
            except RedfishException as e:
                # 404 = System 未广告 SecureBoot 链接（可选组件未启用）
                if getattr(e, "status_code", None) == 404:
                    test.print_log("WARNING",
                        "System 未广告 SecureBoot 资源（可选组件未启用），跳过测试（WARNING 不 FAIL）")
                    checks.append(("SecureBoot 资源存在", None))
                    sb_info["existence"] = "not_advertised"
                    final = "WARNING"
                    self._write_result(final, checks, sb_info)
                    return
                raise

            if sb is None:
                test.print_log("WARNING",
                    "get_secure_boot() 返回 None（System 未广告 SecureBoot 链接），跳过测试")
                checks.append(("SecureBoot 资源存在", None))
                sb_info["existence"] = "not_advertised"
                final = "WARNING"
                self._write_result(final, checks, sb_info)
                return

            checks.append(("SecureBoot 资源存在", True))
            sb_info["existence"] = "advertised"

            # ── 2. 读取当前状态（存档，不作硬性默认值要求）──────────────────
            original_enabled = sb.secure_boot_enable
            test.print_log("INFO", f"SecureBootEnable 当前状态：{original_enabled}")
            sb_info["original"] = {"SecureBootEnable": original_enabled}
            checks.append(("SecureBootEnable 状态读取", original_enabled is not None))

            if original_enabled is None:
                test.print_log("ERROR",
                    "SecureBootEnable 为 None（BMC 未上报安全启动状态），无法执行切换测试")
                self._finalize(test, checks, sb_info, "FAIL")
                return

            # ── 3. 切换 SecureBootEnable ────────────────────────────────────
            # 目标状态：配置指定优先，否则取反
            target = self.TARGET_STATE if self.TARGET_STATE is not None else (not original_enabled)
            test.print_log("INFO", f"Step 2：切换 SecureBootEnable → {target}")
            try:
                self.client.update_secure_boot(enabled=target)
                checks.append((f"SecureBootEnable 切换 PATCH（→{target}）", True))
            except RedfishException as e:
                # 部分 BMC 要求 SecureBoot 关闭状态下才能改其他设置，或处于
                # Pending 状态拒绝切换——如实记录
                test.print_log("ERROR", f"SecureBootEnable 切换失败：{str(e)[:150]}")
                checks.append((f"SecureBootEnable 切换 PATCH（→{target}）", False))
                self._finalize(test, checks, sb_info, "FAIL")
                return

            time.sleep(2)
            sb = self.client.get_secure_boot()
            after_switch = sb.secure_boot_enable
            switch_ok = (after_switch == target)
            test.print_log("INFO" if switch_ok else "ERROR",
                f"切换后读回：SecureBootEnable={after_switch}（期望 {target}）")
            checks.append(("SecureBootEnable 切换读回验证", switch_ok))
            sb_info["after_switch"] = {"SecureBootEnable": after_switch}

            # ── 4. ResetKeys Action 可发现性 ────────────────────────────────
            test.print_log("INFO", "Step 3：探测 ResetKeys Action 可发现性")
            actions = getattr(sb, "actions", None)
            reset_keys = getattr(actions, "reset_keys", None) if actions else None
            if reset_keys is not None:
                target_uri = getattr(reset_keys, "target", None)
                allowable = getattr(reset_keys, "reset_keys_type_allowable_values", None) \
                    or getattr(reset_keys, "model_extra", {}).get("ResetKeysType@Redfish.AllowableValues")
                test.print_log("INFO",
                    f"  ✓ ResetKeys Action 可发现（target={target_uri}，"
                    f"AllowableValues={allowable}）")
                checks.append(("ResetKeys Action 可发现", True))
                sb_info["reset_keys"] = {"target": target_uri, "allowable_values": allowable}

                # 实际执行属破坏性操作（密钥对失效），默认只探测
                if self.RESET_KEYS_MODE == "execute":
                    test.print_log("WARNING",
                        f"ResetKeysMode=execute：执行 ResetKeys（{self.RESET_KEYS_TYPE}）——"
                        f"已部署密钥对将失效！")
                    try:
                        self.client.reset_secure_boot_keys(self.RESET_KEYS_TYPE)
                        checks.append((f"ResetKeys 执行（{self.RESET_KEYS_TYPE}）", True))
                    except RedfishException as e:
                        test.print_log("ERROR", f"ResetKeys 执行失败：{str(e)[:150]}")
                        checks.append((f"ResetKeys 执行（{self.RESET_KEYS_TYPE}）", False))
                else:
                    test.print_log("INFO",
                        "  ResetKeysMode=probe：仅探测不执行（重置密钥属破坏性操作，"
                        "如需验证请在 conf 配置 ResetKeysMode=execute）")
                    checks.append(("ResetKeys 执行", None))  # None=跳过
            else:
                test.print_log("WARNING",
                    "  未发现 ResetKeys Action（部分 BMC 不广告该 Action，记 WARNING 不 FAIL）")
                checks.append(("ResetKeys Action 可发现", None))  # None=跳过
                sb_info["reset_keys"] = {"target": None}

            # ── 5. 恢复初始状态 ─────────────────────────────────────────────
            test.print_log("INFO", f"Step 4：恢复初始状态（SecureBootEnable → {original_enabled}）")
            try:
                self.client.update_secure_boot(enabled=original_enabled)
                time.sleep(2)
                sb = self.client.get_secure_boot()
                restored = sb.secure_boot_enable
                restore_ok = (restored == original_enabled)
                test.print_log("INFO" if restore_ok else "ERROR",
                    f"恢复后读回：SecureBootEnable={restored}（期望 {original_enabled}）")
                checks.append(("SecureBootEnable 恢复初始状态", restore_ok))
                sb_info["after_restore"] = {"SecureBootEnable": restored}
            except RedfishException as e:
                test.print_log("ERROR", f"恢复初始状态失败：{str(e)[:150]}")
                checks.append(("SecureBootEnable 恢复初始状态", False))

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

        self._write_result(final, checks, sb_info)

    def _finalize(self, test, checks, sb_info, final):
        """提前退出时的结果写入"""
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass
        self._write_result(final, checks, sb_info)

    def _write_result(self, final, checks, sb_info):
        test = CommonFunction()
        for label, passed in checks:
            value = "SKIP" if passed is None else ("PASS" if passed else "FAIL")
            test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                       value={"metrics": label, "value": value})
        test.add_key_value_to_json(self.result_json_path, "detail.cycle",
                                   value={"metrics": "secure_boot_info", "value": sb_info})
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
        test_name = SecureBootTest("SecureBootTest")
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
