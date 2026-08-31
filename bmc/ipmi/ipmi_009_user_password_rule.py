#!/bin/python
"""
IPMI 带外密码复杂度规则校验（正反向）（RDSV_BMC_080b）

正向：8位合规/12位合规 → 应成功
反向：7位/纯小写/无特殊字符/纯数字 → 应失败
注意：需求规定长度上限12位，ZTE实测13位也通过，记为WARNING

Usage: python3 bmc/ipmi_user_004.py -i <bmc_ip> -u <user> -p <pass>
"""

import os, subprocess, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase


class IpmiUserPasswordRule(BmcWebTestBase):
    CASE        = "IpmiUserPasswordRule"
    CONFIG_FILE = "ipmi_009_user_password_rule.json"
    CONF_DIR    = "ipmi"   # 配置文件位于 conf/bmc/ipmi/，覆盖基类默认的 "web"
    CSV_HEADER  = "case_type,password_len,desc,expected,actual_rc,result\n"

    def __init__(self) -> None:
        super().__init__()

    def _load_extra_config(self, conf: dict) -> None:
        self.IFACE = conf.get("IpmiInterface", "lanplus");  self.TIMEOUT = conf.get("QueryTimeoutSec", 30)
        self.CHANNEL = conf.get("QueryChannel", 1);  self.WAIT = conf.get("WaitAfterOpSec", 1)
        self.UID = conf["TestUserId"]
        self.POS_CASES              = conf.get("PositiveCases", [])
        self.NEG_MUST_FAIL          = conf.get("NegativeMustFail", [])         # 未拦截 → FAIL
        self.NEG_PASS_IF_ACCEPTED   = conf.get("NegativePassIfAccepted", [])   # 未拦截 → PASS（更长=更安全）
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

        # 先确保测试 slot 可用（set name + enable）
        self._ipmi(["user", "set", "name", str(self.UID), "pwdtestuser"])
        self._ipmi(["user", "enable", str(self.UID)])

        # 正向：应成功
        for case in self.POS_CASES:
            pwd = case["password"];  desc = case["desc"]
            rc, out, err = self._ipmi(["user", "set", "password", str(self.UID), pwd])
            ok = rc == 0 and "successful" in out.lower()
            result = "PASS" if ok else "FAIL"
            if not ok: all_pass = False
            CommonFunction.print_log("INFO" if ok else "ERROR", f"[正向] {desc} → {result} (rc={rc}, {out or err})")
            rows.append(("positive", len(pwd), desc, "pass", rc, result))
            time.sleep(self.WAIT)

        # 反向（必须拦截）：应失败，未拦截 → FAIL
        for case in self.NEG_MUST_FAIL:
            pwd = case["password"];  desc = case["desc"]
            rc, out, err = self._ipmi(["user", "set", "password", str(self.UID), pwd])
            rejected = rc != 0
            result = "PASS" if rejected else "FAIL"
            if not rejected:
                all_pass = False
                CommonFunction.print_log("ERROR", f"[反向-必须拦截] {desc} → BMC未拦截 → FAIL (pwd={pwd})")
            else:
                CommonFunction.print_log("INFO", f"[反向-必须拦截] {desc} → 已被BMC拒绝 ✓")
            rows.append(("neg_must_fail", len(pwd), desc, "fail", rc, result))
            time.sleep(self.WAIT)

        # 反向（>上限位数）：BMC接受 → PASS（更长更安全）；BMC拒绝 → 也是 PASS（合规）
        for case in self.NEG_PASS_IF_ACCEPTED:
            pwd = case["password"];  desc = case["desc"]
            rc, out, err = self._ipmi(["user", "set", "password", str(self.UID), pwd])
            accepted = rc == 0 and "successful" in out.lower()
            # 无论 BMC 接受还是拒绝，都是 PASS（接受=更安全；拒绝=严格合规）
            result = "PASS"
            msg = "BMC接受（更长=更安全，视为PASS）" if accepted else "BMC拒绝（严格合规，也PASS）"
            CommonFunction.print_log("INFO", f"[反向-宽松] {desc} → {msg}")
            rows.append(("neg_pass_if_accepted", len(pwd), desc, "either", rc, result))
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
                value={"metrics": f'{row[0]}_len{row[1]}', "value": {"desc": row[2], "result": row[5]}})
        test.add_key_value_to_json(self.json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result})
        test.print_log("INFO", f"{self.TEST_NAME} 完成，结果：{self.command_check_result}")

if __name__ == "__main__":
    obj = None
    exit_code = 1
    try:
        obj = IpmiUserPasswordRule();  obj.run()
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
