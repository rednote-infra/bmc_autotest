"""
BMC Web 测试公共客户端（方案A：HTTP接口层）
通过 Redfish Session 登录，模拟 Web 浏览器行为访问 BMC 接口。

用法：
    from func.web_client import BmcWebClient
    client = BmcWebClient(bmc_ip, username, password)
    client.login()
    data = client.get('/redfish/v1/Systems/1')
    client.logout()
"""

import json
import ssl
import time
import urllib.error
import urllib.request
from func.common_function import CommonFunction

class BmcWebClient:
    """BMC Web HTTP 客户端，基于 Redfish Session 认证"""

    SESSION_URI = '/redfish/v1/SessionService/Sessions'
    DEFAULT_TIMEOUT = 10

    def __init__(self, bmc_ip: str, username: str, password: str, timeout: int = DEFAULT_TIMEOUT):
        self.bmc_ip   = bmc_ip
        self.username = username
        self.password = password
        self.timeout  = timeout
        self.base_url = f'https://{bmc_ip}'
        self.token    = ''
        self.session_uri = ''
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE

    # ------------------------------------------------------------------
    # 认证
    # ------------------------------------------------------------------

    def login(self, silent: bool = False) -> bool:
        """登录，获取 X-Auth-Token。成功返回 True。
        silent=True 时登录失败只打 DEBUG，适用于反向测试（预期失败）场景。
        """
        payload = json.dumps({'UserName': self.username, 'Password': self.password}).encode()
        req = urllib.request.Request(
            self.base_url + self.SESSION_URI,
            data=payload,
            headers={'Content-Type': 'application/json'},
            method='POST',
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=self.timeout) as resp:
                self.token = resp.headers.get('X-Auth-Token', '')
                body = json.loads(resp.read())
                self.session_uri = body.get('@odata.id', '')
                if not silent:
                    CommonFunction.print_log('INFO', f'Web 登录成功，SessionURI={self.session_uri}')
                return bool(self.token)
        except Exception as e:
            level = 'DEBUG' if silent else 'ERROR'
            CommonFunction.print_log(level, f'Web 登录失败: {e}')
            return False

    def logout(self):
        """删除 Session，登出。"""
        if not self.session_uri:
            return
        try:
            req = urllib.request.Request(
                self.base_url + self.session_uri,
                headers={'X-Auth-Token': self.token},
                method='DELETE',
            )
            with urllib.request.urlopen(req, context=self._ctx, timeout=self.timeout):
                pass
            CommonFunction.print_log('INFO', 'Web 登出成功')
        except Exception as e:
            CommonFunction.print_log('WARNING', f'Web 登出异常（可忽略）: {e}')
        finally:
            self.token = ''
            self.session_uri = ''

    # ------------------------------------------------------------------
    # HTTP 方法
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, body=None, extra_headers=None):
        """发送 HTTP 请求，返回 (status_code, response_body_dict_or_str)"""
        headers = {'X-Auth-Token': self.token, 'Content-Type': 'application/json'}
        if extra_headers:
            headers.update(extra_headers)
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers=headers,
            method=method,
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx, timeout=self.timeout) as resp:
                raw = resp.read()
                try:
                    return resp.status, json.loads(raw)
                except Exception:
                    return resp.status, raw.decode(errors='ignore')
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, raw.decode(errors='ignore')
        except Exception as e:
            return -1, str(e)

    def get(self, path: str, extra_headers=None):
        return self._request('GET', path, extra_headers=extra_headers)

    def post(self, path: str, body=None):
        return self._request('POST', path, body=body)

    def patch(self, path: str, body=None):
        return self._request('PATCH', path, body=body)

    def delete(self, path: str):
        return self._request('DELETE', path)

    # ------------------------------------------------------------------
    # 工具方法
    # ------------------------------------------------------------------

    def check_https_reachable(self) -> tuple:
        """验证 HTTPS 服务可达（GET /），返回 (ok, http_code)"""
        try:
            req = urllib.request.Request(self.base_url + '/', method='GET')
            with urllib.request.urlopen(req, context=self._ctx, timeout=self.timeout) as resp:
                return True, resp.status
        except urllib.error.HTTPError as e:
            return True, e.code   # 有 HTTP 响应即可达
        except Exception as e:
            return False, -1

    def timed_get(self, path: str) -> tuple:
        """计时 GET，返回 (status_code, body, elapsed_sec)"""
        t0 = time.monotonic()
        code, body = self.get(path)
        elapsed = time.monotonic() - t0
        return code, body, elapsed
