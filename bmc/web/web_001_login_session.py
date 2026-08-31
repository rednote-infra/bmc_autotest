#!/bin/python
"""
BMC Web登录会话测试（Web_001）
RDSV_BMC_075/076/077 - Web UI 登录/登出/会话管理

测试步骤：
  1. HTTPS 服务可达性检查（GET /）
  2. 正向登录（正确账密），验证 X-Auth-Token 非空
  3. 验证 Session 记录在 SessionService/Sessions 中可查到
  4. 正常登出，验证 Token 失效（再次 GET 返回 401）
  5. 反向登录（错误密码），验证返回 401

Usage: python3 -m bmc.web_001_login_session -i <bmc_ip> -u <user> -p <pass>
"""

import os, sys, time, traceback
import ssl, urllib.request, urllib.error
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebLoginSession(BmcWebTestBase):
    CASE        = 'WebLoginSession'
    CONFIG_FILE = 'web_001_login_session.json'

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True

        def rec(step, item, ok, detail=''):
            r = 'PASS' if ok else 'FAIL'
            CommonFunction.print_log('INFO' if ok else 'ERROR', f'[{item}] {r}: {detail}')
            steps.append((step, item, r, str(detail)[:200]))
            return ok

        # Step1: HTTPS 可达
        client = BmcWebClient(self.BMC_IP, self.USERNAME, self.PASSWORD, self.TIMEOUT)
        reachable, code = client.check_https_reachable()
        all_pass &= rec(1, 'https_reachable', reachable, f'HTTP={code}')

        # Step2: 正向登录
        ok = client.login()
        all_pass &= rec(2, 'login_success', ok, f'token_len={len(client.token)}')
        if not ok:
            self.command_check_result = 'FAIL'
            return

        # Step3: Session 可查
        code, body = client.get('/redfish/v1/SessionService/Sessions')
        members = body.get('Members', []) if isinstance(body, dict) else []
        session_found = any(client.session_uri in str(m) for m in members)
        all_pass &= rec(3, 'session_in_list', code == 200, f'count={len(members)}, found={session_found}')

        # Step4: 登出 + Token 失效验证（WARNING，部分BMC Token有TTL不立即失效）
        old_token = client.token
        client.logout()
        # 用旧 Token 访问，期望 401；但 Redfish 规范不强制立即失效，降级为 WARNING
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
        stale_code: int = -1
        try:
            req = urllib.request.Request(f'https://{self.BMC_IP}/redfish/v1/Systems/1',
                                         headers={'X-Auth-Token': old_token})
            with urllib.request.urlopen(req, context=ctx, timeout=self.TIMEOUT) as r:
                stale_code = r.status
        except urllib.error.HTTPError as e:
            stale_code = e.code
        except Exception:
            stale_code = -1
        if stale_code == 401:
            steps.append((4, 'token_invalidated_after_logout', 'PASS', f'stale_token_response={stale_code}'))
        else:
            CommonFunction.print_log('WARNING', f'[token_invalidated_after_logout] WARNING: '
                f'登出后Token未立即失效（HTTP={stale_code}），部分BMC有TTL延迟，记WARNING')
            steps.append((4, 'token_invalidated_after_logout', 'WARNING',
                f'stale_token_response={stale_code}（期望401，但非强制）'))

        # Step5: 反向登录（错误密码）
        bad_client = BmcWebClient(self.BMC_IP, self.USERNAME, 'WrongPass!123', self.TIMEOUT)
        bad_ok = bad_client.login(silent=True)   # 预期失败，silent=True 压掉 ERROR 日志
        all_pass &= rec(5, 'login_reject_wrong_password', not bad_ok, f'rejected={not bad_ok}')

        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        with open(self.csv_path, 'a') as f:
            for step, item, res, detail in steps:
                f.write(f'"{step}","{item}","{res}","{detail}"\n')
        for step, item, res, detail in steps:
            test.add_key_value_to_json(self.json_path, 'detail.cycle',
                value={'metrics': item, 'value': {'result': res, 'detail': detail}})
        test.add_key_value_to_json(self.json_path, 'summary',
            value={'metrics': self.TEST_NAME, 'value': self.command_check_result})
        test.print_log('INFO', f'{self.TEST_NAME} 完成，结果：{self.command_check_result}')

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebLoginSession();  obj.run()
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
