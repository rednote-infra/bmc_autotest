#!/bin/python
"""
BMC Web传感器信息查询（Web_010）
RDSV_BMC_100,110 - 传感器列表及类型覆盖

Usage: python3 -m bmc.web_010_sensor_info -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebSensorInfo(BmcWebTestBase):
    CASE        = 'WebSensorInfo'
    CONFIG_FILE = 'web_010_sensor_info.json'

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

        thermal_uri = self.conf.get('ThermalUri', '/redfish/v1/Chassis/1/Thermal')
        power_uri   = self.conf.get('PowerUri', '/redfish/v1/Chassis/1/Power')

        # Thermal（温度+风扇）
        code, body = client.get(thermal_uri)
        all_pass = self._rec(steps, all_pass, 'thermal_accessible', code == 200, f'HTTP={code}')
        if code == 200 and isinstance(body, dict):
            temps = body.get('Temperatures', [])
            fans  = body.get('Fans', [])
            all_pass = self._rec(steps, all_pass, 'temperature_sensors', len(temps) > 0,
                f'{len(temps)}条温度传感器')
            all_pass = self._rec(steps, all_pass, 'fan_sensors', len(fans) > 0,
                f'{len(fans)}条风扇传感器')
            # 关键温度传感器有读数
            valid_temps = [t for t in temps if t.get('ReadingCelsius') is not None]
            all_pass = self._rec(steps, all_pass, 'temperature_has_reading', len(valid_temps) > 0,
                f'{len(valid_temps)}/{len(temps)}条有读数')

        # Power（电压+PSU）
        code2, body2 = client.get(power_uri)
        all_pass = self._rec(steps, all_pass, 'power_accessible', code2 == 200, f'HTTP={code2}')
        if code2 == 200 and isinstance(body2, dict):
            voltages = body2.get('Voltages', [])
            psus     = body2.get('PowerSupplies', [])
            steps.append(('voltage_sensors', 'PASS' if len(voltages) > 0 else 'WARNING',
                f'{len(voltages)}条电压传感器'))
            all_pass = self._rec(steps, all_pass, 'psu_sensors', len(psus) > 0, f'{len(psus)}个PSU')

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebSensorInfo();  obj.run()
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
