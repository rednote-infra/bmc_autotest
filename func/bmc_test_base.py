"""
bmc_test_base.py — Redfish / IPMI suite 公共基类

所有需要标准 _add_args / _load_config / _initialize_log_system 三段式初始化的
测试类（Redfish、IPMI、Event、Account、Chassis、Session、Systems、Managers 等）
均可继承本基类，消除重复样板代码。

使用方式
--------
from func.bmc_test_base import BmcTestBase

class MyTest(BmcTestBase):
    # config_path: JSON 配置文件绝对路径
    # case:        配置文件中的 case key
    # csv_header:  覆写 CSV 表头（默认 "case,result"）
    def __init__(self, case: str):
        super().__init__(
            case=case,
            config_path=os.path.join(os.getcwd(), "conf/bmc/xxx/xxx.json"),
        )

    # 可选：子类重写，加载额外配置参数（在 _load_config 之后、_initialize_log_system 之前调用）
    def _load_extra_config(self, conf_section: dict) -> None:
        self.MY_PARAM = conf_section.get("MyParam", "default")

    def run_test(self):
        ...

注意事项
--------
- 基类统一使用 parse_known_args()；若脚本原来用 parse_args() 会静默忽略多余参数。
- LOG_BASE_NAME / result_json_path / result_csv_path / exit_code_path 命名与
  大多数 Redfish suite 保持一致；迁移 Web suite 时同步改用这套命名。
- CSV 表头默认 "case,result\\n"；子类可在 __init__ 里传入 csv_header 覆盖。
- IPMI suite 等有额外参数的子类，重写 _load_extra_config(conf_section) 即可。
"""

import argparse
import logging
import os
import sys
from typing import Optional

from func.common_function import CommonFunction
from func.info_check_function import load_config
from func.bmc_diag import attach_sdk_logger, diagnose as _diagnose


class BmcTestBase:
    """所有 Redfish / IPMI suite 的公共基类。

    Parameters
    ----------
    case : str
        JSON 配置文件中的顶层 key，对应当前测试用例。
    config_path : str
        JSON 配置文件的绝对路径。
    csv_header : str, optional
        CSV 结果文件的表头行（含换行符）。
        默认为 ``"case,result\\n"``。
    """

    # 子类可在类级别覆盖，避免每次传参
    CSV_HEADER: str = "case,result\n"

    def __init__(self, case: str, config_path: str, csv_header: Optional[str] = None):
        self.case        = case
        self.CUR_DIR     = os.getcwd()
        self.CONFIG_PATH = config_path
        self.command_check_result = "PASS"
        self.client      = None

        if csv_header is not None:
            self._csv_header = csv_header
        else:
            self._csv_header = self.__class__.CSV_HEADER

        self._add_args()
        self._load_config()
        self._initialize_log_system()
        self._init_sdk_client()

    # ── 命令行参数解析 ───────────────────────────────────────────────────────

    def _add_args(self) -> None:
        """解析 -i / -u / -p 三个标准参数（parse_known_args，忽略多余参数）。"""
        parser = argparse.ArgumentParser(description="BMC 自动化测试脚本")
        parser.add_argument("-i", "--bmc_ip",    type=str, help="BMC IP 地址")
        parser.add_argument("-u", "--user_name", type=str, help="BMC 用户名")
        parser.add_argument("-p", "--password",  type=str, help="BMC 密码")
        args, _ = parser.parse_known_args()
        self.BMC_IP   = args.bmc_ip
        self.USERNAME = args.user_name
        self.PASSWORD = args.password

    # ── 配置文件加载 ─────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        """加载公共配置字段并回调 _load_extra_config() 供子类扩展。"""
        CommonFunction.print_log("DEBUG", "开始加载配置文件")
        conf     = load_config(self.CONFIG_PATH)
        c        = conf[self.case]
        self.TEST_NAME     = c["TestName"]
        self.TEST_NUM      = c["TestNum"]
        self.LOG_BASE_NAME = c["LogBasename"]
        if self.BMC_IP   is None: self.BMC_IP   = c.get("BMC_IP")
        if self.USERNAME is None: self.USERNAME = c.get("UserName")
        if self.PASSWORD is None: self.PASSWORD = c.get("PassWord")
        self._load_extra_config(c)
        CommonFunction.print_log("DEBUG", "加载配置文件完成")

    def _load_extra_config(self, conf_section: dict) -> None:
        """子类重写此方法加载额外配置参数，默认无操作。

        Parameters
        ----------
        conf_section : dict
            已经取出的 ``conf[self.case]`` 字典，可直接 .get() 使用。
        """

    def _init_sdk_client(self) -> None:
        """子类重写此方法初始化 SDK 客户端，默认无操作。

        在 ``__init__`` 末尾（日志系统初始化之后）自动调用，确保
        ``self.client`` 在 ``run_test()`` 执行前已完成赋值。
        子类示例::

            def _init_sdk_client(self) -> None:
                from sdk.redfish_client import RedfishClient
                self.client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD)
        """

    # ── 日志 / 结果文件系统初始化 ────────────────────────────────────────────

    def _initialize_log_system(self) -> None:
        """创建 log/result 目录，清理旧 .stdout/.stderr，写入 CSV/JSON 初始内容。

        同时通过 bmc_diag.attach_sdk_logger() 挂载 redfish_sdk.http_client 的
        DEBUG handler，将所有 HTTP 请求（方法/URL/payload/状态码/响应体）自动写入
        <LOG_BASE_NAME>.log 文件，便于 DEBUG 缺陷。
        日志文件层级：
          log/bmc/<LOG_BASE_NAME>/<LOG_BASE_NAME>.log  — DEBUG 全量（含 SDK 请求细节）
          stdout                                        — INFO 及以上
        """
        CommonFunction.print_log("DEBUG", "开始初始化测试文件系统")
        self.LOG_DIR    = os.path.join(self.CUR_DIR, f"log/bmc/{self.LOG_BASE_NAME}")
        self.RESULT_DIR = os.path.join(self.CUR_DIR, f"result/bmc/{self.LOG_BASE_NAME}")
        os.makedirs(self.LOG_DIR,    exist_ok=True)
        os.makedirs(self.RESULT_DIR, exist_ok=True)

        self.stdout_path      = os.path.join(self.LOG_DIR,    self.LOG_BASE_NAME + ".stdout")
        self.stderr_path      = os.path.join(self.LOG_DIR,    self.LOG_BASE_NAME + ".stderr")
        self.result_json_path = os.path.join(self.RESULT_DIR, self.LOG_BASE_NAME + ".json")
        self.result_csv_path  = os.path.join(self.RESULT_DIR, self.LOG_BASE_NAME + ".csv")
        self.exit_code_path   = os.path.join(self.RESULT_DIR, "exit_code")

        # 清理上次运行的 stdout / stderr
        for fname in os.listdir(self.LOG_DIR):
            if fname.endswith(".stdout") or fname.endswith(".stderr"):
                os.remove(os.path.join(self.LOG_DIR, fname))

        with open(self.result_csv_path, "w", encoding="utf-8") as f:
            f.write(self._csv_header)
        with open(self.result_json_path, "w", encoding="utf-8") as f:
            f.write("{}")

        # ── 接入 SDK HTTP 请求日志 ────────────────────────────────────────────
        # attach_sdk_logger 会同时挂载：
        #   1. 主 logger（self.log）— 供子类直接使用 self.log.info/warning/error
        #   2. redfish_sdk.http_client logger — 自动捕获所有 HTTP 请求细节到同一 log 文件
        log_file = os.path.join(self.LOG_DIR, self.LOG_BASE_NAME + ".log")
        self.log = attach_sdk_logger(log_file, logger_name=self.LOG_BASE_NAME)
        CommonFunction.print_log(
            "INFO",
            f"━━ 测试开始 ━━ {self.TEST_NAME}（{self.TEST_NUM}）BMC={self.BMC_IP} User={self.USERNAME}",
        )

        CommonFunction.print_log("DEBUG", "测试文件系统初始化完成")

    def diagnose(
        self,
        exc: Exception = None,
        context: str = "",
        status_code: int = None,
        response_body=None,
    ) -> str:
        """在 WARNING / ERROR 发生时输出初步问题定位提示。

        封装 bmc_diag.diagnose()，自动传入 self.log，方便子类直接调用：
            self.diagnose(exc, context="GET /redfish/v1/Chassis/1/Drives")

        Returns
        -------
        str — 所有诊断提示的合并字符串（也会写入 self.log.warning）
        """
        return _diagnose(
            exc=exc,
            context=context,
            status_code=status_code,
            response_body=response_body,
            logger=self.log,
        )
