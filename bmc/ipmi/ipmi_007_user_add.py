#!/bin/python
"""
IPMI 带外增加用户（正向）（RDSV_BMC_080）

测试步骤：
  1. set name → 2. set password → 3. user priv → 4. user enable
  5. user list 验证用户存在且权限正确
  6. cleanup（改名 + 降权 + disable）

Usage: python3 bmc/ipmi_user_002.py -i <bmc_ip> -u <user> -p <pass>
"""

import os, subprocess, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase


class IpmiUserAdd(BmcWebTestBase):
    CASE        = "IpmiUserAdd"
    CONFIG_FILE = "ipmi_007_user_add.json"
    CONF_DIR    = "ipmi"   # 配置文件位于 conf/bmc/ipmi/，覆盖基类默认的 "web"
    CSV_HEADER  = "step,check_item,result,detail\n"

    def __init__(self) -> None:
        super().__init__()

    def _load_extra_config(self, conf: dict) -> None:
        self.IFACE = conf.get("IpmiInterface", "lanplus");  self.TIMEOUT = conf.get("QueryTimeoutSec", 30)
        self.CHANNEL = conf.get("QueryChannel", 1);  self.WAIT = conf.get("WaitAfterOpSec", 2)
        self.UID = conf["TestUserId"];  self.UNAME = conf["TestUserName"]
        self.UPASS = conf["TestUserPassword"];  self.UPRIV = conf["TestUserPriv"]
        self.PRIV_MAP = conf.get("PrivLevelMap", {})
        self.CLEANUP_PRIV = conf.get("CleanupPriv", 15);  self.CLEANUP_NAME = conf.get("CleanupName", f"unused_{self.UID}")

    def _ipmi(self, sub):
        cmd = ["ipmitool", "-I", self.IFACE, "-H", self.BMC_IP, "-U", self.USERNAME, "-P", self.PASSWORD] + sub
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.TIMEOUT)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"

    def _parse_user_list(self, out):
        users = {}
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("ID"): continue
            parts = line.split()
            if not parts: continue
            try: uid = int(parts[0])
            except: continue
            name = parts[1] if len(parts) > 1 and not parts[1][0].isdigit() else ""
            priv = parts[-1] if len(parts) >= 6 else ""
            ipmi_msg = parts[-2] if len(parts) >= 6 else ""
            users[uid] = {"name": name, "priv": priv, "ipmi_msg": ipmi_msg}
        return users

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True
        uid = self.UID

        def rec(step, item, ok, detail=""):
            if not ok: 
                CommonFunction.print_log("ERROR", f"[{item}] FAIL: {detail}")
            else:
                CommonFunction.print_log("INFO", f"[{item}] PASS: {detail}")
            steps.append((step, item, "PASS" if ok else "FAIL", detail))
            return ok

        # 1. set name
        rc, out, err = self._ipmi(["user", "set", "name", str(uid), self.UNAME])
        ok = rc == 0;  all_pass &= rec(1, "set_name", ok, err or out);  time.sleep(self.WAIT)

        # 2. set password
        rc, out, err = self._ipmi(["user", "set", "password", str(uid), self.UPASS])
        ok = rc == 0 and "successful" in out.lower()
        all_pass &= rec(2, "set_password", ok, err or out);  time.sleep(self.WAIT)

        # 3. user priv
        rc, out, err = self._ipmi(["user", "priv", str(uid), str(self.UPRIV), str(self.CHANNEL)])
        ok = rc == 0 and "successful" in out.lower()
        all_pass &= rec(3, "set_priv", ok, err or out);  time.sleep(self.WAIT)

        # 4. user enable
        rc, out, err = self._ipmi(["user", "enable", str(uid)])
        ok = rc == 0;  all_pass &= rec(4, "enable", ok, err or out);  time.sleep(self.WAIT)

        # 5. verify in user list
        rc, out, err = self._ipmi(["user", "list", str(self.CHANNEL)])
        if rc != 0:
            all_pass &= rec(5, "verify_user_list", False, "user list failed")
        else:
            users = self._parse_user_list(out)
            entry = users.get(uid, {})
            name_ok = entry.get("name", "") == self.UNAME
            priv_exp = self.PRIV_MAP.get(str(self.UPRIV), "ADMINISTRATOR")
            priv_ok  = priv_exp.upper() in entry.get("priv", "").upper()
            ok = name_ok and priv_ok
            detail = f"name={entry.get('name')} priv={entry.get('priv')}"
            all_pass &= rec(5, "verify_user_exists", ok, detail)

        # cleanup
        self._ipmi(["user", "disable", str(uid)])
        self._ipmi(["user", "set", "name", str(uid), self.CLEANUP_NAME])
        self._ipmi(["user", "priv", str(uid), str(self.CLEANUP_PRIV), str(self.CHANNEL)])
        CommonFunction.print_log("INFO", "cleanup 完成")

        self.command_check_result = "PASS" if all_pass else "FAIL"
        with open(self.csv_path, "a") as f:
            for step, item, res, detail in steps:
                f.write(f'"{step}","{item}","{res}","{detail}"\n')
        for step, item, res, detail in steps:
            test.add_key_value_to_json(self.json_path, "detail.cycle",
                value={"metrics": f"step{step}_{item}", "value": {"result": res, "detail": detail}})
        test.add_key_value_to_json(self.json_path, "summary",
            value={"metrics": self.TEST_NAME, "value": self.command_check_result})
        test.print_log("INFO", f"{self.TEST_NAME} 完成，结果：{self.command_check_result}")

if __name__ == "__main__":
    obj = None
    exit_code = 1
    try:
        obj = IpmiUserAdd();  obj.run()
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
