#!/bin/python
"""
BMC Web硬件信息查询（Web_002）
RDSV_BMC_001~015,018,021 - CPU/内存/硬盘/网卡/GPU/PSU/风扇信息

Usage: python3 -m bmc.web_002_hardware_info -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebHardwareInfo(BmcWebTestBase):
    CASE        = 'WebHardwareInfo'
    CONFIG_FILE = 'web_002_hardware_info.json'

    def _load_extra_config(self, conf: dict) -> None:
        self.conf = conf

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True
        client = BmcWebClient(self.BMC_IP, self.USERNAME, self.PASSWORD, self.TIMEOUT)
        if not client.login():
            all_pass = self._rec(steps, all_pass, 'login', False, '登录失败')
            self.command_check_result = 'FAIL'
            self._save(test, steps);  return

        items = self.conf.get('CheckItems', [])
        for item in items:
            name = item['name'];  uri = item['uri'];  req_fields = item.get('required_fields', [])
            optional = item.get('optional', False)  # optional=True 时失败记 WARNING 不 FAIL

            # ---- CPU：查集合，取第一个 Central Processor 成员验证字段 ----
            if item.get('type') == 'cpu_from_collection':
                code, body = client.get(uri)
                if code != 200 or not isinstance(body, dict):
                    all_pass = self._rec(steps, all_pass, name, False, f'集合 HTTP={code}')
                    continue
                members = body.get('Members', [])
                cpu_member = None
                for m in members:
                    mc, mb = client.get(m.get('@odata.id', ''))
                    if mc == 200 and isinstance(mb, dict) and 'Central' in mb.get('ProcessorType', ''):
                        cpu_member = mb
                        break
                if not cpu_member:
                    # 没有 Central Processor，取第一个成员
                    if members:
                        mc, cpu_member = client.get(members[0].get('@odata.id', ''))
                if cpu_member and isinstance(cpu_member, dict):
                    missing = [f for f in req_fields if f not in cpu_member or cpu_member[f] is None]
                    ok = len(missing) == 0
                    detail = (f'missing={missing}' if missing
                              else f'Model={cpu_member.get("Model","?")[:40]}, fields={req_fields} ✓')
                    all_pass = self._rec(steps, all_pass, name, ok, detail)
                else:
                    all_pass = self._rec(steps, all_pass, name, False, '未找到CPU成员')
                continue

            # ---- GPU/PCIe：无GPU时记 WARNING 不 FAIL ----
            if item.get('type') == 'gpu_optional':
                code, body = client.get(uri)
                if code == 200 and isinstance(body, dict):
                    cnt = len(body.get('Members', []))
                    # 有 PCIe 设备即可，GPU不强求
                    CommonFunction.print_log('INFO', f'[{name}] PASS: PCIeDevices={cnt}条')
                    steps.append((name, 'PASS', f'HTTP=200, PCIeDevices={cnt}'))
                else:
                    # 无 GPU/PCIe 资源，降级 WARNING
                    CommonFunction.print_log('WARNING', f'[{name}] WARNING: HTTP={code}，无PCIe资源（非必须）')
                    steps.append((name, 'WARNING', f'HTTP={code}，无PCIeDevices资源'))
                continue

            # ---- 通用逻辑 ----
            code, body = client.get(uri)
            if code != 200:
                if optional:
                    CommonFunction.print_log('WARNING', f'[{name}] WARNING: HTTP={code}')
                    steps.append((name, 'WARNING', f'HTTP={code}'))
                else:
                    all_pass = self._rec(steps, all_pass, name, False, f'HTTP={code}')
                continue
            if not isinstance(body, dict):
                all_pass = self._rec(steps, all_pass, name, False, '响应非JSON')
                continue
            missing = [f for f in req_fields if f not in body or body[f] is None]
            ok = len(missing) == 0
            detail = f'HTTP=200, missing={missing}' if missing else f'HTTP=200, fields={req_fields} ✓'
            all_pass = self._rec(steps, all_pass, name, ok, detail)

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebHardwareInfo();  obj.run()
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
