#!/bin/python
"""
BMC Web UID灯控制（Web_007）
RDSV_BMC_050,051 - 查询/控制UID灯状态

Usage: python3 -m bmc.web_007_uid_control -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
import ssl, json as _json, urllib.request, urllib.error
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebUidControl(BmcWebTestBase):
    CASE        = 'WebUidControl'
    CONFIG_FILE = 'web_007_uid_control.json'

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

        chassis_uri = self.conf.get('ChassisUri', '/redfish/v1/Chassis/1')
        patch_uri   = self.conf.get('PatchUri', '/redfish/v1/Chassis/1')

        # GET Chassis，同时拿 ETag（浪潮等厂商 PATCH 需要 If-Match）
        _ctx = ssl.create_default_context(); _ctx.check_hostname = False; _ctx.verify_mode = ssl.CERT_NONE
        etag = ''
        code: int = -1;  body: dict = {}
        try:
            _req = urllib.request.Request(f'https://{self.BMC_IP}{chassis_uri}',
                headers={'X-Auth-Token': client.token})
            with urllib.request.urlopen(_req, context=_ctx, timeout=self.TIMEOUT) as r:
                etag = r.headers.get('ETag', '')
                body = _json.loads(r.read())
                code = r.status
        except urllib.error.HTTPError as e:
            code = e.code; body = {}
        except Exception:
            code = -1; body = {}

        all_pass = self._rec(steps, all_pass, 'chassis_accessible', code == 200, f'HTTP={code}')
        if not (code == 200 and isinstance(body, dict)):
            client.logout();  self.command_check_result = 'FAIL';  self._save(test, steps);  return

        init_led = body.get('IndicatorLED', body.get('LocationIndicatorActive', None))
        all_pass = self._rec(steps, all_pass, 'uid_state_readable', init_led is not None,
            f'IndicatorLED={init_led}')

        field = 'IndicatorLED' if 'IndicatorLED' in body else 'LocationIndicatorActive'
        target_on  = 'Lit' if field == 'IndicatorLED' else True
        target_off = 'Off' if field == 'IndicatorLED' else False

        # 带 If-Match 的 PATCH 方法（兼容需要 ETag 的厂商）
        def patch_with_etag(uri, payload, etag_val):
            extra = {'If-Match': etag_val} if etag_val else {}
            return client.patch(uri, payload) if not extra else client._request('PATCH', uri, payload, extra)

        # 正向：打开 UID
        code2, body2 = patch_with_etag(patch_uri, {field: target_on}, etag)
        ok_on = code2 in (200, 204)
        all_pass = self._rec(steps, all_pass, 'uid_turn_on', ok_on, f'PATCH HTTP={code2}')
        time.sleep(2)

        # 验证
        code3, body3 = client.get(chassis_uri)
        if code3 == 200 and isinstance(body3, dict):
            # 重新拿 ETag（PATCH 后 ETag 会变）
            try:
                _req2 = urllib.request.Request(f'https://{self.BMC_IP}{chassis_uri}',
                    headers={'X-Auth-Token': client.token})
                with urllib.request.urlopen(_req2, context=_ctx, timeout=self.TIMEOUT) as r2:
                    etag = r2.headers.get('ETag', etag)
            except Exception:
                pass
            new_led = body3.get(field)
            verified = (new_led == target_on) or (str(new_led).lower() in ('lit','true','blinking'))
            all_pass = self._rec(steps, all_pass, 'uid_on_verified', verified, f'{field}={new_led}')

        # 恢复：关闭 UID
        code4, _ = patch_with_etag(patch_uri, {field: target_off}, etag)
        self._rec(steps, True, 'uid_restore', code4 in (200, 204), f'restore HTTP={code4}')

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebUidControl();  obj.run()
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
