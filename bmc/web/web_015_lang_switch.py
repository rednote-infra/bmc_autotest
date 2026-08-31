#!/bin/python
"""
BMC Web中英文切换（Web_015）
验证 Accept-Language 响应正常，Web UI 支持中英文

Usage: python3 -m bmc.web_015_lang_switch -i <bmc_ip> -u <user> -p <pass>
"""
import os, sys, time, traceback
import ssl, urllib.request, urllib.error
sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))
from func.common_function import CommonFunction
from func.bmc_web_test_base import BmcWebTestBase
from func.web_client import BmcWebClient


class WebLangSwitch(BmcWebTestBase):
    CASE        = 'WebLangSwitch'
    CONFIG_FILE = 'web_015_lang_switch.json'

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

        test_uris = self.conf.get('TestUris', ['/redfish/v1/'])
        lang_headers = self.conf.get('LangHeaders', {'zh': 'zh-CN,zh;q=0.9', 'en': 'en-US,en;q=0.9'})

        # 1. Web UI 主页在两种语言 header 下均可访问（HTTP 200）
        ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE

        for lang, accept_val in lang_headers.items():
            code: int = -1;  content_lang: str = ''
            try:
                req = urllib.request.Request(f"https://{self.BMC_IP}/",
                    headers={'Accept-Language': accept_val})
                with urllib.request.urlopen(req, context=ctx, timeout=self.TIMEOUT) as r:
                    code = r.status
                    content_lang = r.headers.get('Content-Language', '')
            except urllib.error.HTTPError as e:
                code = e.code;  content_lang = ''
            except Exception:
                code = -1;  content_lang = ''
            all_pass = self._rec(steps, all_pass, f'webui_lang_{lang}',
                code == 200, f'lang={lang}, HTTP={code}, Content-Language={content_lang}')

        # 2. Redfish 接口在两种语言 header 下均正常返回 JSON
        for lang, accept_val in lang_headers.items():
            for uri in test_uris[:2]:
                code, body = client.get(uri, extra_headers={'Accept-Language': accept_val})
                ok = code == 200 and isinstance(body, dict)
                all_pass = self._rec(steps, all_pass, f'redfish_{lang}_{uri.split("/")[-1] or "root"}',
                    ok, f'HTTP={code}')

        # 3. 验证 AccountService 语言相关字段（如有）
        code3, body3 = client.get('/redfish/v1/AccountService')
        if code3 == 200 and isinstance(body3, dict):
            lang_field = body3.get('Language', body3.get('Oem',{}).get('Public',{}).get('Language',''))
            if lang_field:
                steps.append(('language_field_found', 'PASS', f'Language={lang_field}'))
            else:
                steps.append(('language_field_found', 'WARNING',
                    '未找到Language字段（非必须，Redfish标准不强制）'))

        client.logout()
        self.command_check_result = 'PASS' if all_pass else 'FAIL'
        self._save(test, steps)

if __name__ == '__main__':
    obj = None
    exit_code = 1
    try:
        obj = WebLangSwitch();  obj.run()
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
