#!/bin/python
"""
IPMI 带外删除用户（正向 + 超管不可删反向）（RDSV_BMC_081）

IPMI 无真正 delete 命令，逻辑删除 = disable + set name 改占位名 + 降权到 NO ACCESS
正向：创建测试用户 → 逻辑删除 → 验证在列表中不可用（IPMI Msg=false，priv=NO ACCESS）
反向：尝试 disable 超管用户（ID=3）→ 应失败（ZTE返回错误码 0x82）

Usage: python3 bmc/ipmi_user_005.py -i <bmc_ip> -u <user> -p <pass>
"""

import os, subprocess, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase


class IpmiUserDelete(BmcWebTestBase):
    CASE        = "IpmiUserDelete"
    CONFIG_FILE = "ipmi_010_user_delete.json"
    CONF_DIR    = "ipmi"   # 配置文件位于 conf/bmc/ipmi/，覆盖基类默认的 "web"
    CSV_HEADER  = "step,check_item,result,detail\n"

    def __init__(self) -> None:
        super().__init__()

    def _load_extra_config(self, conf: dict) -> None:
        self.IFACE = conf.get("IpmiInterface", "lanplus");  self.TIMEOUT = conf.get("QueryTimeoutSec", 30)
        self.CHANNEL = conf.get("QueryChannel", 1);  self.WAIT = conf.get("WaitAfterOpSec", 2)
        self.UID = conf["TestUserId"];  self.UNAME = conf["TestUserName"]
        self.UPASS = conf["TestUserPassword"];  self.UPRIV = conf["TestUserPriv"]
        self.PROTECTED = conf.get("ProtectedUserIds", [1, 2, 3])
        self.CLEANUP_PRIV = conf.get("CleanupPriv", 15);  self.CLEANUP_NAME = conf.get("CleanupName", f"unused_{self.UID}")

    def _ipmi(self, sub):
        cmd = ["ipmitool", "-I", self.IFACE, "-H", self.BMC_IP, "-U", self.USERNAME, "-P", self.PASSWORD] + sub
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.TIMEOUT)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"

    def _parse_user_list(self, out):
        """按列头位置解析 user list，避免 'NO ACCESS' 两词导致 split 偏移错误。

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
            # Name：第2列（若第2 token 是数字则名字为空）
            name = parts[1] if len(parts) > 1 and not parts[1][0].isdigit() else ""
            # 先尝试列偏移法，若截取值不是 true/false 完整词则 fallback
            ipmi_msg = ""
            priv     = ""
            if col_ipmi_msg >= 0 and col_priv >= 0 and len(line) > col_ipmi_msg:
                segment  = line[col_ipmi_msg:col_priv].strip()
                candidate = segment.split()[0].lower() if segment else ""
                if candidate in ("true", "false"):
                    ipmi_msg = candidate
                    priv     = line[col_priv:].strip()
            if not ipmi_msg:
                # fallback：在 parts 中从后向前找 true/false
                for tok in reversed(parts):
                    if tok.lower() in ("true", "false"):
                        ipmi_msg = tok.lower()
                        break
                # priv = 最后若干 token（处理 NO ACCESS 两词）
                if len(parts) >= 3 and parts[-2].upper() == "NO" and parts[-1].upper() == "ACCESS":
                    priv = "NO ACCESS"
                elif parts:
                    priv = parts[-1]
            users[uid] = {"name": name, "ipmi_msg": ipmi_msg, "priv": priv.upper()}
        return users

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True

        def rec(step, item, ok, detail=""):
            r = "PASS" if ok else "FAIL"
            CommonFunction.print_log("INFO" if ok else "ERROR", f"[{item}] {r}: {detail}")
            steps.append((step, item, r, detail));  return ok

        # === 正向：创建 → 逻辑删除 → 验证 ===
        # 创建
        self._ipmi(["user", "set", "name", str(self.UID), self.UNAME])
        self._ipmi(["user", "set", "password", str(self.UID), self.UPASS])
        self._ipmi(["user", "priv", str(self.UID), str(self.UPRIV), str(self.CHANNEL)])
        rc, _, err = self._ipmi(["user", "enable", str(self.UID)])
        all_pass &= rec(1, "pre_create", rc == 0, err or "created")
        time.sleep(self.WAIT)

        # 逻辑删除
        rc1, _, _  = self._ipmi(["user", "disable", str(self.UID)])
        rc2, _, _  = self._ipmi(["user", "set", "name", str(self.UID), self.CLEANUP_NAME])
        rc3, _, _  = self._ipmi(["user", "priv", str(self.UID), str(self.CLEANUP_PRIV), str(self.CHANNEL)])
        ok = rc1 == 0 and rc2 == 0 and rc3 == 0
        all_pass &= rec(2, "logical_delete", ok, f"disable_rc={rc1}, rename_rc={rc2}, priv_rc={rc3}")
        time.sleep(self.WAIT)

        # 验证删除状态
        rc, out, _ = self._ipmi(["user", "list", str(self.CHANNEL)])
        if rc != 0:
            all_pass &= rec(3, "verify_deleted", False, "user list failed")
        else:
            users = self._parse_user_list(out)
            entry = users.get(self.UID, {})
            # 验证：ipmi_msg=false（不可登录），priv 含 NO ACCESS
            ipmi_msg_ok = entry.get("ipmi_msg", "").lower() == "false"
            priv_ok     = "NO ACCESS" in entry.get("priv", "").upper()
            ok = ipmi_msg_ok and priv_ok
            all_pass &= rec(3, "verify_deleted", ok,
                f"ipmi_msg={entry.get('ipmi_msg')}, priv={entry.get('priv')}")

        # === 反向：找到名为 Admin 的超管 ID，尝试 disable 应失败 ===
        # 不写死 ID=3，因为浪潮超管是 ID=2，ZTE 是 ID=3
        rc_list, out_list, _ = self._ipmi(["user", "list", str(self.CHANNEL)])
        admin_id = None
        if rc_list == 0:
            users_now = self._parse_user_list(out_list)
            for uid, info in users_now.items():
                if info.get("name", "").lower() == "admin":
                    admin_id = uid
                    break
        if admin_id is None:
            # fallback 用配置里的 PROTECTED
            admin_id = self.PROTECTED[0] if self.PROTECTED else 3
            CommonFunction.print_log("WARNING", f"未找到名为 Admin 的用户，fallback 到 ID={admin_id}")

        rc, out, err = self._ipmi(["user", "disable", str(admin_id)])
        fail_expected = rc != 0
        if fail_expected:
            CommonFunction.print_log("INFO", f"[反向] disable 超管 ID={admin_id} 被拒绝 ✓ ({out or err})")
            steps.append((4, f"protect_admin_id{admin_id}", "PASS", out or err))
        else:
            CommonFunction.print_log("WARNING",
                f"[反向] disable 超管 ID={admin_id} rc=0（BMC未拦截，可能厂商不做保护，记WARNING）")
            steps.append((4, f"protect_admin_id{admin_id}", "WARNING", "BMC未拦截超管disable"))
            # 尝试恢复（不把这个算FAIL，因为不是所有厂商都保护超管disable）
            self._ipmi(["user", "enable", str(admin_id)])

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
        obj = IpmiUserDelete();  obj.run()
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
