#!/bin/python
"""
BMC Web固件版本查询（Web_003）
RDSV_BMC_022 - BMC/BIOS/CPLD等固件版本

Usage: python3 -m bmc.web_003_firmware_version -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebFirmwareVersion(BmcWebTestBase):
    CASE        = 'WebFirmwareVersion'
    CONFIG_FILE = 'web_003_firmware_version.json'

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

        inv_uri = self.conf.get('FirmwareInventoryUri', '/redfish/v1/UpdateService/FirmwareInventory')
        req_comps = self.conf.get('RequiredComponents', ['BMC', 'BIOS'])

        code, body = client.get(inv_uri)
        all_pass = self._rec(steps, all_pass, 'firmware_inventory_accessible', code == 200, f'HTTP={code}')
        if code == 200 and isinstance(body, dict):
            members = body.get('Members', [])
            all_pass = self._rec(steps, all_pass, 'firmware_members_exist', len(members) > 0, f'count={len(members)}')
            # 查每个成员
            found = {}
            for m in members:
                mcode, mbody = client.get(m.get('@odata.id',''))
                if mcode == 200 and isinstance(mbody, dict):
                    iid = mbody.get('Id',''); ver = mbody.get('Version','')
                    for comp in req_comps:
                        if comp.upper() in iid.upper() and comp not in found:
                            found[comp] = ver
            for comp in req_comps:
                ok = comp in found
                all_pass = self._rec(steps, all_pass, f'component_{comp}', ok,
                    f'version={found.get(comp,"未找到")}')

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebFirmwareVersion();  obj.run()
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
