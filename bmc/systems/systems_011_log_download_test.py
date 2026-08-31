#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/systems_011_log_download_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/22: 新增

校验策略：

  【标准接口】（Redfish 2019+ DiagnosticData，SDK 暂未封装）
  POST /redfish/v1/Managers/1/LogServices/{log_id}/Actions/LogService.CollectDiagnosticData
    Body: {"DiagnosticDataType": "Manager"}
    2xx → PASS
    非2xx/404 → 标准接口 FAIL，尝试定制接口

  【定制接口兜底】（由厂商提供 URL 和 Body，配置在 conf/bmc/systems/systems_011_log_download_test.json）
  Custom.Enabled=true 时：
    POST Custom.Url（Body=Custom.Body）
    2xx → WARNING（标准不合规）+ PASS
    非2xx → FAIL

  PASS 条件：标准接口 2xx，或定制接口 2xx（附 WARNING）
  FAIL 条件：标准失败且无定制配置，或标准+定制均失败

  注：此策略适用于所有 SDK 未封装且厂商 OEM 定制的 Redfish 资源测试，
      新厂商适配只需修改配置文件，代码不变。
"""

import os
import time
import sys
import traceback

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.common_function import CommonFunction
from func.bmc_test_base import BmcTestBase
from func.info_check_function import load_config

from redfish_sdk import RedfishClient, RedfishException


def _load_full_config(config_path: str) -> dict:
    """加载完整配置文件（用于厂商动态匹配）。"""
    return load_config(config_path)

class LogDownloadTest(BmcTestBase):
    """Systems 一键日志下载测试

    用例编号：Redfish_Systems_011
    检查项：验证 BMC 一键日志接口可调通（标准接口优先，定制接口兜底）
    """

    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/systems/systems_011_log_download_test.json"),
        )

    def _load_extra_config(self, conf_section: dict) -> None:
        """厂商动态匹配：先连接 BMC 获取 Manufacturer，再按厂商选配置 case。"""
        conf = _load_full_config(self.CONFIG_PATH)
        bmc_ip   = self.BMC_IP   or conf_section.get("BMC_IP", "")
        username = self.USERNAME or conf_section.get("UserName", "")
        password = self.PASSWORD or conf_section.get("PassWord", "")
        manufacturer = self._get_manufacturer(bmc_ip, username, password)
        if manufacturer:
            for case_name, case_conf in conf.items():
                if isinstance(case_conf, dict) and case_conf.get("Custom", {}).get("Manufacturer", "") == manufacturer:
                    CommonFunction.print_log("DEBUG", f"根据 Manufacturer '{manufacturer}' 自动匹配到配置 case：{case_name}")
                    self.case = case_name
                    conf_section = conf[case_name]
                    # 更新公共字段
                    self.TEST_NAME     = conf_section["TestName"]
                    self.TEST_NUM      = conf_section["TestNum"]
                    self.LOG_BASE_NAME = conf_section["LogBasename"]
                    break
        # 标准接口配置
        self.STANDARD_URL  = conf_section["StandardUrl"]
        self.STANDARD_BODY = conf_section.get("StandardBody", {})
        # 定制接口配置（厂商 OEM）
        custom = conf_section.get("Custom", {})
        self.CUSTOM_ENABLED = custom.get("Enabled", False)
        self.CUSTOM_URL     = custom.get("Url", "")
        self.CUSTOM_BODY    = custom.get("Body", {})
        self.CUSTOM_COMMENT = custom.get("Comment", "")

    def _get_manufacturer(self, bmc_ip, username, password):
        """连接 BMC，通过 get_manufacturer() 获取厂商名"""
        try:
            tmp_client = RedfishClient(bmc_ip, username, password)
            manufacturer = tmp_client.get_manufacturer().strip()
            CommonFunction.print_log("DEBUG", f"BMC {bmc_ip} Manufacturer：{manufacturer}")
            return manufacturer
        except Exception as e:
            CommonFunction.print_log("WARNING", f"获取 Manufacturer 失败：{e}，将使用默认 case")
            return ""

    def close_sdk_client(self):
        if self.client:
            try:
                self.client.close()
            except Exception:
                pass

    # ── 核心方法 ──────────────────────────────────────────────────────────────

    def _init_client(self):
        CommonFunction.print_log("INFO", f"连接 BMC：{self.BMC_IP}")
        self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)

    def _try_post(self, url, body, label):
        """POST 指定接口，返回 (success: bool, http_code_hint: str)"""
        CommonFunction.print_log("INFO", f"[{label}] POST {url}，Body={body}")
        try:
            self.client.post(url, body)
            CommonFunction.print_log("INFO", f"[{label}] 调用成功（2xx）")
            return True
        except RedfishException as e:
            CommonFunction.print_log("INFO", f"[{label}] 调用失败：{str(e)[:200]}")
            return False
        except Exception as e:
            CommonFunction.print_log("ERROR", f"[{label}] 未预期异常：{type(e).__name__}: {str(e)[:200]}")
            return False

    # ── 测试主体 ───────────────────────────────────────────────────────────────

    def run_test(self):
        test = CommonFunction()
        test.print_log("INFO", f"测试用例名称：{self.TEST_NAME}，测试用例编号：{self.TEST_NUM}")
        test.print_log("INFO", "测试开始")
        test.print_log("INFO",
            "注：redfish_python_sdk 暂未封装一键日志接口，当前使用 client.post 直接调用。"
            "标准接口优先，定制接口兜底（2xx=PASS+WARNING，均失败=FAIL）。")

        self._init_client()

        # ── Step 1：标准接口 ────────────────────────────────────────────────
        CommonFunction.print_log("INFO", f"Step 1：尝试标准 Redfish 一键日志接口")
        standard_ok = self._try_post(self.STANDARD_URL, self.STANDARD_BODY, "标准接口")

        if standard_ok:
            overall = "PASS"
            CommonFunction.print_log("INFO", "标准接口调用成功，BMC 一键日志功能合规")
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": "standard_interface", "value": "PASS"}
            )
            with open(self.result_csv_path, "a", encoding="utf-8") as f:
                f.write("standard_interface,PASS\n")

        else:
            CommonFunction.print_log(
                "ERROR",
                f"标准接口调用失败（{self.STANDARD_URL}），该 BMC 不符合 Redfish 标准一键日志规范"
            )
            test.add_key_value_to_json(
                self.result_json_path, "detail.cycle",
                value={"metrics": "standard_interface", "value": "FAIL"}
            )
            with open(self.result_csv_path, "a", encoding="utf-8") as f:
                f.write("standard_interface,FAIL\n")

            # ── Step 2：定制接口兜底 ────────────────────────────────────────
            if not self.CUSTOM_ENABLED or not self.CUSTOM_URL:
                CommonFunction.print_log(
                    "ERROR",
                    "未配置厂商定制接口（Custom.Enabled=false 或 Custom.Url 为空），无法兜底，测试 FAIL"
                )
                overall = "FAIL"

            else:
                CommonFunction.print_log(
                    "INFO",
                    f"Step 2：尝试厂商定制接口（{self.CUSTOM_COMMENT or self.CUSTOM_URL}）"
                )
                CommonFunction.print_log(
                    "WARNING",
                    f"redfish_python_sdk 暂未适配此厂商定制接口，当前绕过 SDK 直接调用，请推动 SDK 业务方跟进适配"
                )
                custom_ok = self._try_post(self.CUSTOM_URL, self.CUSTOM_BODY, "定制接口")

                if custom_ok:
                    CommonFunction.print_log(
                        "WARNING",
                        f"定制接口调用成功，但该 BMC 不支持标准 Redfish 一键日志接口，不符合规范，"
                        f"请推动厂商实现标准接口（{self.STANDARD_URL}）"
                    )
                    overall = "PASS"
                    test.add_key_value_to_json(
                        self.result_json_path, "detail.cycle",
                        value={"metrics": "custom_interface", "value": "PASS"}
                    )
                    with open(self.result_csv_path, "a", encoding="utf-8") as f:
                        f.write("custom_interface,PASS\n")
                else:
                    CommonFunction.print_log(
                        "ERROR",
                        "定制接口调用也失败，BMC 一键日志功能无法验证，测试 FAIL"
                    )
                    overall = "FAIL"
                    test.add_key_value_to_json(
                        self.result_json_path, "detail.cycle",
                        value={"metrics": "custom_interface", "value": "FAIL"}
                    )
                    with open(self.result_csv_path, "a", encoding="utf-8") as f:
                        f.write("custom_interface,FAIL\n")

        # ── 写汇总结果 ──────────────────────────────────────────────────────
        self.command_check_result = overall
        with open(self.result_csv_path, "a", encoding="utf-8") as f:
            f.write(f"test_result,{overall}\n")
        test.add_key_value_to_json(
            self.result_json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": overall}
        )
        test.print_log("INFO", f"{self.TEST_NAME}测试完成，结果：{overall}")

# ── 主程序 ───────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    exit_code = 1
    checker = None
    try:
        checker = LogDownloadTest("LogDownloadTest")
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
        if checker:
            try:
                checker.close_sdk_client()
            except Exception:
                pass
            try:
                with open(checker.exit_code_path, "w", encoding="utf-8") as f:
                    f.write(str(exit_code))
            except Exception as e:
                CommonFunction.print_log("ERROR", f"写入退出码文件失败: {str(e)}")
    sys.exit(exit_code)
