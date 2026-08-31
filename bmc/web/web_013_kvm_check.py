#!/bin/python
"""
BMC Web KVM服务检查（Web_013）
RDSV_BMC_037 - KVM服务接口可达及字段完整

Usage: python3 -m bmc.web_013_kvm_check -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebKvmCheck(BmcWebTestBase):
    CASE        = 'WebKvmCheck'
    CONFIG_FILE = 'web_013_kvm_check.json'

    def _load_extra_config(self, conf: dict) -> None:
        self.conf = conf

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True
        client = BmcWebClient(self.BMC_IP, self.USERNAME, self.PASSWORD, self.TIMEOUT)
        if not client.login():
            self._rec(steps, all_pass, 'login', False, '登录失败')
            self.command_check_result = 'FAIL'
            self._save(test, steps);  return

        kvm_uri = self.conf.get('KvmServiceUri', '/redfish/v1/Managers/1/KvmService')
        req_fields = self.conf.get('RequiredKvmFields', ['MaxSessions'])

        code, body = client.get(kvm_uri)
        all_pass = self._rec(steps, all_pass, 'kvm_service_accessible', code == 200, f'HTTP={code}')
        if code == 200 and isinstance(body, dict):
            # 字段存在性检查（任意一个别名满足即可）
            aliases = self.conf.get('MaxSessionsAliases', ['MaximumNumberOfSessions', 'MaxSessions'])
            max_s = None
            for alias in aliases:
                if alias in body and body[alias] is not None:
                    max_s = body[alias]
                    break
            field_found = max_s is not None
            all_pass = self._rec(steps, all_pass, 'kvm_max_sessions_field_exists', field_found,
                f'found={max_s} (aliases={aliases})' if field_found else f'字段不存在，aliases={aliases}')
            min_s = int(self.conf.get('MinMaxSessions', 1))
            if field_found:
                all_pass = self._rec(steps, all_pass, 'kvm_max_sessions_valid',
                    int(max_s) >= min_s, f'MaxSessions={max_s}（期望≥{min_s}）')
            # 其他必要字段
            other_fields = [f for f in req_fields if f not in aliases]
            missing = [f for f in other_fields if f not in body]
            if other_fields:
                all_pass = self._rec(steps, all_pass, 'kvm_other_fields',
                    len(missing) == 0, f'missing={missing}' if missing else '✓')

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebKvmCheck();  obj.run()
        exit_code = 0 if obj.command_check_result == 'PASS' else 2
        time.sleep(5)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        CommonFunction.print_log('ERROR', str(e));  traceback.print_exc();  exit_code = 1
    finally:
        if obj:
            try: open(obj.ec_path, 'w').write(str(exit_code))
            except: pass
    sys.exit(exit_code)
