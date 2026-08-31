"""
bmc_event_test_base.py — Event / Protocol suite 公共基类

Event suite（event_001~010）和 Protocol suite（protocol_001）与其他 suite 有以下差异：
  1. 使用标准 Python logging 模块（self.log），不使用 CommonFunction.print_log
  2. command_check_result 默认为 "FAIL"（主动写 PASS 才标记通过）
  3. 结果只写 JSON，不写 CSV
  4. 配置文件路径风格：conf/bmc/<suite>/<config_file>
  5. _add_args 参数均为 required=True（-i/-u/-p 必填）

使用方式
--------
from func.bmc_event_test_base import BmcEventTestBase

class EventXxx(BmcEventTestBase):
    # 类属性必填
    CASE_KEY    = "EventXxxKey"          # JSON 配置文件中的顶层 key
    TEST_NAME   = "xxx 测试"             # 默认测试名（配置文件可覆盖）
    TEST_NUM    = "Redfish_Event_00x"    # 默认用例编号
    LOG_BASE_NAME = "event_00x_xxx"      # 默认日志文件名前缀
    CONFIG_FILE = "event_test.json"      # 配置文件名（相对于 conf/bmc/<suite>/）
    CONF_DIR    = "event"                # 配置文件所在子目录名（event / protocol）

    def run_test(self):
        ...
        self._write_result(final, findings)

if __name__ == '__main__':
    ...（标准 __main__ 块）
"""

import argparse
import json
import logging
import os
import sys

from func.common_function import CommonFunction
from func.info_check_function import load_config
from func.bmc_diag import attach_sdk_logger, diagnose as _diagnose


class BmcEventTestBase:
    """Event / Protocol suite 公共基类（使用 logging 模块）。

    子类需定义以下类属性：

    Attributes
    ----------
    CASE_KEY      : str  — JSON 配置顶层 key
    TEST_NAME     : str  — 默认测试名（配置文件可覆盖）
    TEST_NUM      : str  — 默认用例编号（配置文件可覆盖）
    LOG_BASE_NAME : str  — 默认日志/结果文件名前缀（配置文件可覆盖）
    CONFIG_FILE   : str  — 配置文件名
    CONF_DIR      : str  — 配置文件所在子目录（"event" 或 "protocol"）
    """

    CASE_KEY:      str = ""
    TEST_NAME:     str = ""
    TEST_NUM:      str = ""
    LOG_BASE_NAME: str = ""
    CONFIG_FILE:   str = ""
    CONF_DIR:      str = "event"

    def __init__(self) -> None:
        self.CUR_DIR = os.getcwd()
        self.command_check_result = "FAIL"
        self.client = None
        self._add_args()
        self._load_config()
        self._initialize_log_system()

    # ── 命令行参数解析 ────────────────────────────────────────────────────────

    def _add_args(self) -> None:
        """解析 -i / -u / -p 三个必填参数（parse_known_args，忽略多余参数）。"""
        p = argparse.ArgumentParser(description=self.__class__.TEST_NAME)
        p.add_argument("-i", "--bmc_ip",    required=True)
        p.add_argument("-u", "--user_name", required=True)
        p.add_argument("-p", "--password",  required=True)
        args, _ = p.parse_known_args()
        self.BMC_IP   = args.bmc_ip
        self.USERNAME = args.user_name
        self.PASSWORD = args.password

    # ── 配置文件加载 ──────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        """加载 JSON 配置文件公共字段，回调 _load_extra_config() 供子类扩展。"""
        config_path = os.path.join(
            self.CUR_DIR, "conf/bmc", self.__class__.CONF_DIR, self.__class__.CONFIG_FILE
        )
        cfg = load_config(config_path)
        c   = cfg[self.__class__.CASE_KEY]
        self.TEST_NAME     = c.get("TestName",    self.__class__.TEST_NAME)
        self.TEST_NUM      = c.get("TestNum",      self.__class__.TEST_NUM)
        self.LOG_BASE_NAME = c.get("LogBasename",  self.__class__.LOG_BASE_NAME)
        self._load_extra_config(c)

    def _load_extra_config(self, conf: dict) -> None:
        """子类重写此方法加载额外配置参数，默认无操作。"""

    # ── 日志系统初始化 ────────────────────────────────────────────────────────

    def _initialize_log_system(self) -> None:
        """创建 log/result 目录，初始化 logging，写空 JSON。

        同时通过 bmc_diag.attach_sdk_logger() 挂载 redfish_sdk.http_client 的
        DEBUG handler，将所有 HTTP 请求（URL/payload/状态码）自动写入 log 文件。
        """
        self.LOG_DIR    = os.path.join(self.CUR_DIR, f"log/bmc/{self.LOG_BASE_NAME}")
        self.RESULT_DIR = os.path.join(self.CUR_DIR, f"result/bmc/{self.LOG_BASE_NAME}")
        os.makedirs(self.LOG_DIR,    exist_ok=True)
        os.makedirs(self.RESULT_DIR, exist_ok=True)
        self.result_json_path = os.path.join(self.RESULT_DIR, f"{self.LOG_BASE_NAME}.json")
        self.exit_code_path   = os.path.join(self.RESULT_DIR, "exit_code")
        with open(self.result_json_path, "w", encoding="utf-8") as f:
            f.write("{}")
        log_path = os.path.join(self.LOG_DIR, f"{self.LOG_BASE_NAME}.log")
        # 使用 attach_sdk_logger 统一初始化，自动接管 redfish_sdk HTTP 请求日志
        self.log = attach_sdk_logger(log_path, logger_name=self.LOG_BASE_NAME)
        CommonFunction.print_log(
            "INFO",
            f"━━ 测试开始 ━━ {self.TEST_NAME}（{self.TEST_NUM}）BMC={self.BMC_IP} User={self.USERNAME}",
        )

    def diagnose(
        self,
        exc: Exception = None,
        context: str = "",
        status_code: int = None,
        response_body=None,
    ) -> str:
        """在 WARNING / ERROR 发生时输出初步问题定位提示（封装 bmc_diag.diagnose）。"""
        return _diagnose(
            exc=exc,
            context=context,
            status_code=status_code,
            response_body=response_body,
            logger=self.log,
        )

    # ── 结果写入 ──────────────────────────────────────────────────────────────

    def _write_result(self, final: str, findings: list) -> None:
        """将 findings 列表和 summary 写入 JSON 结果文件。

        Parameters
        ----------
        final    : "PASS" 或 "FAIL"
        findings : list[dict]，每项含 "item"/"result"/"detail"
        """
        fails  = [f for f in findings if f["result"] == "FAIL"]
        passes = [f for f in findings if f["result"] == "PASS"]
        result = {
            "detail":  {
                "findings": findings,
                "stats":    {
                    "total": len(findings),
                    "pass":  len(passes),
                    "fail":  len(fails),
                },
            },
            "summary": {"metrics": self.TEST_NAME, "value": final},
        }
        with open(self.result_json_path, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
