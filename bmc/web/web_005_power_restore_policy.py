#!/bin/python
"""
BMC Web通电开机策略（Web_005）
RDSV_BMC_066 - 查询并验证通电策略默认值

Usage: python3 -m bmc.web_005_power_restore_policy -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebPowerRestorePolicy(BmcWebTestBase):
    CASE        = 'WebPowerRestorePolicy'
    CONFIG_FILE = 'web_005_power_restore_policy.json'

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

        sys_uri = self.conf.get('SystemUri', '/redfish/v1/Systems/1')
        expected = self.conf.get('ExpectedDefaults', ['RestorePreviousState','LastState'])
        allowable = self.conf.get('AllowableValues', [])

        code, body = client.get(sys_uri)
        all_pass = self._rec(steps, all_pass, 'system_accessible', code == 200, f'HTTP={code}')
        if code == 200 and isinstance(body, dict):
            # 标准字段
            policy = body.get('PowerRestorePolicy', '')
            # OEM字段
            oem = body.get('Oem', {}).get('Public', {})
            oem_policy = oem.get('PowerOnStrategy', '') or oem.get('PowerRestorePolicy', '')
            actual = policy or oem_policy
            ok = any(e in actual for e in expected) if actual else False
            all_pass = self._rec(steps, all_pass, 'default_policy_correct',
                ok, f'policy={actual}（期望含{expected}）')
            if allowable:
                all_pass = self._rec(steps, all_pass, 'policy_field_exists', bool(actual),
                    f'std={policy}, oem={oem_policy}')

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebPowerRestorePolicy();  obj.run()
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
