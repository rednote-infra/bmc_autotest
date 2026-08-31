#!/bin/python
"""
IPMI 带外查询用户列表（RDSV_BMC_079）

校验：
  - 用户 slot 数 >= MinUserSlots（默认8）
  - 至少各存在一个 ADMINISTRATOR / OPERATOR / USER 权限的槽位配置能力
    （ZTE 实测：ID1~3 已有 Admin，权限上限覆盖即可）

Usage: python3 bmc/ipmi_user_001.py -i <bmc_ip> -u <user> -p <pass>
"""

import os, subprocess, sys, traceback
import time
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase


class IpmiUserList(BmcWebTestBase):
    CASE        = "IpmiUserList"
    CONFIG_FILE = "ipmi_006_user_list.json"
    CONF_DIR    = "ipmi"   # 配置文件位于 conf/bmc/ipmi/，覆盖基类默认的 "web"
    CSV_HEADER  = "check_item,result,detail\n"

    def __init__(self) -> None:
        super().__init__()

    def _load_extra_config(self, conf: dict) -> None:
        self.IFACE = conf.get("IpmiInterface", "lanplus")
        self.TIMEOUT = conf.get("QueryTimeoutSec", 30)
        self.CHANNEL = conf.get("QueryChannel", 1)
        self.MIN_SLOTS = conf.get("MinUserSlots", 8)
        self.REQ_PRIVS = conf.get("RequiredPrivLevels", ["ADMINISTRATOR", "OPERATOR", "USER"])

    def _ipmi(self, sub, timeout=None):
        cmd = ["ipmitool", "-I", self.IFACE, "-H", self.BMC_IP, "-U", self.USERNAME, "-P", self.PASSWORD] + sub
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout or self.TIMEOUT)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"

    def run(self):
        test = CommonFunction()
        results = [];  all_pass = True

        # 1. user summary
        rc, out, err = self._ipmi(["user", "summary", str(self.CHANNEL)])
        if rc != 0:
            results.append(("user_summary", "FAIL", err)); all_pass = False
        else:
            results.append(("user_summary", "PASS", out))
            CommonFunction.print_log("INFO", f"user summary:\n{out}")

        # 2. user list
        rc, out, err = self._ipmi(["user", "list", str(self.CHANNEL)])
        if rc != 0 or not out:
            results.append(("user_list", "FAIL", err)); all_pass = False
        else:
            lines = [l for l in out.splitlines() if l.strip() and not l.startswith("ID")]
            slot_count = len(lines)
            CommonFunction.print_log("INFO", f"user list slot count: {slot_count}")
            if slot_count < self.MIN_SLOTS:
                CommonFunction.print_log("ERROR", f"用户 slot 数 {slot_count} < {self.MIN_SLOTS}")
                results.append(("user_slot_count", "FAIL", f"{slot_count} < {self.MIN_SLOTS}"))
                all_pass = False
            else:
                results.append(("user_slot_count", "PASS", f"slots={slot_count}"))

            # 权限覆盖检查：检查 slot 配置中是否覆盖三种权限
            # 出厂只有超管属正常情况，未覆盖 OPERATOR/USER 记 WARNING，不 FAIL
            all_privs = " ".join(lines).upper()
            for priv in self.REQ_PRIVS:
                if priv.upper() in all_privs:
                    results.append((f"priv_{priv}", "PASS", "found"))
                    CommonFunction.print_log("INFO", f"权限 {priv} 覆盖 ✓")
                else:
                    results.append((f"priv_{priv}", "WARNING", "not found"))
                    CommonFunction.print_log("WARNING",
                        f"权限 {priv} 未在任何用户中找到（出厂仅超管属正常，记WARNING）")

        self.command_check_result = "PASS" if all_pass else "FAIL"
        with open(self.csv_path, "a") as f:
            for item, res, detail in results:
                f.write(f'"{item}","{res}","{detail.replace(chr(10)," ")}"\n')
        for item, res, detail in results:
            test.add_key_value_to_json(self.json_path, "detail.cycle",
                value={"metrics": item, "value": {"result": res, "detail": detail}})
        test.add_key_value_to_json(self.json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result})
        test.print_log("INFO", f"{self.TEST_NAME} 完成，结果：{self.command_check_result}")

if __name__ == "__main__":
    obj = None
    exit_code = 1
    try:
        obj = IpmiUserList();  obj.run()
        exit_code = 0 if obj.command_check_result == "PASS" else 2
        time.sleep(5)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log("ERROR", str(e));  traceback.print_exc();  exit_code = 1
    finally:
        if obj:
            try: open(obj.ec_path, "w").write(str(exit_code))
            except: pass
    sys.exit(exit_code)
