#!/bin/python
"""
Author: Fengmian
Date: 2026/05/13
Usage: python3 bmc/ipmi_boot_001.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/05/13: 新增

IPMI 带外系统启动项设置测试

测试步骤：
  1. 逐一设置启动设备（chassis bootdev <dev> [options=efiboot]）
  2. 执行 chassis bootparam get 5 查询实际生效的启动参数
  3. 校验返回内容与期望关键字匹配
  4. 最终恢复默认（bootdev none，取消 override）

校验策略：
  PASS：所有测试启动项均设置成功，且 bootparam get 5 返回包含期望关键字
  FAIL：任一设置失败 / bootparam 返回内容不匹配

支持的启动项（配置文件 TestBootDevices）：
  pxe  options=efiboot → "Force PXE"
  disk               → "Force Disk"
  bios               → "Force BIOS"
  none               → "No override"（恢复默认）
"""

import os
import time
import re
import subprocess
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase

class IpmiBootOrder(BmcTestBase):
    """IPMI 带外系统启动项设置测试

    用例编号：IPMI_Boot_001
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/ipmi/ipmi_005_boot_order.json"),
        )
    def _load_extra_config(self, conf_section: dict) -> None:
        self.IPMI_IFACE     = conf_section.get("IpmiInterface", "lanplus")
        self.QUERY_TIMEOUT  = conf_section.get("QueryTimeoutSec", 30)
        self.WAIT_AFTER_SET = conf_section.get("WaitAfterSetSec", 2)
        self.TEST_BOOT_DEVS = conf_section.get("TestBootDevices", [])

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

    def boot_check(self) -> tuple:
        results = []
        all_pass = True

        for entry in self.TEST_BOOT_DEVS:
            dev         = entry.get("dev", "")
            options     = entry.get("options", "")
            expect_kw   = entry.get("expect_keyword", "")
            description = entry.get("description", dev)

            # 设置启动项
            sub_cmd = ["chassis", "bootdev", dev]
            if options:
                sub_cmd.append(f"options={options}")

            CommonFunction.print_log("INFO", f"设置启动项：{dev} {options}（{description}）")
            rc, stdout, stderr = self._run_ipmi(sub_cmd, self.QUERY_TIMEOUT)
            set_ok = rc == 0 and "Set Boot Device" in stdout
            set_result = "PASS" if set_ok else "FAIL"

            if not set_ok:
                all_pass = False
                CommonFunction.print_log("ERROR", f"bootdev {dev} 设置失败，rc={rc}，stdout={stdout}，stderr={stderr}")
                results.append({
                    "boot_dev": dev, "description": description,
                    "set_result": set_result, "verify_result": "SKIP",
                    "check_result": "FAIL", "detail": stderr or stdout,
                })
                continue

            CommonFunction.print_log("INFO", f"bootdev {dev} 设置成功：{stdout}")
            time.sleep(self.WAIT_AFTER_SET)

            # 验证 bootparam get 5
            expect_byte2 = entry.get("expect_data_byte2", "")
            rc2, stdout2, _ = self._run_ipmi(["chassis", "bootparam", "get", "5"], self.QUERY_TIMEOUT)
            if rc2 != 0:
                CommonFunction.print_log("ERROR", f"bootparam get 5 失败，rc={rc2}")
                verify_result = "FAIL"
                all_pass = False
            else:
                # 优先关键字匹配
                kw_match = expect_kw and expect_kw.lower() in stdout2.lower()
                # 降级：解析 "Boot parameter data: XXXXXXXXXX"，取第2字节（索引2~3）
                byte2_match = False
                if not kw_match and expect_byte2:
                    m = re.search(r"Boot parameter data:\s*([0-9a-fA-F]+)", stdout2)
                    if m:
                        raw_data = m.group(1)
                        actual_byte2 = raw_data[2:4].lower() if len(raw_data) >= 4 else ""
                        byte2_match = (actual_byte2 == expect_byte2.lower())
                        if byte2_match:
                            CommonFunction.print_log("INFO",
                                f"bootparam 关键字未匹配，但 data 第2字节='{actual_byte2}' 符合期望='{expect_byte2}' → PASS")
                        else:
                            CommonFunction.print_log("ERROR",
                                f"bootparam 验证失败：关键字='{expect_kw}' 未匹配，data 第2字节='{actual_byte2}' 期望='{expect_byte2}'，实际返回：{stdout2[:200]}")
                    else:
                        CommonFunction.print_log("ERROR",
                            f"bootparam 验证失败：未找到 'Boot parameter data'，实际返回：{stdout2[:200]}")

                if kw_match:
                    CommonFunction.print_log("INFO", f"bootparam 验证通过，含关键字 '{expect_kw}'")
                    verify_result = "PASS"
                elif byte2_match:
                    verify_result = "PASS"
                else:
                    if not expect_byte2:
                        CommonFunction.print_log("ERROR",
                            f"bootparam 验证失败：期望关键字 '{expect_kw}'，实际返回：{stdout2[:200]}")
                    verify_result = "FAIL"
                    all_pass = False

            check_result = "PASS" if (set_ok and verify_result == "PASS") else "FAIL"
            results.append({
                "boot_dev":     dev,
                "description":  description,
                "set_result":   set_result,
                "verify_result": verify_result,
                "check_result": check_result,
                "detail":       stdout2[:300] if rc2 == 0 else "",
            })

        final = "PASS" if all_pass else "FAIL"
        CommonFunction.print_log("INFO" if final == "PASS" else "ERROR", f"启动项测试完成，结果：{final}")
        return final, results

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")

        check_result, results = self.boot_check()
        self.command_check_result = check_result

        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            for r in results:
                f.write(
                    f'"{r["boot_dev"]}",'
                    f'"{r["description"]}",'
                    f'"{r["set_result"]}",'
                    f'"{r["verify_result"]}",'
                    f'"{r["check_result"]}"\n'
                )

        for r in results:
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": f'bootdev_{r["boot_dev"]}', "value": r}
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
        checker = IpmiBootOrder("IpmiBootOrder")
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
