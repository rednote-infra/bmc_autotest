#!/bin/python
"""
Author: Fengmian
Date: 2026/05/13
Usage: python3 bmc/ipmi_power_002.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/13: 新增

IPMI 带外整机通电开机策略设置测试

测试步骤：
  1. 查询支持的通电策略列表（chassis policy list）
  2. 逐一设置测试策略（always-off / always-on / previous）并校验返回成功
  3. 最终恢复为默认策略（previous = 恢复上次状态）

校验策略：
  PASS：chassis policy list 成功 + 三种策略均可设置成功
  FAIL：任一策略设置失败

注意：仅验证设置命令成功，不验证掉电重启后实际行为（避免中断测试环境）
"""

import os
import subprocess
import sys
import time
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

class IpmiPowerRestorePolicy(BmcTestBase):
    """IPMI 带外整机通电开机策略设置测试

    用例编号：IPMI_Power_002
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/ipmi/ipmi_002_power_restore_policy.json"),
        )
    def _load_extra_config(self, conf_section: dict) -> None:
        self.IPMI_IFACE         = conf_section.get("IpmiInterface", "lanplus")
        self.QUERY_TIMEOUT      = conf_section.get("QueryTimeoutSec", 30)
        self.WAIT_AFTER_SET     = conf_section.get("WaitAfterSetSec", 3)
        self.DEFAULT_POLICY     = conf_section.get("ExpectedDefaultPolicy", "previous")
        self.TEST_POLICIES      = conf_section.get("TestPolicies", ["always-off", "always-on", "previous"])

    def _run_ipmi(self, sub_cmd: list, timeout: int = 30) -> tuple:
        cmd = [
            "ipmitool", "-I", self.IPMI_IFACE,
            "-H", self.BMC_IP,
            "-U", self.USERNAME,
            "-P", self.PASSWORD,
        ] + sub_cmd
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", f"命令执行超时（>{timeout}s）"
        except Exception as e:
            return -1, "", str(e)

    def policy_check(self) -> tuple:
        results = []
        all_pass = True

        # 1. 查询支持的策略列表
        CommonFunction.print_log("INFO", "查询支持的通电策略（chassis policy list）")
        rc, stdout, stderr = self._run_ipmi(["chassis", "policy", "list"], self.QUERY_TIMEOUT)
        if rc != 0:
            CommonFunction.print_log("ERROR", f"chassis policy list 失败，rc={rc}，stderr={stderr}")
            all_pass = False
            results.append({"check_item": "policy_list", "policy": "", "result": "FAIL", "detail": stderr})
        else:
            CommonFunction.print_log("INFO", f"支持的策略：{stdout}")
            supported = stdout.lower()
            results.append({"check_item": "policy_list", "policy": stdout, "result": "PASS", "detail": stdout})

            # 2. 逐一测试每个策略
            for policy in self.TEST_POLICIES:
                if policy.lower() not in supported:
                    CommonFunction.print_log("WARNING", f"策略 {policy} 不在支持列表中，跳过")
                    results.append({"check_item": "policy_set", "policy": policy, "result": "WARNING", "detail": "not in supported list"})
                    continue

                CommonFunction.print_log("INFO", f"设置通电策略：{policy}")
                rc2, stdout2, stderr2 = self._run_ipmi(["chassis", "policy", policy], self.QUERY_TIMEOUT)
                success = rc2 == 0 and "Set chassis power restore policy" in stdout2
                result = "PASS" if success else "FAIL"
                if not success:
                    all_pass = False
                    CommonFunction.print_log("ERROR", f"策略 {policy} 设置失败，rc={rc2}，stdout={stdout2}，stderr={stderr2}")
                else:
                    CommonFunction.print_log("INFO", f"策略 {policy} 设置成功：{stdout2}")
                results.append({"check_item": "policy_set", "policy": policy, "result": result, "detail": stdout2 or stderr2})
                time.sleep(self.WAIT_AFTER_SET)

        # 3. 恢复默认策略
        CommonFunction.print_log("INFO", f"恢复默认策略：{self.DEFAULT_POLICY}")
        rc3, stdout3, _ = self._run_ipmi(["chassis", "policy", self.DEFAULT_POLICY], self.QUERY_TIMEOUT)
        restore_ok = rc3 == 0 and "Set chassis power restore policy" in stdout3
        if restore_ok:
            CommonFunction.print_log("INFO", f"默认策略已恢复：{stdout3}")
        else:
            CommonFunction.print_log("WARNING", f"默认策略恢复失败，需手动恢复为 {self.DEFAULT_POLICY}")
        results.append({"check_item": "policy_restore", "policy": self.DEFAULT_POLICY, "result": "PASS" if restore_ok else "WARNING", "detail": stdout3})

        final = "PASS" if all_pass else "FAIL"
        return final, results

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, results = self.policy_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for r in results:
                f.write(f'"{r["check_item"]}","{r["policy"]}","{r["result"]}","{r["detail"]}"\n')

        for r in results:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'{r["check_item"]}_{r["policy"]}', "value": r}
            )
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result}
        )
        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{self.command_check_result}")

if __name__ == "__main__":
    exit_code = 0
    checker = None
    try:
        checker = IpmiPowerRestorePolicy("IpmiPowerRestorePolicy")
        checker.run_test()
        exit_code = 0 if checker.command_check_result == "PASS" else 2
        time.sleep(5)
    except KeyboardInterrupt:
        CommonFunction.print_log("ERROR", "键盘中断")
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", f"未处理异常: {e}")
        traceback.print_exc()
        exit_code = 1
    finally:
        if checker:
            try:
                with open(checker.exit_code_path, "w") as f:
                    f.write(str(exit_code))
            except Exception as e:
                CommonFunction.print_log("ERROR", f"写入退出码失败: {e}")
    sys.exit(exit_code)
