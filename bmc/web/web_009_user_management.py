#!/bin/python
"""
BMC Web用户管理（Web_009）
RDSV_BMC_080~084 - 增删改查用户/禁用

Usage: python3 -m bmc.web_009_user_management -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
import ssl as _ssl, urllib.request as _urlreq, urllib.error as _urlerr
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebUserManagement(BmcWebTestBase):
    CASE        = 'WebUserManagement'
    CONFIG_FILE = 'web_009_user_management.json'

    def _load_extra_config(self, conf: dict) -> None:
        self.conf = conf

    def run(self):
        test = CommonFunction()
        steps = [];  all_pass = True
        created_uri = None
        client = BmcWebClient(self.BMC_IP, self.USERNAME, self.PASSWORD, self.TIMEOUT)
        if not client.login():
            self._rec(steps, all_pass, 'login', False, '登录失败')
            self.command_check_result = 'FAIL'
            self._save(test, steps);  return

        accounts_uri = self.conf.get('AccountsUri', '/redfish/v1/AccountService/Accounts')
        test_user = self.conf.get('TestUser', {})

        # Step1：查询用户列表
        code, body = client.get(accounts_uri)
        all_pass = self._rec(steps, all_pass, 'list_accounts', code == 200,
            f'HTTP={code}, count={len(body.get("Members",[])) if isinstance(body,dict) else 0}')

        # Step2：POST 新建用户（ZTE 支持 POST /Accounts 返回201，不用写死槽位）
        code2, body2 = client.post(accounts_uri, {
            'UserName': test_user.get('UserName', 'web_test_u'),
            'Password': test_user.get('Password', 'WebTest@2024'),
            'RoleId':   test_user.get('RoleId', 'Operator'),
            'Enabled':  True,
        })
        ok_create = code2 in (200, 201)
        all_pass = self._rec(steps, all_pass, 'create_user', ok_create, f'HTTP={code2}')
        if ok_create and isinstance(body2, dict):
            created_uri = body2.get('@odata.id', '')
        time.sleep(1)

        # Step3：验证新用户存在
        if created_uri:
            code3, body3 = client.get(created_uri)
            if code3 == 200 and isinstance(body3, dict):
                uname = body3.get('UserName', '')
                all_pass = self._rec(steps, all_pass, 'verify_created',
                    uname == test_user.get('UserName', 'web_test_u'), f'UserName={uname}')

        # Step4：禁用用户（部分厂商如浪潮 PATCH 需要 If-Match ETag）
        if created_uri:
            # 先 GET 拿 ETag
            _ctx2 = _ssl.create_default_context(); _ctx2.check_hostname = False; _ctx2.verify_mode = _ssl.CERT_NONE
            etag = ''
            try:
                _r = _urlreq.Request(f'https://{self.BMC_IP}{created_uri}',
                    headers={'X-Auth-Token': client.token})
                with _urlreq.urlopen(_r, context=_ctx2, timeout=self.TIMEOUT) as _resp:
                    etag = _resp.headers.get('ETag', '')
            except Exception:
                pass
            extra = {'If-Match': etag} if etag else {}
            code4, _ = client._request('PATCH', created_uri, {'Enabled': False}, extra_headers=extra or None)
            all_pass = self._rec(steps, all_pass, 'disable_user', code4 in (200, 204), f'HTTP={code4}')
            time.sleep(1)
            code5, body5 = client.get(created_uri)
            if code5 == 200 and isinstance(body5, dict):
                enabled = body5.get('Enabled', True)
                all_pass = self._rec(steps, all_pass, 'verify_disabled', not enabled, f'Enabled={enabled}')

        # Cleanup：DELETE 新建的用户
        if created_uri:
            code_del, _ = client.delete(created_uri)
            CommonFunction.print_log('INFO', f'cleanup DELETE {created_uri} -> HTTP={code_del}')

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebUserManagement();  obj.run()
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
