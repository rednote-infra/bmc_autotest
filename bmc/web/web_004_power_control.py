#!/bin/python
"""
BMC Web电源状态控制（Web_004）
RDSV_BMC_063 - 查询电源状态及可用操作（只查不操作）

Usage: python3 -m bmc.web_004_power_control -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebPowerControl(BmcWebTestBase):
    CASE        = 'WebPowerControl'
    CONFIG_FILE = 'web_004_power_control.json'

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

        sys_uri = self.conf.get('PowerStateUri', '/redfish/v1/Systems/1')
        expect_resets = self.conf.get('AllowableResetTypes', [])

        code, body = client.get(sys_uri)
        all_pass = self._rec(steps, all_pass, 'system_accessible', code == 200, f'HTTP={code}')
        if code == 200 and isinstance(body, dict):
            power_state = body.get('PowerState', '')
            all_pass = self._rec(steps, all_pass, 'power_state_readable',
                power_state in ('On','Off','PoweringOn','PoweringOff'), f'PowerState={power_state}')
            # 验证 AllowableValues 包含必要操作
            actions = body.get('Actions', {})
            reset_action = actions.get('#ComputerSystem.Reset', {})
            allowable = reset_action.get('ResetType@Redfish.AllowableValues', [])
            for r in expect_resets:
                present = r in allowable
                if not present:
                    CommonFunction.print_log('WARNING', f'ResetType {r} 不在 AllowableValues（记WARNING）')
                    steps.append((f'reset_type_{r}', 'WARNING', f'not in {allowable}'))
                else:
                    steps.append((f'reset_type_{r}', 'PASS', 'found'))

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebPowerControl();  obj.run()
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
