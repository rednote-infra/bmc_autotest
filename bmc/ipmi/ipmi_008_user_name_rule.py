#!/bin/python
"""
IPMI 带外用户名规则校验（正反向边界）（RDSV_BMC_080a）

正向：4位/16位/全大写/全小写/混合 → 应成功
反向：17位 → 应失败（ZTE报 Username is too long）
注意：需求要求4~16位，ZTE实测3位及以下BMC未拦截，记录为WARNING

Usage: python3 bmc/ipmi_user_003.py -i <bmc_ip> -u <user> -p <pass>
"""

import os, subprocess, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase


class IpmiUserNameRule(BmcWebTestBase):
    CASE        = "IpmiUserNameRule"
    CONFIG_FILE = "ipmi_008_user_name_rule.json"
    CONF_DIR    = "ipmi"   # 配置文件位于 conf/bmc/ipmi/，覆盖基类默认的 "web"
    CSV_HEADER  = "case_type,name,desc,expected,actual_rc,result\n"

    def __init__(self) -> None:
        super().__init__()

    def _load_extra_config(self, conf: dict) -> None:
        self.IFACE = conf.get("IpmiInterface", "lanplus");  self.TIMEOUT = conf.get("QueryTimeoutSec", 30)
        self.CHANNEL = conf.get("QueryChannel", 1);  self.WAIT = conf.get("WaitAfterOpSec", 1)
        self.UID = conf["TestUserId"]
        self.POS_CASES          = conf.get("PositiveCases", [])
        self.NEG_MUST_FAIL      = conf.get("NegativeMustFail", [])      # 未拦截 → FAIL
        self.NEG_WARN_IF_PASS   = conf.get("NegativeWarnIfPass", [])    # 未拦截 → WARNING（不影响总结果）
        self.CLEANUP_NAME = conf.get("CleanupName", f"unused_{conf['TestUserId']}")
        self.CLEANUP_PRIV = conf.get("CleanupPriv", 15)

    def _ipmi(self, sub):
        cmd = ["ipmitool", "-I", self.IFACE, "-H", self.BMC_IP, "-U", self.USERNAME, "-P", self.PASSWORD] + sub
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.TIMEOUT)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"

    def run(self):
        test = CommonFunction()
        rows = [];  all_pass = True

        # 正向：set name 应成功（rc=0）
        for case in self.POS_CASES:
            name = case["name"];  desc = case["desc"]
            rc, out, err = self._ipmi(["user", "set", "name", str(self.UID), name])
            ok = rc == 0
            result = "PASS" if ok else "FAIL"
            if not ok: all_pass = False
            CommonFunction.print_log("INFO" if ok else "ERROR", f"[正向] {desc} name={name} → {result} (rc={rc})")
            rows.append(("positive", name, desc, "pass", rc, result))
            time.sleep(self.WAIT)

        # 反向（必须拦截）：set name 应失败（rc!=0），未拦截 → FAIL
        for case in self.NEG_MUST_FAIL:
            name = case["name"];  desc = case["desc"]
            rc, out, err = self._ipmi(["user", "set", "name", str(self.UID), name])
            rejected = rc != 0
            result = "PASS" if rejected else "FAIL"
            if not rejected:
                all_pass = False
                CommonFunction.print_log("ERROR", f"[反向-必须拦截] {desc} name={name} → BMC未拦截 → FAIL")
            else:
                CommonFunction.print_log("INFO", f"[反向-必须拦截] {desc} name={name} → 已被BMC拒绝 ✓")
            rows.append(("neg_must_fail", name, desc, "fail", rc, result))
            time.sleep(self.WAIT)

        # 反向（宽松）：set name 应失败，未拦截 → WARNING（不影响总体 PASS/FAIL）
        for case in self.NEG_WARN_IF_PASS:
            name = case["name"];  desc = case["desc"]
            rc, out, err = self._ipmi(["user", "set", "name", str(self.UID), name])
            rejected = rc != 0
            result = "PASS" if rejected else "WARNING"
            if not rejected:
                CommonFunction.print_log("WARNING", f"[反向-宽松] {desc} name={name} → BMC未拦截，记WARNING（不影响总结果）")
            else:
                CommonFunction.print_log("INFO", f"[反向-宽松] {desc} name={name} → 已被BMC拒绝 ✓")
            rows.append(("neg_warn_if_pass", name, desc, "fail", rc, result))
            time.sleep(self.WAIT)

        # cleanup
        self._ipmi(["user", "set", "name", str(self.UID), self.CLEANUP_NAME])
        self._ipmi(["user", "priv", str(self.UID), str(self.CLEANUP_PRIV), str(self.CHANNEL)])
        self._ipmi(["user", "disable", str(self.UID)])

        self.command_check_result = "PASS" if all_pass else "FAIL"
        with open(self.csv_path, "a") as f:
            for row in rows:
                f.write(",".join(f'"{v}"' for v in row) + "\n")
        for row in rows:
            test.add_key_value_to_json(self.json_path, "detail.cycle",
                value={"metrics": f'{row[0]}_{row[1]}', "value": {"desc": row[2], "result": row[5]}})
        test.add_key_value_to_json(self.json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result})
        test.print_log("INFO", f"{self.TEST_NAME} 完成，结果：{self.command_check_result}")

if __name__ == "__main__":
    obj = None
    exit_code = 1
    try:
        obj = IpmiUserNameRule();  obj.run()
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
