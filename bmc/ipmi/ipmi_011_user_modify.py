#!/bin/python
"""
IPMI 带外修改用户名/密码/权限（RDSV_BMC_082）

测试步骤：
  1. 创建初始测试用户
  2. 修改用户名 → 验证
  3. 修改密码 → 验证
  4. 依次修改权限为 USER/OPERATOR/ADMINISTRATOR → 验证
  5. cleanup

Usage: python3 bmc/ipmi_user_006.py -i <bmc_ip> -u <user> -p <pass>
"""

import os, subprocess, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase


class IpmiUserModify(BmcWebTestBase):
    CASE        = "IpmiUserModify"
    CONFIG_FILE = "ipmi_011_user_modify.json"
    CONF_DIR    = "ipmi"   # 配置文件位于 conf/bmc/ipmi/，覆盖基类默认的 "web"
    CSV_HEADER  = "step,check_item,result,detail\n"

    def __init__(self) -> None:
        super().__init__()

    def _load_extra_config(self, conf: dict) -> None:
        self.IFACE = conf.get("IpmiInterface", "lanplus");  self.TIMEOUT = conf.get("QueryTimeoutSec", 30)
        self.CHANNEL = conf.get("QueryChannel", 1);  self.WAIT = conf.get("WaitAfterOpSec", 2)
        self.UID = conf["TestUserId"]
        self.INIT_NAME = conf["InitName"];  self.INIT_PASS = conf["InitPassword"];  self.INIT_PRIV = conf["InitPriv"]
        self.MOD_NAME  = conf["ModifyNameTo"];  self.MOD_PASS = conf["ModifyPasswordTo"]
        self.PRIV_SEQ  = conf.get("ModifyPrivSequence", [2, 3, 4])
        self.PRIV_MAP  = conf.get("PrivLevelMap", {})
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
            priv = parts[-1] if len(parts) >= 2 else ""
            users[uid] = {"name": name, "priv": priv}
        return users

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True

        def rec(step, item, ok, detail=""):
            r = "PASS" if ok else "FAIL"
            CommonFunction.print_log("INFO" if ok else "ERROR", f"[{item}] {r}: {detail}")
            steps.append((step, item, r, detail));  return ok

        # 1. 创建初始用户
        self._ipmi(["user", "set", "name", str(self.UID), self.INIT_NAME])
        self._ipmi(["user", "set", "password", str(self.UID), self.INIT_PASS])
        self._ipmi(["user", "priv", str(self.UID), str(self.INIT_PRIV), str(self.CHANNEL)])
        self._ipmi(["user", "enable", str(self.UID)])
        rec(1, "pre_create", True, f"uid={self.UID} name={self.INIT_NAME}")
        time.sleep(self.WAIT)

        # 2. 修改用户名
        rc, out, err = self._ipmi(["user", "set", "name", str(self.UID), self.MOD_NAME])
        ok = rc == 0
        if ok:
            rc2, out2, _ = self._ipmi(["user", "list", str(self.CHANNEL)])
            users = self._parse_user_list(out2)
            ok = users.get(self.UID, {}).get("name", "") == self.MOD_NAME
        all_pass &= rec(2, "modify_name", ok, f"new_name={self.MOD_NAME}")
        time.sleep(self.WAIT)

        # 3. 修改密码
        rc, out, err = self._ipmi(["user", "set", "password", str(self.UID), self.MOD_PASS])
        ok = rc == 0 and "successful" in out.lower()
        all_pass &= rec(3, "modify_password", ok, out or err)
        time.sleep(self.WAIT)

        # 4. 修改权限序列（USER→OPERATOR→ADMINISTRATOR）
        for priv_level in self.PRIV_SEQ:
            rc, out, err = self._ipmi(["user", "priv", str(self.UID), str(priv_level), str(self.CHANNEL)])
            op_ok = rc == 0 and "successful" in out.lower()
            if op_ok:
                rc2, out2, _ = self._ipmi(["user", "list", str(self.CHANNEL)])
                users = self._parse_user_list(out2)
                priv_name = self.PRIV_MAP.get(str(priv_level), str(priv_level))
                op_ok = priv_name.upper() in users.get(self.UID, {}).get("priv", "").upper()
            all_pass &= rec(4, f"modify_priv_to_{priv_level}", op_ok,
                self.PRIV_MAP.get(str(priv_level), str(priv_level)))
            time.sleep(self.WAIT)

        # cleanup
        self._ipmi(["user", "disable", str(self.UID)])
        self._ipmi(["user", "set", "name", str(self.UID), self.CLEANUP_NAME])
        self._ipmi(["user", "priv", str(self.UID), str(self.CLEANUP_PRIV), str(self.CHANNEL)])

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
        obj = IpmiUserModify();  obj.run()
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
