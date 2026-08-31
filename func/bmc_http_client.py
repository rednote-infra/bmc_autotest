"""
bmc_http_client.py — 轻量级 Redfish HTTP 客户端（urllib.request，无第三方依赖）

用于 event_xxx、protocol_xxx 等直接操作 HTTP 响应头/状态码的脚本。
与 redfish_sdk 无关，可在目标机 Python 3.6+ 及 venv 3.9 中运行。

Author: Fengmian
Date: 2026/05/14
"""

import base64
import json
import logging
import ssl
import urllib.error
import urllib.request

class BmcHttpClient:
    """Redfish HTTP 客户端

    用法：
        client = BmcHttpClient(bmc_ip, username, password, logger)
        token, session_loc = client.login()
        sc, body, headers = client.request("GET", "/redfish/v1/EventService", token=token)
        client.logout(session_loc, token)

    request() 返回值：
        sc      — HTTP 状态码（int），连接失败时为 -1
        body    — 解析后的 dict/list，或原始字符串；连接失败时为错误信息字符串
        headers — 响应头字典（全小写 key）；连接失败时为 {}
    """

    def __init__(self, bmc_ip: str, username: str, password: str,
                 logger: logging.Logger = None, timeout: int = 30):
        self.bmc_ip   = bmc_ip
        self.username = username
        self.password = password
        self.log      = logger or logging.getLogger(__name__)
        self.timeout  = timeout
        self.base_url = f"https://{bmc_ip}"
        self._ctx     = self._build_ctx()

    @staticmethod
    def _build_ctx():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode    = ssl.CERT_NONE
        return ctx

    # ─────────────────────────────────────────────────────
    # 核心 HTTP 方法
    # ─────────────────────────────────────────────────────
    def request(self, method: str, path: str, body=None, token: str = None,
                basic_auth: tuple = None, extra_headers: dict = None):
        """发送请求，返回 (status_code, body, headers_dict)"""
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-Auth-Token"] = token
        elif basic_auth:
            cred = base64.b64encode(
                f"{basic_auth[0]}:{basic_auth[1]}".encode()
            ).decode()
            headers["Authorization"] = f"Basic {cred}"
        if extra_headers:
            headers.update(extra_headers)

        data = json.dumps(body).encode() if body is not None else None
        req  = urllib.request.Request(
            self.base_url + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx,
                                        timeout=self.timeout) as resp:
                raw  = resp.read()
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                try:
                    return resp.status, json.loads(raw), hdrs
                except Exception:
                    return resp.status, raw.decode(errors="ignore"), hdrs
        except urllib.error.HTTPError as e:
            raw  = e.read()
            hdrs = {k.lower(): v for k, v in e.headers.items()}
            try:
                return e.code, json.loads(raw), hdrs
            except Exception:
                return e.code, raw.decode(errors="ignore"), hdrs
        except Exception as ex:
            self.log.error(f"HTTP {method} {path} 连接异常：{ex}")
            return -1, str(ex), {}

    # ─────────────────────────────────────────────────────
    # Session 管理
    # ─────────────────────────────────────────────────────
    def login(self):
        """创建 Redfish Session，返回 (token, session_uri)；失败返回 (None, None)"""
        sc, body, hdrs = self.request(
            "POST", "/redfish/v1/SessionService/Sessions",
            body={"UserName": self.username, "Password": self.password}
        )
        if sc == 201:
            token = hdrs.get("x-auth-token", "")
            loc   = hdrs.get("location", "")
            if not token and isinstance(body, dict):
                token = body.get("Token", "")
            self.log.info(f"Session 创建成功，Location={loc}")
            return token, loc
        self.log.error(f"Session 创建失败，HTTP {sc}")
        return None, None

    def logout(self, session_loc: str, token: str):
        """删除 Session"""
        if session_loc and token:
            sc, _, _ = self.request("DELETE", session_loc, token=token)
            self.log.info(f"Session 已删除（HTTP {sc}）")
