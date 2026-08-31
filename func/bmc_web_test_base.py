"""
bmc_web_test_base.py — Web suite 公共基类

Web suite 脚本（web_001~web_016）的初始化与 Redfish/IPMI suite 有以下差异：
  1. 配置文件路径风格：conf/bmc/web/<config_file>
  2. 配置 key 名：LBN（LogBasename）、TimeoutSec
  3. CSV 表头：多列格式（check_item,result,detail 等）
  4. _init_log 不清理旧 .stdout/.stderr（Web 脚本本身不产生这两类文件）
  5. 提供 _rec() 和 _save() 公共辅助方法，各脚本无需重复实现

使用方式
--------
from func.bmc_web_test_base import BmcWebTestBase

CASE        = 'WebXxx'
CONFIG_FILE = 'web_00x_xxx.json'

class WebXxx(BmcWebTestBase):
    CASE        = 'WebXxx'
    CONFIG_FILE = 'web_00x_xxx.json'

    def run(self):
        test  = CommonFunction()
        steps = []
        all_pass = True
        ...
        all_pass = self._rec(steps, all_pass, 'item_name', ok, 'detail text')
        ...
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    ...（标准 __main__ 块）
"""

import argparse
import os
import sys

from func.common_function import CommonFunction
from func.info_check_function import load_config
from func.bmc_diag import attach_sdk_logger, diagnose as _diagnose


class BmcWebTestBase:
    """Web suite 公共基类。

    子类需定义以下类属性（或在 __init__ 时传入）：

    Attributes
    ----------
    CASE : str
        JSON 配置文件中的顶层 key。
    CONFIG_FILE : str
        配置文件名（相对于 ``conf/bmc/{CONF_DIR}/`` 目录）。
    CONF_DIR : str
        配置文件所在子目录名，默认为 ``"web"``。
        IPMI 脚本（ipmi_006~013）需在子类中设置 ``CONF_DIR = "ipmi"``
        以正确指向 ``conf/bmc/ipmi/`` 目录。
    CSV_HEADER : str
        CSV 表头行（含换行符）。默认为 ``"check_item,result,detail\\n"``。
    """

    CASE:        str = ""
    CONFIG_FILE: str = ""
    CONF_DIR:    str = "web"   # 配置文件子目录；IPMI 脚本中设置为 "ipmi"
    CSV_HEADER:  str = "check_item,result,detail\n"

    def __init__(self) -> None:
        self.CUR_DIR = os.getcwd()
        self.command_check_result = "PASS"
        self._add_args()
        self._load_config()
        self._init_log()

    # ── 命令行参数解析 ───────────────────────────────────────────────────────

    def _add_args(self) -> None:
        """解析 -i / -u / -p 三个标准参数（parse_known_args，忽略多余参数）。"""
        p = argparse.ArgumentParser(description="BMC Web 自动化测试脚本")
        p.add_argument("-i", "--bmc_ip",    type=str)
        p.add_argument("-u", "--user_name", type=str)
        p.add_argument("-p", "--password",  type=str)
        args, _ = p.parse_known_args()
        self.BMC_IP   = args.bmc_ip
        self.USERNAME = args.user_name
        self.PASSWORD = args.password

    # ── 配置文件加载 ─────────────────────────────────────────────────────────

    def _load_config(self) -> None:
        """加载公共配置字段，并回调 _load_extra_config() 供子类扩展。"""
        config_path = os.path.join(
            self.CUR_DIR, "conf/bmc", self.__class__.CONF_DIR, self.__class__.CONFIG_FILE
        )
        conf = load_config(config_path)[self.__class__.CASE]
        self.TEST_NAME = conf["TestName"]
        self.TEST_NUM  = conf["TestNum"]
        self.LOG_BASE_NAME = conf["LogBasename"]
        self.LBN = self.LOG_BASE_NAME   # 兼容 IPMI Web-style 脚本（ipmi_006~013）
        if not self.BMC_IP:   self.BMC_IP   = conf.get("BMC_IP", "")
        if not self.USERNAME: self.USERNAME = conf.get("UserName", "")
        if not self.PASSWORD: self.PASSWORD = conf.get("PassWord", "")
        self.TIMEOUT = conf.get("TimeoutSec", 10)
        self._load_extra_config(conf)

    def _load_extra_config(self, conf: dict) -> None:
        """子类重写此方法加载额外配置参数，默认无操作。

        Parameters
        ----------
        conf : dict
            已经取出的 ``conf[CASE]`` 字典，可直接 .get() 使用。
        """

    # ── 日志 / 结果文件系统初始化 ────────────────────────────────────────────

    def _init_log(self) -> None:
        """创建 log/result 目录，写入 JSON/CSV 初始内容（不清理 stdout/stderr）。

        同时通过 bmc_diag.attach_sdk_logger() 挂载 redfish_sdk.http_client 的
        DEBUG handler，将所有 HTTP 请求（URL/payload/状态码）自动写入 log 文件。
        """
        self.LOG_DIR    = os.path.join(self.CUR_DIR, f"log/bmc/{self.LOG_BASE_NAME}")
        self.RESULT_DIR = os.path.join(self.CUR_DIR, f"result/bmc/{self.LOG_BASE_NAME}")
        os.makedirs(self.LOG_DIR,    exist_ok=True)
        os.makedirs(self.RESULT_DIR, exist_ok=True)
        # 与原脚本保持相同属性名（json_path / csv_path / ec_path）
        self.json_path = os.path.join(self.RESULT_DIR, self.LOG_BASE_NAME + ".json")
        self.csv_path  = os.path.join(self.RESULT_DIR, self.LOG_BASE_NAME + ".csv")
        self.ec_path   = os.path.join(self.RESULT_DIR, "exit_code")
        open(self.json_path, "w", encoding="utf-8").write("{}")
        with open(self.csv_path, "w", encoding="utf-8") as f:
            f.write(self.__class__.CSV_HEADER)

        # ── 接入 SDK HTTP 请求日志 ────────────────────────────────────────────
        log_file = os.path.join(self.LOG_DIR, self.LOG_BASE_NAME + ".log")
        self.log = attach_sdk_logger(log_file, logger_name=self.LOG_BASE_NAME)
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

    # ── 公共辅助方法 ─────────────────────────────────────────────────────────

    def _rec(
        self,
        steps:    list,
        all_pass: bool,
        item:     str,
        ok:       bool,
        detail:   str = "",
    ) -> bool:
        """记录单个检查项，追加到 steps 列表并打印日志，返回更新后的 all_pass。

        Parameters
        ----------
        steps    : 当前 step 列表（会 in-place append）
        all_pass : 当前总体是否通过
        item     : 检查项名称
        ok       : 本项是否通过
        detail   : 附加信息字符串（会截断到 300 字符）

        Returns
        -------
        bool — 新的 all_pass（all_pass and ok）
        """
        r = "PASS" if ok else "FAIL"
        CommonFunction.print_log("INFO" if ok else "ERROR", f"[{item}] {r}: {detail}")
        steps.append((item, r, str(detail)[:300]))
        return all_pass and ok

    def _save(self, test: object, steps: list) -> None:
        """将 steps 写入 CSV 和 JSON，并写入 summary。

        Parameters
        ----------
        test  : CommonFunction 实例
        steps : 由 _rec() 积累的 (item, result, detail) 三元组列表
        """
        with open(self.csv_path, "a", encoding="utf-8") as f:
            for item, res, detail in steps:
                f.write(f'"{item}","{res}","{detail}"\n')
        for item, res, detail in steps:
            test.add_key_value_to_json(
                self.json_path, "detail.cycle",
                value={"metrics": item, "value": {"result": res, "detail": detail}},
            )
        test.add_key_value_to_json(
            self.json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result},
        )
        test.print_log("INFO", f"{self.TEST_NAME} 完成，结果：{self.command_check_result}")
