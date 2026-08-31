#!/bin/python
"""
IPMI 带外查询在线活动用户（RDSV_BMC_084）

用 session info all 查询活跃 IPMI 会话：
  - 至少存在 1 个活动 session（active sessions >= 1）
  - 每个活动 session 应包含必要字段：session handle / active sessions / user id / privilege level / session type
  - 记录所有活动会话的 user id / privilege / console ip 到结果

Usage: python3 bmc/ipmi_user_008.py -i <bmc_ip> -u <user> -p <pass>
"""

import os, subprocess, sys, traceback
import time
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase


class IpmiUserSession(BmcWebTestBase):
    CASE        = "IpmiUserSession"
    CONFIG_FILE = "ipmi_013_user_session.json"
    CONF_DIR    = "ipmi"   # 配置文件位于 conf/bmc/ipmi/，覆盖基类默认的 "web"
    CSV_HEADER  = "check_item,result,detail\n"

    def __init__(self) -> None:
        super().__init__()

    def _load_extra_config(self, conf: dict) -> None:
        self.IFACE = conf.get("IpmiInterface", "lanplus");  self.TIMEOUT = conf.get("QueryTimeoutSec", 30)
        self.REQ_FIELDS = conf.get("RequiredSessionFields",
            ["session handle", "active sessions", "user id", "privilege level", "session type"])

    def _ipmi(self, sub):
        cmd = ["ipmitool", "-I", self.IFACE, "-H", self.BMC_IP, "-U", self.USERNAME, "-P", self.PASSWORD] + sub
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=self.TIMEOUT)
            return r.returncode, r.stdout.strip(), r.stderr.strip()
        except subprocess.TimeoutExpired:
            return -1, "", "timeout"

    def _parse_sessions(self, out):
        """解析 session info all 输出，返回 session block 列表"""
        sessions = []
        current = {}
        for line in out.splitlines():
            line = line.strip()
            if not line:
                if current:
                    sessions.append(current); current = {}
                continue
            if ":" in line:
                k, _, v = line.partition(":")
                current[k.strip().lower()] = v.strip()
        if current:
            sessions.append(current)
        # 只返回有 user id 的活动 session
        return [s for s in sessions if s.get("user id", "").strip() not in ("", "0", None)]

    def run(self):
        test = CommonFunction()
        results = [];  all_pass = True

        rc, out, err = self._ipmi(["session", "info", "all"])
        if rc != 0 or not out:
            CommonFunction.print_log("ERROR", f"session info all 失败，rc={rc}，err={err}")
            results.append(("session_info_all", "FAIL", err))
            all_pass = False
        else:
            CommonFunction.print_log("INFO", f"session info all 成功，原始输出：\n{out}")
            results.append(("session_info_all", "PASS", "ok"))

            # 必要字段检查
            out_lower = out.lower()
            for field in self.REQ_FIELDS:
                if field.lower() in out_lower:
                    results.append((f"field_{field}", "PASS", "present"))
                else:
                    results.append((f"field_{field}", "FAIL", "missing"))
                    all_pass = False
                    CommonFunction.print_log("ERROR", f"session info 缺少字段：{field}")

            # 活动 session 数量
            active_sessions = self._parse_sessions(out)
            count = len(active_sessions)
            CommonFunction.print_log("INFO", f"活动会话数（有 user id 的）：{count}")
            if count >= 1:
                results.append(("active_session_count", "PASS", f"count={count}"))
                for i, s in enumerate(active_sessions):
                    CommonFunction.print_log("INFO",
                        f"  Session {i+1}: user_id={s.get('user id','?')}"
                        f" priv={s.get('privilege level','?')} type={s.get('session type','?')}"
                        f" console_ip={s.get('console ip','?')}")
            else:
                results.append(("active_session_count", "FAIL", f"count={count}，期望>=1"))
                all_pass = False
                CommonFunction.print_log("ERROR", "未检测到活动会话（至少应有本次连接）")

        self.command_check_result = "PASS" if all_pass else "FAIL"
        with open(self.csv_path, "a") as f:
            for item, res, detail in results:
                f.write(f'"{item}","{res}","{detail}"\n')
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
        obj = IpmiUserSession();  obj.run()
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
