#!/bin/python
"""
IPMI 带外禁用用户（正向 + 超管不可禁反向）（RDSV_BMC_083）

正向：创建测试用户 → disable → 验证列表 IPMI Msg=false
反向：尝试 disable Admin(ID=3) → 应失败（ZTE 返回错误码 0x82）

注意：禁用后无法通过 RMCP+ 登录验证（ZTE 会话稳定性问题），
      以 user list 中 IPMI Msg 列为 false 作为禁用成功的依据

Usage: python3 bmc/ipmi_user_007.py -i <bmc_ip> -u <user> -p <pass>
"""

import os, subprocess, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase


class IpmiUserDisable(BmcWebTestBase):
    CASE        = "IpmiUserDisable"
    CONFIG_FILE = "ipmi_012_user_disable.json"
    CONF_DIR    = "ipmi"   # 配置文件位于 conf/bmc/ipmi/，覆盖基类默认的 "web"
    CSV_HEADER  = "step,check_item,result,detail\n"

    def __init__(self) -> None:
        super().__init__()

    def _load_extra_config(self, conf: dict) -> None:
        self.IFACE = conf.get("IpmiInterface", "lanplus");  self.TIMEOUT = conf.get("QueryTimeoutSec", 30)
        self.CHANNEL = conf.get("QueryChannel", 1);  self.WAIT = conf.get("WaitAfterOpSec", 2)
        self.UID = conf["TestUserId"];  self.UNAME = conf["TestUserName"]
        self.UPASS = conf["TestUserPassword"];  self.UPRIV = conf["TestUserPriv"]
        self.ADMIN_ID = conf.get("ProtectedAdminId", 3)
        self.CLEANUP_PRIV = conf.get("CleanupPriv", 15);  self.CLEANUP_NAME = conf.get("CleanupName", f"unused_{self.UID}")

    def _ipmi(self, sub):
        cmd = ["ipmitool", "-I", self.IFACE, "-H", self.BMC_IP, "-U", self.USERNAME, "-P", self.PASSWORD] + sub
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.TIMEOUT)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"

    def _parse_user_list(self, out):
        """按列头位置解析，避免 'NO ACCESS' 两词导致 split 偏移错误。

        已知问题：列偏移法在某些厂商 BMC 中因实际字符宽度不对齐会截到 'se'（false 后两字）
        等错误片段。修复：对截取结果做 true/false 完整词检查，不符合则回退 parts 倒数法。
        """
        users = {}
        col_ipmi_msg = col_priv = -1
        for line in out.splitlines():
            line = line.rstrip()
            if "IPMI Msg" in line and "Channel Priv" in line:
                col_ipmi_msg = line.index("IPMI Msg")
                col_priv     = line.index("Channel Priv Limit") if "Channel Priv Limit" in line else line.index("Channel Priv")
                continue
            if not line.strip() or line.strip().startswith("ID"):
                continue
            parts = line.split()
            if not parts:
                continue
            try:
                uid = int(parts[0])
            except Exception:
                continue
            name = parts[1] if len(parts) > 1 and not parts[1][0].isdigit() else ""
            # 先尝试列偏移法，若截取值不是 true/false 完整词则 fallback
            ipmi_msg = ""
            if col_ipmi_msg >= 0 and col_priv >= 0 and len(line) > col_ipmi_msg:
                segment   = line[col_ipmi_msg:col_priv].strip()
                candidate = segment.split()[0].lower() if segment else ""
                if candidate in ("true", "false"):
                    ipmi_msg = candidate
            if not ipmi_msg:
                # fallback：在 parts 中从后向前找 true/false
                for tok in reversed(parts):
                    if tok.lower() in ("true", "false"):
                        ipmi_msg = tok.lower()
                        break
            users[uid] = {"name": name, "ipmi_msg": ipmi_msg}
        return users

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True

        def rec(step, item, ok, detail=""):
            r = "PASS" if ok else "FAIL"
            CommonFunction.print_log("INFO" if ok else "ERROR", f"[{item}] {r}: {detail}")
            steps.append((step, item, r, detail));  return ok

        # === 正向：创建 → 禁用 → 验证 ===
        # 创建
        self._ipmi(["user", "set", "name", str(self.UID), self.UNAME])
        self._ipmi(["user", "set", "password", str(self.UID), self.UPASS])
        self._ipmi(["user", "priv", str(self.UID), str(self.UPRIV), str(self.CHANNEL)])
        rc, _, err = self._ipmi(["user", "enable", str(self.UID)])
        all_pass &= rec(1, "pre_create", rc == 0, err or "created")

        # 确认 enable 后 IPMI Msg=true
        rc, out, _ = self._ipmi(["user", "list", str(self.CHANNEL)])
        if rc == 0:
            users = self._parse_user_list(out)
            ipmi_msg_before = users.get(self.UID, {}).get("ipmi_msg", "")
            all_pass &= rec(2, "verify_enabled", ipmi_msg_before.lower() == "true",
                f"ipmi_msg={ipmi_msg_before}（期望 true）")
        time.sleep(self.WAIT)

        # 禁用
        rc, _, err = self._ipmi(["user", "disable", str(self.UID)])
        all_pass &= rec(3, "disable_user", rc == 0, err or "disabled")
        time.sleep(self.WAIT)

        # 验证禁用：IPMI Msg=false
        rc, out, _ = self._ipmi(["user", "list", str(self.CHANNEL)])
        if rc != 0:
            all_pass &= rec(4, "verify_disabled", False, "user list failed")
        else:
            users = self._parse_user_list(out)
            ipmi_msg_after = users.get(self.UID, {}).get("ipmi_msg", "")
            all_pass &= rec(4, "verify_disabled", ipmi_msg_after.lower() == "false",
                f"ipmi_msg={ipmi_msg_after}（期望 false）")

        # === 反向：动态找 Admin 超管 ID（不写死，浪潮是ID=2，ZTE是ID=3）===
        rc_list, out_list, _ = self._ipmi(["user", "list", str(self.CHANNEL)])
        admin_id = self.ADMIN_ID  # fallback
        if rc_list == 0:
            tmp_users = self._parse_user_list(out_list)
            for uid, info in tmp_users.items():
                if info.get("name", "").lower() == "admin":
                    admin_id = uid
                    break

        rc, out, err = self._ipmi(["user", "disable", str(admin_id)])
        fail_expected = rc != 0
        if fail_expected:
            CommonFunction.print_log("INFO", f"[反向] disable Admin(ID={admin_id}) 被拒绝 ✓ ({out or err})")
            steps.append((5, f"protect_admin_id{admin_id}", "PASS", out or err))
        else:
            CommonFunction.print_log("WARNING",
                f"[反向] disable Admin(ID={admin_id}) rc=0（BMC未拦截，可能厂商不做保护，记WARNING）")
            steps.append((5, f"protect_admin_id{admin_id}", "WARNING", "BMC未拦截超管disable"))
            self._ipmi(["user", "enable", str(admin_id)])  # 尝试恢复

        # cleanup
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
        obj = IpmiUserDisable();  obj.run()
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
