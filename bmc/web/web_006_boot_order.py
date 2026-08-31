#!/bin/python
"""
BMC Web启动顺序设置（Web_006）
RDSV_BMC_068 - 查询启动设备列表，验证PXE/HD等可用

Usage: python3 -m bmc.web_006_boot_order -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebBootOrder(BmcWebTestBase):
    CASE        = 'WebBootOrder'
    CONFIG_FILE = 'web_006_boot_order.json'

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
        boot_opts_uri = self.conf.get('BootOptionsUri', '/redfish/v1/Systems/1/BootOptions')
        req_devs = self.conf.get('RequiredBootDevices', ['Pxe','Hdd'])

        # 各厂商启动设备关键字映射（模糊匹配，大小写不敏感）
        DEV_KEYWORDS = {
            'Pxe':    ['pxe', 'network', 'uefi:network', 'ipv4', 'ipv6'],
            'Hdd':    ['hdd', 'hard disk', 'harddisk', 'sata', 'nvme', 'disk'],
            'Cd':     ['cd', 'dvd', 'optical'],
        }

        code, body = client.get(sys_uri)
        all_pass = self._rec(steps, all_pass, 'system_accessible', code == 200, f'HTTP={code}')
        if code == 200 and isinstance(body, dict):
            boot = body.get('Boot', {})
            allowable_src = boot.get('BootSourceOverrideTarget@Redfish.AllowableValues', [])

            if allowable_src:
                # 路径1：AllowableValues 存在，直接匹配
                all_pass = self._rec(steps, all_pass, 'boot_allowable_sources',
                    True, f'sources={allowable_src}')
                for dev in req_devs:
                    keywords = DEV_KEYWORDS.get(dev, [dev.lower()])
                    ok = any(dev.lower() == s.lower() or
                             any(kw in s.lower() for kw in keywords)
                             for s in allowable_src)
                    all_pass = self._rec(steps, all_pass, f'boot_dev_{dev}', ok,
                        f'{"✓ 命中" if ok else "未命中"} keywords={keywords} in {allowable_src}')
            else:
                # 路径2：AllowableValues 为空，降级查 BootOptions 集合
                CommonFunction.print_log('WARNING',
                    'BootSourceOverrideTarget@Redfish.AllowableValues 为空，降级查 BootOptions 集合')
                steps.append(('boot_allowable_sources', 'WARNING',
                    'AllowableValues 为空，降级查 BootOptions'))
                code2, body2 = client.get(boot_opts_uri)
                if code2 == 200 and isinstance(body2, dict):
                    members = body2.get('Members', [])
                    all_pass = self._rec(steps, all_pass, 'boot_options_exist',
                        len(members) > 0, f'BootOptions成员数={len(members)}')
                    # 从 BootOptions 成员里提取 BootOptionReference 做模糊匹配
                    refs = []
                    for m in members:
                        mc, mb = client.get(m.get('@odata.id', ''))
                        if mc == 200 and isinstance(mb, dict):
                            refs.append(mb.get('BootOptionReference', ''))
                    for dev in req_devs:
                        keywords = DEV_KEYWORDS.get(dev, [dev.lower()])
                        ok = any(any(kw in ref.lower() for kw in keywords) for ref in refs)
                        all_pass = self._rec(steps, all_pass, f'boot_dev_{dev}', ok,
                            f'{"✓ 命中" if ok else "未命中"} keywords={keywords} in refs={refs}')
                else:
                    all_pass = self._rec(steps, all_pass, 'boot_options_exist',
                        False, f'BootOptions HTTP={code2}')

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebBootOrder();  obj.run()
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
