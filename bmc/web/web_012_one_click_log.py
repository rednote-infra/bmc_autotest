#!/bin/python
"""
BMC Web一键日志收集（Web_012）
RDSV_BMC_131 - 触发一键日志收集接口

Usage: python3 -m bmc.web_012_one_click_log -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebOneClickLog(BmcWebTestBase):
    CASE        = 'WebOneClickLog'
    CONFIG_FILE = 'web_012_one_click_log.json'

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

        std_uri = self.conf.get('StandardActionUri',
            '/redfish/v1/Managers/1/LogServices/Actions/LogServices.Dump')
        oem_uri = self.conf.get('CustomActionUri',
            '/redfish/v1/Managers/1/LogServices/1/Actions/LogService.ExportLogs')
        body_payload = self.conf.get('ActionBody', {})

        # 先试标准接口
        code, body = client.post(std_uri, body_payload)
        if code in (200, 202, 204):
            all_pass = self._rec(steps, all_pass, 'one_click_log_standard', True,
                f'HTTP={code}（标准接口成功）')
        else:
            steps.append(('one_click_log_standard', 'WARNING', f'HTTP={code}，尝试OEM接口'))
            # 试 OEM 接口
            code2, body2 = client.post(oem_uri, body_payload)
            if code2 in (200, 202, 204):
                all_pass = self._rec(steps, all_pass, 'one_click_log_oem', True,
                    f'HTTP={code2}（OEM接口成功）')
            else:
                all_pass = self._rec(steps, all_pass, 'one_click_log_oem', False,
                    f'标准={code}，OEM={code2}')

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebOneClickLog();  obj.run()
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
