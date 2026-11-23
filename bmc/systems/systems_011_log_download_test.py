#!/bin/python
"""
Author: Fengmian
Date: 2026/04/22
Usage: python3 bmc/systems_011_log_download_test.py -i <bmc_ip> -u <username> -p <password>
Update:
2026/04/22: 新增
2026/10/16: SDK 1.3.0 适配 - 标准接口改用 collect_and_download_diagnostic_data()
  （SDK 1.3.0 已封装），URI 由 SDK 自动发现；OEM 定制接口仍走 conf 配置（issue/8）
2026/10/26: 修复误判通过风险（思路源自上游 PR #10 / commit 84f4b2e）——
  1. 补传 output_path：SDK 下载的日志包留存到 log 目录（原落在临时目录用完即弃，
     测试无证据留存）；
  2. 下载后校验文件存在且非空（原"调用不抛异常"即 PASS，BMC 返回空包/损坏包
     时误判通过，与 web_012 同源问题）；
  3. DiagnosticDataType 默认值由硬编码 "Manager" 改为 None（SDK 对 None 使用
     厂商默认值：OEM 可用时用 OEM，否则 Manager），避免部分厂商不接受
     "Manager" 类型导致标准接口被误判失败。

校验策略：

  【标准接口】（SDK 1.3.0 collect_and_download_diagnostic_data）
  SDK 自动发现 LogService 与 CollectDiagnosticData target
    2xx → PASS
    失败 → 标准接口 FAIL，尝试定制接口

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
from func.system_selection import discover_system
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
        # 标准接口：SDK 1.3.0 collect_and_download_diagnostic_data()（URI 由 SDK 自动发现）
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
            selected = discover_system(tmp_client, self.REQUESTED_SYSTEM_ID)
            manufacturer = tmp_client.get_manufacturer(system_id=selected.system_id).strip()
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
            "注：标准接口使用 SDK 1.3.0 collect_and_download_diagnostic_data()（URI 自动发现），"
            "定制接口兜底（2xx=PASS+WARNING，均失败=FAIL）。")

        self._init_client()
        if not self.prepare_system_client():
            return

        # ── Step 1：标准接口（SDK 1.3.0 类型化接口）─────────────────────────
        # 修复说明（issue/9，思路源自上游 PR #10 / commit 84f4b2e）：
        # 1. 原调用未传 output_path——SDK 下载的日志包落在临时目录，用完即弃，
        #    测试无法留存证据，且"下载成功"无从核验；
        # 2. 原调用未校验下载文件存在且非空——BMC 返回空包/损坏包时照样 PASS
        #    （与 web_012 同源的误判通过风险）；
        # 3. DiagnosticDataType 由硬编码 "Manager" 改为 None——SDK 对 None 使用
        #    厂商默认值（OEM 可用时用 OEM，否则 Manager），避免部分厂商 BMC
        #    不接受 "Manager" 类型导致标准接口被误判失败。
        CommonFunction.print_log("INFO", "Step 1：尝试标准 Redfish 一键日志接口（SDK collect_and_download_diagnostic_data）")
        standard_ok = False
        try:
            downloaded_path = self.client.collect_and_download_diagnostic_data(
                output_path=os.path.join(self.LOG_DIR, "diagnostic_bundle.tar"),
                diagnostic_data_type=self.STANDARD_BODY.get("DiagnosticDataType"),
            )
            if not os.path.isfile(downloaded_path) or os.path.getsize(downloaded_path) == 0:
                raise RuntimeError(f"下载的日志文件不存在或为空：{downloaded_path}")
            CommonFunction.print_log("INFO",
                f"[标准接口] 采集下载成功，文件={downloaded_path}，大小={os.path.getsize(downloaded_path)} bytes")
            standard_ok = True
        except RedfishException as e:
            CommonFunction.print_log("INFO", f"[标准接口] SDK 调用失败：{str(e)[:200]}")
        except Exception as e:
            CommonFunction.print_log("ERROR", f"[标准接口] 未预期异常：{type(e).__name__}: {str(e)[:200]}")

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
                "标准接口调用失败（collect_and_download_diagnostic_data），该 BMC 不符合 Redfish 标准一键日志规范"
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
                        "定制接口调用成功，但该 BMC 不支持标准 Redfish 一键日志接口，不符合规范，"
                        "请推动厂商实现标准接口（LogService.CollectDiagnosticData）"
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
