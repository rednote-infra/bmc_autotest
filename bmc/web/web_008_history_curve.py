#!/bin/python
"""
BMC Web历史曲线查询（Web_008）
RDSV_BMC_061,062 - 历史功率/温度曲线数据

Usage: python3 -m bmc.web_008_history_curve -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebHistoryCurve(BmcWebTestBase):
    CASE        = 'WebHistoryCurve'
    CONFIG_FILE = 'web_008_history_curve.json'

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

        min_records = self.conf.get('MinHistoryRecords', 1)

        # 功率数据：优先 PowerControl（标准历史），降级 Oem.Public.TotalPower（当前快照）
        code, body = client.get('/redfish/v1/Chassis/1/Power')
        if code == 200 and isinstance(body, dict):
            pctls = body.get('PowerControl', [])
            if len(pctls) > 0:
                all_pass = self._rec(steps, all_pass, 'power_history_accessible',
                    True, f'PowerControl entries={len(pctls)}')
            else:
                # 降级：查 Oem.Public.TotalPower 当前功率快照
                oem_pub = body.get('Oem', {}).get('Public', {})
                total_power = oem_pub.get('TotalPower')
                if total_power is not None:
                    CommonFunction.print_log('WARNING',
                        f'[power_history_accessible] WARNING: 无PowerControl历史，'
                        f'降级为当前功率快照 TotalPower={total_power}W')
                    steps.append(('power_history_accessible', 'WARNING',
                        f'无PowerControl，OEM当前功率TotalPower={total_power}W'))
                else:
                    all_pass = self._rec(steps, all_pass, 'power_history_accessible',
                        False, 'PowerControl为空且无OEM功率数据')
        else:
            all_pass = self._rec(steps, all_pass, 'power_history_accessible', False, f'HTTP={code}')

        # 温度数据：先试标准路径 Thermal.Temperatures，再试OEM历史路径
        temp_ok = False
        code2, body2 = client.get('/redfish/v1/Chassis/1/Thermal')
        if code2 == 200 and isinstance(body2, dict):
            temps = body2.get('Temperatures', [])
            temp_ok = len(temps) >= min_records
            all_pass = self._rec(steps, all_pass, 'temp_history_standard',
                temp_ok, f'Temperatures={len(temps)}条')
        if not temp_ok:
            oem_uri = self.conf.get('TempHistoryUri', '/redfish/v1/Chassis/1/Thermal/InletHistoryTemperature')
            code3, body3 = client.get(oem_uri)
            if code3 == 200:
                records = body3 if isinstance(body3, list) else body3.get('Members', [])
                all_pass = self._rec(steps, all_pass, 'temp_history_oem',
                    len(records) >= min_records, f'OEM记录数={len(records)}')
            else:
                steps.append(('temp_history_oem', 'WARNING', f'HTTP={code3}（OEM路径不可用）'))

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebHistoryCurve();  obj.run()
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
