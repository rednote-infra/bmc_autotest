#!/bin/python
"""
BMC Web配置导出（Web_014）
RDSV_BMC_070~073 - BMC/BIOS配置可查询

Usage: python3 -m bmc.web_014_config_export -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebConfigExport(BmcWebTestBase):
    CASE        = 'WebConfigExport'
    CONFIG_FILE = 'web_014_config_export.json'

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

        bmc_uri  = self.conf.get('BmcConfigUri', '/redfish/v1/Managers/1')
        bios_uri = self.conf.get('BiosConfigUri', '/redfish/v1/Systems/1/Bios')

        # BMC 配置可查
        code, body = client.get(bmc_uri)
        all_pass = self._rec(steps, all_pass, 'bmc_config_accessible', code == 200,
            f'HTTP={code}')
        if code == 200 and isinstance(body, dict):
            has_net = 'EthernetInterfaces' in body or 'NetworkProtocol' in body
            steps.append(('bmc_config_has_network', 'PASS' if has_net else 'WARNING',
                f'network_fields={has_net}'))

        # BIOS 配置可查
        code2, body2 = client.get(bios_uri)
        all_pass = self._rec(steps, all_pass, 'bios_config_accessible', code2 == 200,
            f'HTTP={code2}')
        if code2 == 200 and isinstance(body2, dict):
            attrs = body2.get('Attributes', {})
            steps.append(('bios_attributes_count', 'PASS' if len(attrs) > 0 else 'WARNING',
                f'{len(attrs)}个BIOS属性'))

        # 导出 Action（仅验证接口存在，不实际导出）
        export_uri = self.conf.get('ExportActionUri')
        if export_uri:
            code3, _ = client.get(export_uri.replace('/Actions/', '/').rsplit('/',1)[0])
            steps.append(('export_action_parent_accessible', 'PASS' if code3==200 else 'WARNING',
                f'HTTP={code3}'))

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebConfigExport();  obj.run()
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
