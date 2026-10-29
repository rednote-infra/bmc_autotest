#!/bin/python
"""
Author: Fengmian
Date: 2026/05/14
Update:
2026/10/16: SDK 1.3.0 适配 - 硬编码 URI 改为 SDK/ServiceRoot 动态发现（issue/8）
Usage: python3 bmc/protocol_001_dmtf_compliance.py -i <bmc_ip> -u <username> -p <password>

测试内容：DMTF Redfish 协议合规性检查（RDSV_BMC_147）

参考规范：
  - 小红书内部规范：https://docs.xiaohongshu.com/doc/e4c5a98fadd391972a88ffeb0d6a69bc
  - DMTF 官方规范：https://redfish.dmtf.org/schemas/DSP0266_1.14.0.html

记录级别说明：
  ERROR   — 文档明确要求但 BMC 未实现，直接导致最终 FAIL
  WARNING — 通用 DMTF 规范要求但公司文档未特别列出，建议整改
  INFO    — 符合规范或非关键信息，不影响结论

覆盖检查项（共 8 大类 30+ 项）：
  1. Redfish Version      — 根节点可访问、RedfishVersion 字段合规
  2. ETags                — 按资源实际能力检查可选 ETag / If-Match 行为
  3. HTTP Method 合规     — GET/POST/PATCH/DELETE 各自幂等性及支持情况
  4. HTTP Header 合规     — Content-Type、Authorization 校验、If-Match 响应
  5. HTTP StatusCode 合规 — 各场景状态码正确（200/201/204/400/401/403/404/405/428 等）
  6. HTTP Request 合规    — PATCH 空 JSON 不报错、Boot 只传 Boot 属性
  7. HTTP Response 合规   — Action 204+空/200+error、400/500 必须有 error response 结构
  8. 通用 DMTF 补充       — OData-Version header、必填 @odata 字段、Allow/Location header

PASS 标准：
  所有 ERROR 项全部通过，整体 PASS；任意 ERROR 项不通过则最终 FAIL。
  WARNING 不导致 FAIL，但在报告中单独列出供参考。
"""

import json
import logging
import os
import base64
import sys
import ssl
import traceback
import urllib.error
import urllib.parse
import urllib.request

sys.path.append(os.path.join(os.path.dirname(__file__), "../.."))

from func.bmc_event_test_base import BmcEventTestBase
from func.redfish_event_compat import resolve_test_message
try:
    from redfish_sdk import RedfishClient
except ImportError:  # 允许不安装设备 SDK 时运行纯离线合规测试
    RedfishClient = None

# ─────────────────────────────────────────────────────────
# 常量
# ─────────────────────────────────────────────────────────
LEVEL_ERROR   = "ERROR"
LEVEL_WARNING = "WARNING"
LEVEL_INFO    = "INFO"

RESULT_PASS    = "PASS"
RESULT_FAIL    = "FAIL"
RESULT_SKIPPED = "SKIPPED"
RESULT_BLOCKED = "BLOCKED"


class Protocol001DmtfCompliance(BmcEventTestBase):
    CASE_KEY      = 'Protocol001DmtfCompliance'
    TEST_NAME     = 'DMTF Redfish 协议合规性检查'
    TEST_NUM      = 'RDSV_BMC_147'
    LOG_BASE_NAME = 'protocol_001_dmtf_compliance'
    CONFIG_FILE   = 'protocol_001_dmtf_compliance.json'
    CONF_DIR      = 'protocol'

    def __init__(self) -> None:
        # 运行时属性预声明（_load_extra_config / _discover_uris 中赋值）
        self.findings             = []
        self._session_create_sc   = -1
        self._session_location    = ''
        self._session_location_ok = False
        self._session_delete_sc   = -1
        self._session_create_evidence = {}
        self._session_delete_evidence = {}
        self.BASE_URL      = ''
        self.timeout       = 30
        # 资源 URI 一律运行时从 ServiceRoot 动态发现（conf 显式配置可覆盖）
        self.chassis_uri   = ''
        self.systems_uri   = ''
        self.power_uri     = ''
        self.sessions_uri  = ''
        self.managers_uri  = ''
        self.accounts_uri  = ''
        self.event_svc_uri = ''
        self._ctx          = None
        self._last_evidence = {}
        super().__init__()

    """DMTF Redfish 协议合规性检查"""

    # ─────────────────────────────────────────────────────
    # 初始化
    # ─────────────────────────────────────────────────────
    def _load_extra_config(self, conf: dict) -> None:
        """从配置文件读取协议测试专属参数并初始化 HTTP 上下文。

        URI 类配置项仅在显式配置时覆盖（默认全部运行时动态发现）。
        """
        self.timeout      = int(conf.get("TimeoutSeconds", 30))
        self.chassis_uri  = conf.get("ChassisUri")  or ''
        self.systems_uri  = conf.get("SystemsUri")  or ''
        self.power_uri    = conf.get("PowerUri")    or ''
        self.sessions_uri = conf.get("SessionsUri") or ''
        self.managers_uri = conf.get("ManagersUri") or ''
        self.BASE_URL     = f"https://{self.BMC_IP}"
        self._build_ctx()

    def _discover_uris(self) -> bool:
        """从 ServiceRoot 动态发现资源 URI。

        /redfish/v1 是 DMTF 唯一强约定入口，其余 URI 全部从 root 的
        Links / 顶层服务链接 / 集合 Members 逐级发现，不硬编码任何资源路径。
        conf 显式配置的 URI 只有在集合确实发布该成员时才可用于消歧。
        """
        sc, root, _ = self._request("GET", "/redfish/v1")
        if sc != 200 or not isinstance(root, dict):
            self._record("从 ServiceRoot 发现资源", LEVEL_ERROR, False,
                         f"ServiceRoot 返回 HTTP {sc}，无法继续资源发现",
                         result=RESULT_BLOCKED)
            return False
        links = root.get("Links", {}) or {}

        def _link(key: str) -> str:
            # 集合 URI 优先取 root 顶层服务链接（Systems/Chassis/Managers 等），
            # Links 中不存在时回落（Links.Sessions 等仅在 Links 中）
            top = (root.get(key) or {}).get("@odata.id", "")
            return top or (links.get(key) or {}).get("@odata.id", "")

        def _member(col_uri: str, configured: str, name: str) -> str:
            """仅从集合发布的成员中选择；多成员必须由配置显式消歧。"""
            if not col_uri:
                self._record(f"发现 {name} 集合", LEVEL_ERROR, False,
                             f"ServiceRoot 未发布 {name} 集合链接",
                             result=RESULT_BLOCKED)
                return ""
            member_sc, body, _ = self._request(
                "GET", col_uri, basic_auth=(self.USERNAME, self.PASSWORD)
            )
            members = [m.get("@odata.id", "") for m in (body.get("Members") or [])
                       if isinstance(m, dict) and m.get("@odata.id")] \
                if isinstance(body, dict) else []
            if member_sc != 200 or not members:
                self._record(f"发现 {name} 成员", LEVEL_ERROR, False,
                             f"GET {col_uri} 返回 HTTP {member_sc}，Members={members!r}",
                             result=RESULT_BLOCKED)
                return ""
            if configured:
                if configured in members:
                    return configured
                self._record(f"验证配置的 {name} URI", LEVEL_ERROR, False,
                             f"配置值 {configured!r} 不在服务发布的成员中：{members!r}",
                             result=RESULT_BLOCKED)
                return ""
            if len(members) == 1:
                return members[0]
            self._record(f"选择 {name} 成员", LEVEL_ERROR, False,
                         f"集合发布了多个成员，需配置 {name}Uri 显式选择：{members!r}",
                         result=RESULT_BLOCKED)
            return ""

        # 集合 URI（root.Links 指向集合本身）
        systems_col = _link("Systems")
        chassis_col = _link("Chassis")
        managers_col = _link("Managers")

        # 成员 URI（单成员自动选择；多成员只能用已发布的配置值消歧）
        self.systems_uri = _member(systems_col, self.systems_uri, "Systems")
        self.chassis_uri = _member(chassis_col, self.chassis_uri, "Chassis")
        self.managers_uri = _member(managers_col, self.managers_uri, "Managers")
        if not self.sessions_uri:
            self.sessions_uri = _link("Sessions")
        if not self.sessions_uri:
            self._record("发现 Sessions 集合", LEVEL_ERROR, False,
                         "ServiceRoot 未发布 Sessions 链接", result=RESULT_BLOCKED)

        # Power 子资源从 Chassis 的 Power 链接发现
        if not self.power_uri and self.chassis_uri:
            _, ch_body, _ = self._request(
                "GET", self.chassis_uri, basic_auth=(self.USERNAME, self.PASSWORD)
            )
            if isinstance(ch_body, dict):
                self.power_uri = (ch_body.get("Power") or {}).get("@odata.id", "")

        # AccountService/Accounts、EventService 从 root 顶层服务链接发现
        acct_uri = (root.get("AccountService") or {}).get("@odata.id", "")
        if acct_uri and not self.accounts_uri:
            _, acct_body, _ = self._request(
                "GET", acct_uri, basic_auth=(self.USERNAME, self.PASSWORD)
            )
            self.accounts_uri = (acct_body.get("Accounts") or {}).get("@odata.id", "") \
                if isinstance(acct_body, dict) else ""
        if not self.event_svc_uri:
            self.event_svc_uri = (root.get("EventService") or {}).get("@odata.id", "")

        self.log.info(
            f"URI 动态发现完成：Systems={self.systems_uri}, Chassis={self.chassis_uri}, "
            f"Managers={self.managers_uri}, Sessions={self.sessions_uri}, "
            f"Power={self.power_uri}, Accounts={self.accounts_uri}"
        )
        return bool(self.systems_uri and self.chassis_uri and
                    self.managers_uri and self.sessions_uri)

    def _build_ctx(self):
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode    = ssl.CERT_NONE

    # ─────────────────────────────────────────────────────
    # HTTP 工具（带响应头返回）
    # ─────────────────────────────────────────────────────
    def _request(self, method, path, body=None, extra_headers=None,
                 token=None, basic_auth=None):
        """
        发送 HTTP 请求，返回 (status_code, body_dict_or_str, resp_headers_dict)
        resp_headers_dict: 全小写 key
        """
        if not path:
            self._last_evidence = {
                "method": method, "uri": path, "status": None,
                "response": "resource URI was not discovered", "headers": {},
            }
            return -1, "resource URI was not discovered", {}
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
            self.BASE_URL + path, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, context=self._ctx,
                                        timeout=self.timeout) as resp:
                raw  = resp.read()
                hdrs = {k.lower(): v for k, v in resp.headers.items()}
                try:
                    parsed = json.loads(raw)
                except Exception:
                    parsed = raw.decode(errors="ignore")
                self._capture_evidence(method, path, resp.status, parsed, hdrs)
                return resp.status, parsed, hdrs
        except urllib.error.HTTPError as e:
            raw  = e.read()
            hdrs = {k.lower(): v for k, v in e.headers.items()}
            try:
                parsed = json.loads(raw)
            except Exception:
                parsed = raw.decode(errors="ignore")
            self._capture_evidence(method, path, e.code, parsed, hdrs)
            return e.code, parsed, hdrs
        except Exception as ex:
            self._capture_evidence(method, path, -1, str(ex), {})
            return -1, str(ex), {}

    @staticmethod
    def _redact(value):
        """保留原始响应证据结构，同时屏蔽凭据和会话令牌。"""
        sensitive = {"password", "token", "x-auth-token", "authorization"}
        if isinstance(value, dict):
            return {k: ("<redacted>" if str(k).lower() in sensitive
                        else Protocol001DmtfCompliance._redact(v))
                    for k, v in value.items()}
        if isinstance(value, list):
            return [Protocol001DmtfCompliance._redact(v) for v in value]
        return value

    def _capture_evidence(self, method, uri, status, response, headers):
        self._last_evidence = {
            "method": method,
            "uri": uri,
            "status": status,
            "response": self._redact(response),
            "headers": self._redact(headers),
        }

    def _build_submit_test_event_body(self, ev_body, token):
        """按 ActionInfo 和设备 Registry 构造合法请求，兼容旧式 EventType。"""
        action = next((value for key, value in ev_body.get("Actions", {}).items()
                       if "SubmitTestEvent" in key and isinstance(value, dict)), {})
        parameters = {}
        info_uri = action.get("@Redfish.ActionInfo")
        if info_uri:
            sc, info, _ = self._request("GET", info_uri, token=token)
            if sc != 200 or not isinstance(info, dict):
                raise ValueError(f"读取 SubmitTestEvent ActionInfo 失败：HTTP {sc}")
            parameters = {p["Name"]: p for p in info.get("Parameters", [])
                          if isinstance(p, dict) and p.get("Name")}

        required = {name for name, p in parameters.items() if p.get("Required")}
        unsupported = required - {"MessageId", "MessageArgs", "EventType"}
        if unsupported:
            raise ValueError(f"无法自动构造必填参数：{sorted(unsupported)}")

        body = {}
        if "MessageId" in parameters:
            if RedfishClient is None:
                raise ValueError("redfish-python-sdk 未安装，无法解析测试 MessageId")
            client = RedfishClient(self.BMC_IP, self.USERNAME, self.PASSWORD,
                                   read_timeout=self.timeout)
            try:
                message_id, message_args = resolve_test_message(client)
            finally:
                client.close()
            body["MessageId"] = message_id
            if "MessageArgs" in parameters and (message_args or "MessageArgs" in required):
                body["MessageArgs"] = message_args

        if not body or "EventType" in required:
            allowable = (parameters.get("EventType", {}).get("AllowableValues")
                         or action.get("EventType@Redfish.AllowableValues"))
            if "EventType" not in parameters and not allowable:
                raise ValueError("设备未声明可用的 MessageId 或 EventType 参数")
            body["EventType"] = ("StatusChange" if not allowable or "StatusChange" in allowable
                                 else allowable[0])
        return body

    # ─────────────────────────────────────────────────────
    # ETag 辅助：同时从响应体和响应头取 ETag
    # ─────────────────────────────────────────────────────
    def _get_etag(self, uri: str, token: str) -> str:
        """GET uri，从响应体 @odata.etag 或响应头 ETag 取值，返回字符串（可能为空）。"""
        sc, body, hdrs = self._request("GET", uri, token=token)
        etag = ""
        if sc == 200:
            if isinstance(body, dict):
                etag = body.get("@odata.etag") or hdrs.get("etag") or ""
            else:
                etag = hdrs.get("etag") or ""
        return str(etag).strip()

    @staticmethod
    def _is_write_denied(status: int) -> bool:
        """Return whether a protected write was rejected by authorization.

        Some Redfish implementations return 401 instead of 403 when valid
        read-only credentials are used for a write.  A preceding authenticated
        GET proves the credentials are valid, so either response demonstrates
        that the write privilege was denied.
        """
        return status in (401, 403)

    # ─────────────────────────────────────────────────────
    # 检查结果记录
    # ─────────────────────────────────────────────────────
    def _record(self, item, level, passed, detail, result=None, evidence=None):
        if result is None:
            result = RESULT_PASS if passed else (
                RESULT_FAIL if level == LEVEL_ERROR else RESULT_SKIPPED
            )
        entry = {
            "item": item, "level": level, "result": result, "detail": detail,
            "evidence": dict(evidence if evidence is not None else self._last_evidence),
        }
        self.findings.append(entry)
        if result == RESULT_PASS:
            self.log.info(f"✅ {item}: {detail}")
        elif result == RESULT_FAIL:
            self.log.error(f"❌ {item}: {detail}")
        elif result == RESULT_BLOCKED:
            self.log.warning(f"⛔ {item}: {detail}")
        elif result == RESULT_SKIPPED:
            self.log.warning(f"⚠️  {item}: {detail}")
        else:
            self.log.info(f"ℹ️  {item}: {detail}")

    # ─────────────────────────────────────────────────────
    # 检查模块
    # ─────────────────────────────────────────────────────

    # ── 1. Redfish Version ──────────────────────────────
    def check_redfish_version(self):
        self.log.info("=" * 60)
        self.log.info("【1】Redfish Version 检查")

        # /redfish/v1 是 DMTF 强约定的 ServiceRoot 入口（唯一允许的固定路径）
        # DSP0266: ServiceRoot 必须允许匿名 GET；其他资源仍须认证。
        sc, body, hdrs = self._request("GET", "/redfish/v1")
        # 1-1 根节点可访问
        ok = sc == 200
        self._record("根节点 /redfish/v1 可访问（200）", LEVEL_ERROR, ok, f"HTTP {sc}")
        if not ok or not isinstance(body, dict):
            return

        # 1-2 RedfishVersion 字段存在
        ver = body.get("RedfishVersion", "")
        self._record("RedfishVersion 字段存在", LEVEL_ERROR, bool(ver),
                     f"RedfishVersion={ver!r}")

        # 1-3 版本格式 X.Y.Z（通用 DMTF）
        if ver:
            parts = ver.split(".")
            ok_fmt = len(parts) == 3 and all(p.isdigit() for p in parts)
            self._record("RedfishVersion 格式为 X.Y.Z", LEVEL_WARNING, ok_fmt,
                         f"RedfishVersion={ver!r}")

        # 1-4 @odata.id / @odata.type（DMTF 必填字段）
        for field in ("@odata.id", "@odata.type"):
            self._record(f"根节点包含 {field}（DMTF 必填）", LEVEL_WARNING,
                         field in body, f"{field}={body.get(field)!r}")

        # 1-5 OData-Version 响应头（DMTF 通用）
        odata_ver = hdrs.get("odata-version", "")
        self._record("响应头包含 OData-Version（DMTF 通用）", LEVEL_WARNING,
                     bool(odata_ver), f"OData-Version={odata_ver!r}")

        self.log.info(f"  RedfishVersion={ver}")

    # ── 2. ETags ────────────────────────────────────────
    def check_etags(self, token):
        self.log.info("=" * 60)
        self.log.info("【2】ETags 检查")

        for label, uri in [("Chassis", self.chassis_uri),
                            ("ComputerSystem", self.systems_uri)]:
            # 2-1 GET 拿到 @odata.etag
            sc, body, hdrs = self._request("GET", uri, token=token)
            etag = None
            if isinstance(body, dict):
                etag = body.get("@odata.etag") or hdrs.get("etag")
            if sc != 200:
                self._record(f"GET {label} 以检查 ETag", LEVEL_ERROR, False,
                             f"HTTP {sc}")
                continue
            if not etag:
                self._record(f"{label} 未声明 ETag（可选能力）", LEVEL_INFO, False,
                             f"{uri} 未返回 @odata.etag 或 ETag；条件 PATCH 检查不适用",
                             result=RESULT_SKIPPED)
                continue
            self._record(f"GET {label} 返回 ETag", LEVEL_INFO, True,
                         f"HTTP {sc}, etag={etag!r}")

            # 428 是服务可选的并发控制策略：接受正常 PATCH 或 428。
            sc_no_etag, _, _ = self._request("PATCH", uri, body={}, token=token)
            if sc_no_etag == 405:
                self._record(f"PATCH {label} 的 ETag 前置条件检查", LEVEL_INFO, False,
                             f"{uri} 不支持 PATCH（HTTP 405），该操作不适用",
                             result=RESULT_SKIPPED)
                continue
            self._record(f"PATCH {label} 不带 If-Match 的响应合规", LEVEL_ERROR,
                         sc_no_etag in (200, 204, 428),
                         f"HTTP {sc_no_etag}（200/204=未强制；428=要求前置条件）")

            sc_with, _, _ = self._request(
                "PATCH", uri, body={}, token=token,
                extra_headers={"If-Match": etag}
            )
            self._record(f"PATCH {label} 带正确 If-Match → 200/204", LEVEL_ERROR,
                         sc_with in (200, 204), f"实际 HTTP {sc_with}")

    # ── 3. HTTP Method 合规 ──────────────────────────────
    def check_http_methods(self, token):
        self.log.info("=" * 60)
        self.log.info("【3】HTTP Method 合规检查")

        # 3-1 GET 支持且幂等
        sc1, b1, _ = self._request("GET", self.systems_uri, token=token)
        sc2, b2, _ = self._request("GET", self.systems_uri, token=token)
        self._record("GET 必须支持（200）", LEVEL_ERROR,
                     sc1 == 200, f"HTTP {sc1}")
        if sc1 == 200 and sc2 == 200 and isinstance(b1, dict) and isinstance(b2, dict):
            self._record("GET 必须幂等（两次 Id 一致）", LEVEL_ERROR,
                         b1.get("Id") == b2.get("Id"),
                         f"Id: {b1.get('Id')!r} vs {b2.get('Id')!r}")

        # 3-2 PATCH 支持（先 GET ETag，携带 If-Match 再 PATCH）
        etag = self._get_etag(self.chassis_uri, token)
        if not etag:
            self.log.warning("  3-2: 未能获取 ETag，不携带 If-Match 的 PATCH 将被 BMC 以 428 拒绝，降级记 WARNING")
            self._record("PATCH 必须支持（200/204）", LEVEL_WARNING,
                         False, "无法获取 @odata.etag / ETag 响应头，PATCH 跳过（ETag 未暴露）")
        else:
            sc_patch, _, _ = self._request(
                "PATCH", self.chassis_uri, body={}, token=token,
                extra_headers={"If-Match": etag}
            )
            self._record("PATCH 必须支持（200/204）", LEVEL_ERROR,
                         sc_patch in (200, 204), f"HTTP {sc_patch}")

        # 3-3 POST 必须支持 + 不幂等（文档明确：POST 必须不幂等）
        #     连续两次 POST 创建 Session → 应产生两个不同资源（不同 Id 或 Location）
        self._record("POST 必须支持（Session 创建 201 已验证）", LEVEL_ERROR,
                     self._session_create_sc == 201,
                     f"HTTP {self._session_create_sc}",
                     evidence=self._session_create_evidence)
        sc_p1, body_p1, hdrs_p1 = self._request(
            "POST", self.sessions_uri,
            body={"UserName": self.USERNAME, "Password": self.PASSWORD}
        )
        sc_p2, body_p2, hdrs_p2 = self._request(
            "POST", self.sessions_uri,
            body={"UserName": self.USERNAME, "Password": self.PASSWORD}
        )
        loc1 = hdrs_p1.get("location", "")
        loc2 = hdrs_p2.get("location", "")
        tok1 = hdrs_p1.get("x-auth-token", "")
        tok2 = hdrs_p2.get("x-auth-token", "")
        post_not_idempotent = (
            sc_p1 == 201 and sc_p2 == 201 and loc1 != loc2
        )
        self._record("POST 必须不幂等（两次创建 Session → 不同 Location）", LEVEL_ERROR,
                     post_not_idempotent,
                     f"loc1={loc1!r}, loc2={loc2!r}, 是否不同={loc1 != loc2}")
        # 清理两个临时 Session
        for loc, tok in [(loc1, tok1), (loc2, tok2)]:
            if loc and (token or tok):
                self._request("DELETE", loc, token=token or tok)

        # 3-4 DELETE 必须支持（Session 删除 204 已验证）
        self._record("DELETE 必须支持（Session 删除 204 已验证）", LEVEL_ERROR,
                     self._session_delete_sc == 204,
                     f"HTTP {self._session_delete_sc}",
                     evidence=self._session_delete_evidence)

        # 3-5 PUT 非必须，但 405 = 明确不支持（非 501 的话 WARNING）
        sc_put, _, _ = self._request("PUT", self.managers_uri, body={}, token=token)
        self._record("PUT（非必须）— 返回非 405", LEVEL_WARNING,
                     sc_put != 405,
                     f"HTTP {sc_put}（405=明确拒绝；501=功能未实现，可接受）")

        # 3-6 HEAD 非必须
        sc_head, _, _ = self._request("HEAD", self.systems_uri, token=token)
        self._record("HEAD（非必须）— 返回非 405", LEVEL_WARNING,
                     sc_head != 405, f"HTTP {sc_head}")

    # ── 4. HTTP Header 合规 ──────────────────────────────
    def check_http_headers(self, token):
        self.log.info("=" * 60)
        self.log.info("【4】HTTP Header 合规检查")

        # 4-1 响应 Content-Type 含 application/json
        sc, _, hdrs = self._request("GET", self.systems_uri, token=token)
        ct = hdrs.get("content-type", "")
        self._record("GET 响应 Content-Type 含 application/json", LEVEL_ERROR,
                     "application/json" in ct, f"Content-Type={ct!r}")

        # ServiceRoot 是认证例外；其他受保护资源仍应要求认证。
        sc_root, _, _ = self._request("GET", "/redfish/v1")
        self._record("无凭据访问 ServiceRoot → 200（认证例外）", LEVEL_ERROR,
                     sc_root == 200, f"HTTP {sc_root}（期望 200）")

        # 无凭据访问受保护资源 → 401
        sc_401, _, _ = self._request("GET", self.systems_uri)
        self._record("无凭据访问 ComputerSystem → 401", LEVEL_ERROR,
                     sc_401 == 401, f"HTTP {sc_401}（期望 401）")

        # 4-3 错误凭据 → 401
        sc_bad, _, _ = self._request("GET", self.systems_uri,
                                     basic_auth=("invalid_xyz", "bad_pwd_xyz"))
        self._record("错误凭据 → 401", LEVEL_ERROR,
                     sc_bad == 401, f"HTTP {sc_bad}（期望 401）")

        # If-Match / 428 的可选能力已按具体资源和操作在 ETag 模块检查。

        # 4-5 POST 创建资源响应包含 Location header（DMTF 通用）
        self._record("POST 创建 Session 响应包含 Location header", LEVEL_WARNING,
                     self._session_location_ok,
                     f"Location={self._session_location!r}",
                     evidence=self._session_create_evidence)

        # 4-6 If-None-Match 非必须（文档列出，验证 BMC 对其态度）
        # 带 If-None-Match: * 做 GET → 304（资源存在，条件不满足）或 200（BMC 忽略此 Header）
        sc_inm, _, _ = self._request(
            "GET", self.systems_uri, token=token,
            extra_headers={"If-None-Match": "*"}
        )
        # 304 = 符合规范；200 = BMC 忽略 If-None-Match（可接受，非必须）；其他视为异常
        ok_inm = sc_inm in (200, 304)
        self._record("If-None-Match（非必须）— 响应合理（200 或 304）", LEVEL_WARNING,
                     ok_inm, f"HTTP {sc_inm}（200=忽略/304=支持，均可接受）")

    # ── 5. HTTP StatusCode 合规 ──────────────────────────
    def check_status_codes(self, token):
        self.log.info("=" * 60)
        self.log.info("【5】HTTP StatusCode 合规检查")

        # 200
        sc, _, _ = self._request("GET", self.systems_uri, token=token)
        self._record(f"GET {self.systems_uri} → 200", LEVEL_ERROR,
                     sc == 200, f"HTTP {sc}")

        # 201（Session 创建）
        self._record("POST 创建 Session → 201", LEVEL_ERROR,
                     self._session_create_sc == 201,
                     f"HTTP {self._session_create_sc}",
                     evidence=self._session_create_evidence)

        # 204（Session 删除）
        self._record("DELETE Session → 204", LEVEL_ERROR,
                     self._session_delete_sc == 204,
                     f"HTTP {self._session_delete_sc}",
                     evidence=self._session_delete_evidence)

        # 401
        sc_401, _, _ = self._request("GET", self.systems_uri)
        self._record("无凭据访问 → 401", LEVEL_ERROR,
                     sc_401 == 401, f"HTTP {sc_401}")

        # 403：创建 ReadOnly 账号，用其访问需要写权限的接口 → 期望 403
        # 测试账号：dmtf_ro_test / TestPwd@2024，用完即删
        _ro_user = "dmtf_ro_test"
        _ro_pass = "TestPwd@2024"
        _ro_uid  = None
        sc_create_ro, body_create_ro, hdrs_create_ro = self._request(
            "POST", self.accounts_uri,
            body={"UserName": _ro_user, "Password": _ro_pass, "RoleId": "ReadOnly"},
            token=token
        )
        ro_loc = hdrs_create_ro.get("location", "")
        if sc_create_ro == 201:
            # 用 ReadOnly 账号 PATCH 受保护资源 → 期望 403
            sc_403, _, _ = self._request(
                "GET", self.chassis_uri,
                basic_auth=(_ro_user, _ro_pass)
            )
            # 先确认 ReadOnly 账号能 GET（200）
            self._record("403 前置：ReadOnly 账号 GET Chassis → 200", LEVEL_INFO,
                         sc_403 == 200, f"HTTP {sc_403}")
            # ReadOnly 账号 PATCH → 401/403 均表示写权限被拒绝。
            _, body_ch_ro, _ = self._request("GET", self.chassis_uri,
                                              basic_auth=(_ro_user, _ro_pass))
            etag_ro = body_ch_ro.get("@odata.etag", "") if isinstance(body_ch_ro, dict) else ""
            sc_403p, _, _ = self._request(
                "PATCH", self.chassis_uri, body={},
                basic_auth=(_ro_user, _ro_pass),
                extra_headers={"If-Match": etag_ro} if etag_ro else {}
            )
            self._record("ReadOnly 账号 PATCH 受保护资源 → 401/403", LEVEL_ERROR,
                         self._is_write_denied(sc_403p),
                         f"HTTP {sc_403p}（401/403 均表示写操作被拒绝）")
            # 清理测试账号
            if ro_loc:
                self._request("DELETE", ro_loc, token=token)
                self.log.info(f"  → ReadOnly 测试账号 {_ro_user} 已清理")
        else:
            # 账号创建失败（可能已存在或不支持 POST 创建账号）
            self._record("403 权限拒绝（ReadOnly 账号创建失败，降级为已知信息验证）",
                         LEVEL_WARNING, False,
                         f"创建测试账号 HTTP {sc_create_ro}（{body_create_ro!r}）")

        # 404（构造一个确定不存在的资源路径，基于动态发现的集合 URI 拼接）
        sc_404, _, _ = self._request(
            "GET", f"{self.systems_uri}/nonexistent_resource_xyzabc123", token=token
        )
        self._record("访问不存在资源 → 404", LEVEL_ERROR,
                     sc_404 == 404, f"HTTP {sc_404}")

        # 405（对只读集合尝试 DELETE；集合 URI 从成员 URI 上溯一级）
        systems_col_uri = self.systems_uri.rsplit("/", 1)[0] if self.systems_uri else ""
        sc_405, _, hdrs_405 = self._request(
            "DELETE", systems_col_uri, token=token
        )
        self._record("不支持的 Method → 405", LEVEL_ERROR,
                     sc_405 == 405,
                     f"HTTP {sc_405}（对 Systems 集合 DELETE，期望 405）")

        # 405 响应应含 Allow header（DMTF 通用）
        allow = hdrs_405.get("allow", "")
        self._record("405 响应包含 Allow header（DMTF 通用）", LEVEL_WARNING,
                     bool(allow), f"Allow={allow!r}")

        # 428/412 仅在资源声明 ETag 且服务对该 PATCH 强制前置条件时适用。
        etag = self._get_etag(self.chassis_uri, token)
        if not etag:
            self._record("PATCH 前置条件状态码检查", LEVEL_INFO, False,
                         "Chassis 未声明 ETag，428/412 检查不适用",
                         result=RESULT_SKIPPED)
        else:
            sc_precondition, _, _ = self._request(
                "PATCH", self.chassis_uri, body={}, token=token
            )
            self._record("PATCH 不带 If-Match → 200/204/428", LEVEL_ERROR,
                         sc_precondition in (200, 204, 428),
                         f"HTTP {sc_precondition}")
            if sc_precondition == 428:
                sc_412, _, _ = self._request(
                    "PATCH", self.chassis_uri, body={}, token=token,
                    extra_headers={"If-Match": '"invalid_etag_value_xyz_999"'}
                )
                self._record("强制前置条件时错误 ETag → 412", LEVEL_ERROR,
                             sc_412 == 412, f"HTTP {sc_412}")
            else:
                self._record("错误 ETag → 412", LEVEL_INFO, False,
                             "服务未对该 PATCH 强制 If-Match，412 检查不适用",
                             result=RESULT_SKIPPED)

        # 409（资源状态冲突：重复创建已存在的 Session 用户暂无法自动产生 409，
        #      改为验证 Accounts 重复创建场景）
        # 先读 AccountService 是否支持
        sc_acct_col, acct_col, _ = self._request(
            "GET", self.accounts_uri, token=token
        )
        if sc_acct_col == 200:
            # 尝试创建与现有账号同名的账号 → 期望 409
            sc_409, body_409, _ = self._request(
                "POST", self.accounts_uri,
                body={"UserName": self.USERNAME, "Password": "TestPwd@2024",
                      "RoleId": "ReadOnly"},
                token=token
            )
            if sc_409 == 409:
                self._record("409 资源冲突（重复创建同名账号）→ 409", LEVEL_WARNING,
                             True, f"HTTP 409，符合规范")
            elif sc_409 in (400, 422):
                self._record("409 资源冲突（重复创建同名账号）→ 400/422（BMC 降级处理）",
                             LEVEL_WARNING, True,
                             f"HTTP {sc_409}（400/422 也可接受，文档倾向 409）")
            else:
                self._record(f"409 资源冲突场景（重复创建同名账号 → HTTP {sc_409}）",
                             LEVEL_WARNING, False,
                             f"HTTP {sc_409}（期望 409 或 400/422）")
        else:
            self._record("409 资源冲突场景（AccountService GET 失败）", LEVEL_ERROR,
                         False, f"AccountService HTTP {sc_acct_col}（应返回 200）")

        # 功耗采集场景：GET Power → 200
        sc_pwr, pwr_body, _ = self._request("GET", self.power_uri, token=token)
        self._record(f"GET Power（功耗采集）→ 200", LEVEL_ERROR,
                     sc_pwr == 200, f"HTTP {sc_pwr}")
        if isinstance(pwr_body, dict):
            has_pwr = bool(pwr_body.get("PowerControl")) or bool(
                pwr_body.get("Oem", {}).get("Public", {}).get("TotalPower")
            )
            self._record("Power 包含 PowerControl 或 OEM 功耗字段", LEVEL_WARNING,
                         has_pwr,
                         f"PowerControl={bool(pwr_body.get('PowerControl'))}, "
                         f"OEM.TotalPower={pwr_body.get('Oem',{}).get('Public',{}).get('TotalPower')}")

    # ── 6. HTTP Request 合规 ──────────────────────────────
    def check_http_request(self, token):
        self.log.info("=" * 60)
        self.log.info("【6】HTTP Request 合规检查")

        # 6-1 PATCH 支持空 JSON {}（规范明确要求，传空不应报错）
        etag = self._get_etag(self.chassis_uri, token)
        if not etag:
            self.log.warning("  6-1: 未能获取 ETag，跳过 PATCH 空 JSON 检查（记 WARNING）")
            self._record("PATCH 传空 JSON {} 不报错（200/204）", LEVEL_WARNING,
                         False, "无法获取 ETag，PATCH 空 JSON 跳过")
        else:
            sc_empty, _, _ = self._request(
                "PATCH", self.chassis_uri, body={}, token=token,
                extra_headers={"If-Match": etag}
            )
            self._record("PATCH 传空 JSON {} 不报错（200/204）", LEVEL_ERROR,
                         sc_empty in (200, 204),
                         f"HTTP {sc_empty}（期望 200 或 204）")

        # 6-2 Boot PATCH：只传 Boot 属性，其余属性不受影响（BootOrder 场景）
        _, body_s, hdrs_s = self._request("GET", self.systems_uri, token=token)
        etag_s = (body_s.get("@odata.etag") or hdrs_s.get("etag") or ""
                  if isinstance(body_s, dict) else hdrs_s.get("etag") or "")
        original_boot = {}
        if isinstance(body_s, dict) and "Boot" in body_s:
            b = body_s["Boot"]
            original_boot = {
                "BootSourceOverrideTarget":  b.get("BootSourceOverrideTarget", "None"),
                "BootSourceOverrideEnabled": b.get("BootSourceOverrideEnabled", "Disabled"),
            }
        boot_patch = {"Boot": {"BootSourceOverrideEnabled": "Disabled"}}
        sc_boot, boot_resp, _ = self._request(
            "PATCH", self.systems_uri, body=boot_patch, token=token,
            extra_headers={"If-Match": etag_s} if etag_s else {}
        )
        self._record("PATCH Boot 只传 Boot 属性（200/204）", LEVEL_ERROR,
                     sc_boot in (200, 204), f"HTTP {sc_boot}")

        # 6-3 PATCH Boot 响应体包含变更后的 Boot 字段（规范建议）
        if sc_boot in (200, 204) and isinstance(boot_resp, dict):
            self._record("PATCH Boot 响应体含变更后 Boot 字段", LEVEL_WARNING,
                         "Boot" in boot_resp,
                         f"Boot={'存在' if 'Boot' in boot_resp else '缺失'}")

        # 6-4 恢复原始 Boot 配置
        if original_boot:
            _, body_s2, _ = self._request("GET", self.systems_uri, token=token)
            etag_s2 = (body_s2.get("@odata.etag", etag_s)
                       if isinstance(body_s2, dict) else etag_s)
            self._request(
                "PATCH", self.systems_uri, body={"Boot": original_boot}, token=token,
                extra_headers={"If-Match": etag_s2} if etag_s2 else {}
            )
            self.log.info("  → Boot 配置已恢复")

        # 6-5 数组属性 PATCH（文档要求：null 删除/{}不改/其他替换，末尾重排）
        # 用 BootOrder 数组做验证（若 BootOrder 存在）
        _, body_s3, _ = self._request("GET", self.systems_uri, token=token)
        etag_s3 = body_s3.get("@odata.etag", "") if isinstance(body_s3, dict) else ""
        boot_order = (body_s3.get("Boot", {}).get("BootOrder") or []
                      if isinstance(body_s3, dict) else [])
        if boot_order and len(boot_order) >= 2:
            # 测试：传入 [null, first_entry] → null 位置应被删除并右移
            # 实际上用空 BootOrder 测"不做任何变更"语义（传 {} 不改）
            patch_arr = {"Boot": {"BootOrder": [None, boot_order[0]]}}
            sc_arr, _, _ = self._request(
                "PATCH", self.systems_uri, body=patch_arr, token=token,
                extra_headers={"If-Match": etag_s3} if etag_s3 else {}
            )
            self._record("6-5 数组属性 PATCH（BootOrder 含 null）→ 200/204", LEVEL_WARNING,
                         sc_arr in (200, 204),
                         f"HTTP {sc_arr}（200/204=支持数组PATCH；4xx=不支持）")
            # 恢复 BootOrder
            _, body_s4, _ = self._request("GET", self.systems_uri, token=token)
            etag_s4 = body_s4.get("@odata.etag", etag_s3) if isinstance(body_s4, dict) else etag_s3
            self._request(
                "PATCH", self.systems_uri,
                body={"Boot": {"BootOrder": boot_order}}, token=token,
                extra_headers={"If-Match": etag_s4} if etag_s4 else {}
            )
            self.log.info("  → BootOrder 已恢复")
        else:
            # BootOrder 为空或只有 1 个元素 → 改为 NTP ServerList 数组测试（若可用）
            # Manager 的 NetworkProtocol NTP.NTPServers 通常是字符串数组
            # NetworkProtocol URI 从 Manager 资源链接动态发现
            _, mgr_body, _ = self._request(
                "GET", self.managers_uri, token=token
            )
            np_uri = (mgr_body.get("NetworkProtocol") or {}).get("@odata.id", "") \
                if isinstance(mgr_body, dict) else ""
            _, np_body, _ = self._request(
                "GET", np_uri, token=token
            )
            ntp_servers = (np_body.get("NTP", {}).get("NTPServers") or []
                           if isinstance(np_body, dict) else [])
            if isinstance(ntp_servers, list) and len(ntp_servers) >= 1:
                # 构造含 null 的数组 → null 位应被删除并右移
                patch_ntp = {"NTP": {"NTPServers": [None] + ntp_servers[:1]}}
                sc_ntp_arr, _, _ = self._request(
                    "PATCH", np_uri,
                    body=patch_ntp, token=token
                )
                self._record("6-5 数组属性 PATCH（NTP.NTPServers 含 null）→ 200/204",
                             LEVEL_WARNING, sc_ntp_arr in (200, 204),
                             f"HTTP {sc_ntp_arr}（200/204=支持数组PATCH；4xx=不支持）")
                # 恢复 NTP Servers
                self._request(
                    "PATCH", np_uri,
                    body={"NTP": {"NTPServers": ntp_servers}}, token=token
                )
                self.log.info("  → NTPServers 已恢复")
            else:
                self._record("6-5 数组属性 PATCH（BootOrder/NTPServers 均不可用）",
                             LEVEL_WARNING, False,
                             f"BootOrder={boot_order!r}, NTPServers={ntp_servers!r}，无法自动验证")

    # ── 7. HTTP Response 合规 ─────────────────────────────
    def check_http_response(self, token):
        """
        文档 HTTP Response 表格 8 种场景全量覆盖：
        ┌───────────────────────────────────┬──────────────────────────────────────────────────────────┐
        │ 场景                              │ 规范要求                                                  │
        ├───────────────────────────────────┼──────────────────────────────────────────────────────────┤
        │ 7-1 POST Actions                 │ 204 → None；200 → error response；202 → TaskMonitor       │
        │ 7-2 POST Actions（有响应结构）    │ 200 → action response 含 @odata 相关字段                  │
        │ 7-3 POST Actions 400/500          │ → 必须返回 error response                                 │
        │ 7-4 POST 创建资源（无响应结构）   │ 201 → error response（Location header 必须）              │
        │ 7-5 POST 创建资源（有响应结构）   │ 201 → action response（资源体含 @odata.id 等）            │
        │ 7-6 PATCH/PUT/DELETE 成功         │ 200/204/201 → 符合 modification-success-responses 规范   │
        │ 7-7 PATCH/PUT/DELETE 400/500      │ → 必须返回 error response                                 │
        │ 7-8 ComputerSystem.Reset          │ 200 → error response；204 → None                          │
        └───────────────────────────────────┴──────────────────────────────────────────────────────────┘
        """
        self.log.info("=" * 60)
        self.log.info("【7】HTTP Response 合规检查")

        def _is_error_response(body):
            """判断是否为 DMTF error response 结构"""
            return isinstance(body, dict) and (
                "error" in body or "@Message.ExtendedInfo" in body
            )

        def _is_action_response(body):
            """判断是否为 action response（含 @odata 字段）"""
            return isinstance(body, dict) and any(
                k.startswith("@odata") for k in body
            )

        # ── 探测 EventService.SubmitTestEvent ──
        # 根据 ActionInfo/Registry 构造合法请求；异步请求可返回 202。
        # 本项检查 HTTP 响应，不验证测试事件是否最终送达订阅端。
        _, ev_body, _ = self._request("GET", self.event_svc_uri, token=token)
        submit_test_uri = None
        if isinstance(ev_body, dict):
            submit_test_uri = (
                ev_body.get("Actions", {})
                       .get("#EventService.SubmitTestEvent", {})
                       .get("target")
            )
            # 兼容不同厂商大小写/命名
            if not submit_test_uri:
                for k, v in ev_body.get("Actions", {}).items():
                    if "SubmitTestEvent" in k and isinstance(v, dict):
                        submit_test_uri = v.get("target")
                        break

        # ──────────────────────────────────────────────────
        # 7-1 / 7-2 / 7-3  POST Actions 响应规范
        # 测试资源：EventService.SubmitTestEvent（带外订阅场景）
        # ──────────────────────────────────────────────────
        self.log.info("  --- 7-1/7-2/7-3 POST Actions（EventService.SubmitTestEvent）---")

        if submit_test_uri:
            # 7-1：按设备声明提交合法参数，兼容同步与异步响应。
            sc_71, body_71, hdrs_71 = None, None, {}
            try:
                submit_ok_body = self._build_submit_test_event_body(ev_body, token)
            except Exception as exc:
                self._record("7-1 POST Action(SubmitTestEvent) 合法参数发现",
                             LEVEL_ERROR, False, str(exc))
            else:
                self.log.info(f"  SubmitTestEvent 正向请求参数：{submit_ok_body}")
                sc_71, body_71, hdrs_71 = self._request(
                    "POST", submit_test_uri, body=submit_ok_body, token=token
                )
            if sc_71 == 204:
                ok_none = body_71 in ("", None) or body_71 == b""
                self._record("7-1 POST Action(SubmitTestEvent) 204 响应体为空（None）",
                             LEVEL_ERROR, ok_none,
                             f"HTTP 204, body={body_71!r}（期望 None/空）")
            elif sc_71 == 200:
                # 有的 BMC 返回 200 + action response（有响应结构场景，对应 7-2）
                if _is_action_response(body_71):
                    self._record("7-2 POST Action(SubmitTestEvent) 200 返回 action response（含 @odata 字段）",
                                 LEVEL_ERROR, True,
                                 f"body keys={list(body_71.keys())}")
                    self._record("7-1 POST Action(SubmitTestEvent) 204→None（BMC 返回 200，改为 7-2 验证）",
                                 LEVEL_INFO, True, "BMC 返回 200+action response，符合 7-2 规范")
                else:
                    self._record("7-1 POST Action(SubmitTestEvent) 200 返回 error response 结构",
                                 LEVEL_ERROR, _is_error_response(body_71),
                                 f"body keys={list(body_71.keys()) if isinstance(body_71,dict) else body_71!r}")
            elif sc_71 == 202:
                location = hdrs_71.get("location", "")
                try:
                    parsed = urllib.parse.urlsplit(location)
                    location_ok = bool(location) and (
                        (location.startswith("/") and not parsed.netloc)
                        or (parsed.scheme in ("http", "https") and bool(parsed.netloc))
                    )
                except ValueError:
                    location_ok = False
                # Task 消息体是可选的；存在时应提供 Task 类型及任务链接。
                task_ok = body_71 in ("", None, b"")
                if isinstance(body_71, dict):
                    task_type = body_71.get("@odata.type", "")
                    task_ok = ("error" not in body_71 and isinstance(task_type, str)
                               and task_type.startswith("#Task.") and task_type.endswith(".Task")
                               and any(isinstance(body_71.get(key), str) and body_71[key]
                                       for key in ("@odata.id", "TaskMonitor")))
                self._record("7-1 POST Action(SubmitTestEvent) 202 异步受理响应",
                             LEVEL_ERROR, location_ok and task_ok,
                             f"HTTP 202, Location={location!r}, "
                             f"Location有效={location_ok}, Task响应有效={task_ok}；"
                             "仅检查异步受理响应，未验证事件送达")
            elif sc_71 is not None:
                self._record("7-1 POST Action(SubmitTestEvent) → 200/202/204", LEVEL_ERROR,
                             False, f"实际 HTTP {sc_71}（期望 200/202/204）")

            # 7-2：若 7-1 已返回 204，则 7-2 需要另一个 Action 来验证"有响应结构"场景
            #      被测 BMC 暂无返回 200+action response 的 Action，记录为 INFO（非必须）
            if sc_71 == 204:
                self._record("7-2 POST Action 200 返回 action response（有响应结构场景）",
                             LEVEL_INFO, True,
                             "SubmitTestEvent 返回 204（无响应结构场景），7-2 需有 200+@odata 的 Action 才能验证，当前 BMC 暂无此类 Action，记录 INFO")

            # 7-3：非法参数 → 400 + error response
            submit_bad_body = {"InvalidParam_XYZ_999": "bad_value"}
            sc_73, body_73, _ = self._request(
                "POST", submit_test_uri, body=submit_bad_body, token=token
            )
            if sc_73 in (400, 422):
                self._record("7-3 POST Action(SubmitTestEvent) 非法参数 → 400/422 含 error response",
                             LEVEL_ERROR, _is_error_response(body_73),
                             f"HTTP {sc_73}, keys={list(body_73.keys()) if isinstance(body_73,dict) else body_73!r}")
            elif sc_73 in (200, 204):
                self._record("7-3 POST Action(SubmitTestEvent) 非法参数 BMC 未报错（不合规）",
                             LEVEL_ERROR, False,
                             f"HTTP {sc_73}（BMC 忽略非法参数，规范要求 400+error response）")
            else:
                self._record(f"7-3 POST Action(SubmitTestEvent) 非法参数 → 400（实际 {sc_73}）",
                             LEVEL_ERROR, False, f"HTTP {sc_73}")
        else:
            # EventService.SubmitTestEvent 不存在，BMC 未暴露此标准 Action
            self._record("7-1/7-3 EventService.SubmitTestEvent Action 存在",
                         LEVEL_ERROR, False,
                         "EventService Actions 中未找到 SubmitTestEvent，BMC 未实现此标准 Action")
            self._record("7-2 POST Action 200 action response（有响应结构场景）",
                         LEVEL_WARNING, False,
                         "无可用 POST Action URI 验证")

        # ──────────────────────────────────────────────────
        # 7-4 / 7-5  POST 创建资源（Session 为例）
        # ──────────────────────────────────────────────────
        self.log.info("  --- 7-4/7-5 POST 创建资源（Session）---")
        sc_c, body_c, hdrs_c = self._request(
            "POST", self.sessions_uri,
            body={"UserName": self.USERNAME, "Password": self.PASSWORD}
        )
        new_token  = hdrs_c.get("x-auth-token", "")
        new_loc    = hdrs_c.get("location", "")
        if not new_token and isinstance(body_c, dict):
            new_token = body_c.get("Token", "")

        if sc_c == 201:
            # 7-4：无额外响应结构 → 201 + Location header
            self._record("7-4 POST 创建资源 201 + Location header", LEVEL_ERROR,
                         bool(new_loc),
                         f"HTTP 201, Location={new_loc!r}")
            # 7-5：有响应结构 → 201 + 资源体含 @odata.id
            if isinstance(body_c, dict):
                has_odata = "@odata.id" in body_c or "Id" in body_c
                self._record("7-5 POST 创建资源 201 响应体含资源标识字段", LEVEL_ERROR,
                             has_odata,
                             f"@odata.id={'存在' if '@odata.id' in body_c else '缺失'}, "
                             f"Id={body_c.get('Id')!r}")
            else:
                self._record("7-5 POST 创建资源 201 响应体为 dict", LEVEL_WARNING,
                             False, f"body type={type(body_c).__name__}")
        else:
            self._record("7-4 POST 创建资源 → 201", LEVEL_ERROR,
                         False, f"实际 HTTP {sc_c}（期望 201）")
            self._record("7-5 POST 创建资源 201 响应体含资源标识", LEVEL_ERROR,
                         False, f"HTTP {sc_c}（无法验证响应体，创建本身已 FAIL）")
        # 清理新建的 Session
        if new_loc and (token or new_token):
            self._request("DELETE", new_loc, token=token or new_token)

        # ──────────────────────────────────────────────────
        # 7-6  PATCH/DELETE 成功 → 符合 modification-success-responses
        # ──────────────────────────────────────────────────
        self.log.info("  --- 7-6 PATCH/DELETE 成功响应规范 ---")
        # PATCH 成功（先 GET ETag，再携带 If-Match PATCH）
        etag_ch = self._get_etag(self.chassis_uri, token)
        if not etag_ch:
            self.log.warning("  7-6: 未能获取 ETag，跳过 PATCH 成功检查（记 WARNING）")
            self._record("7-6 PATCH 成功 → 200 或 204", LEVEL_WARNING,
                         False, "无法获取 ETag，PATCH 跳过（ETag 未暴露）")
            patch_ok_body = None
            sc_patch_ok   = -1
        else:
            sc_patch_ok, patch_ok_body, _ = self._request(
                "PATCH", self.chassis_uri, body={}, token=token,
                extra_headers={"If-Match": etag_ch}
            )
            self._record("7-6 PATCH 成功 → 200 或 204", LEVEL_ERROR,
                         sc_patch_ok in (200, 204), f"HTTP {sc_patch_ok}")
        if sc_patch_ok == 200 and isinstance(patch_ok_body, dict):
            self._record("7-6 PATCH 成功 200 响应体含 @odata.id（规范建议）",
                         LEVEL_WARNING, "@odata.id" in patch_ok_body,
                         f"@odata.id={'存在' if '@odata.id' in patch_ok_body else '缺失'}")

        # DELETE 成功（创建临时 Session 后 DELETE）
        sc_tmp, _, hdrs_tmp = self._request(
            "POST", self.sessions_uri,
            body={"UserName": self.USERNAME, "Password": self.PASSWORD}
        )
        tmp_tok = hdrs_tmp.get("x-auth-token", "")
        tmp_loc = hdrs_tmp.get("location", "")
        if sc_tmp == 201 and tmp_loc:
            sc_del_ok, _, _ = self._request(
                "DELETE", tmp_loc, token=token or tmp_tok
            )
            self._record("7-6 DELETE 成功 → 200 或 204", LEVEL_ERROR,
                         sc_del_ok in (200, 204),
                         f"HTTP {sc_del_ok}（DELETE Session {tmp_loc}）")
            # 文档备注：资源已删除再 DELETE → 期望 404
            sc_del2, _, _ = self._request("DELETE", tmp_loc, token=token)
            self._record("7-6 DELETE 已删资源 → 404（文档倾向 404）", LEVEL_WARNING,
                         sc_del2 == 404,
                         f"HTTP {sc_del2}（期望 404；200/204 也合规但文档更倾向 404）")
        else:
            self._record("7-6 DELETE 成功 → 200 或 204", LEVEL_ERROR,
                         False, f"临时 Session 创建失败（HTTP {sc_tmp}），无法验证 DELETE")

        # ──────────────────────────────────────────────────
        # 7-7  PATCH 400/500 → 必须返回 error response
        # ──────────────────────────────────────────────────
        self.log.info("  --- 7-7 PATCH/DELETE 400/500 error response ---")
        etag_ch2 = self._get_etag(self.chassis_uri, token)
        sc_patch_bad, body_patch_bad, _ = self._request(
            "PATCH", self.chassis_uri,
            body={"InvalidField_XYZ_NotExist": "bad_value"}, token=token,
            extra_headers={"If-Match": etag_ch2} if etag_ch2 else {}
        )
        if sc_patch_bad in (400, 422):
            self._record("7-7 PATCH 400 含 error response 结构", LEVEL_ERROR,
                         _is_error_response(body_patch_bad),
                         f"HTTP {sc_patch_bad}, keys={list(body_patch_bad.keys()) if isinstance(body_patch_bad,dict) else body_patch_bad!r}")
        elif sc_patch_bad in (200, 204):
            self._record("7-7 PATCH 非法字段 BMC 忽略（不报 400）", LEVEL_WARNING,
                         True, f"HTTP {sc_patch_bad}（BMC 忽略未知字段，规范建议 400 + error response）")
        else:
            self._record("7-7 PATCH 非法字段 → 400 或忽略", LEVEL_WARNING,
                         False, f"HTTP {sc_patch_bad}")

        # ──────────────────────────────────────────────────
        # 7-8  ComputerSystem.Reset：200 → error response；204 → None
        # ──────────────────────────────────────────────────
        self.log.info("  --- 7-8 ComputerSystem.Reset 响应规范 ---")
        _, sys_body, _ = self._request("GET", self.systems_uri, token=token)
        reset_uri = None
        if isinstance(sys_body, dict):
            reset_uri = (
                sys_body.get("Actions", {})
                        .get("#ComputerSystem.Reset", {})
                        .get("target")
            )
        if reset_uri:
            # 只读验证：用非法 ResetType 触发错误，不真正重启服务器
            sc_rst, rst_body, _ = self._request(
                "POST", reset_uri,
                body={"ResetType": "InvalidType_XYZ_NotExist"}, token=token
            )
            if sc_rst in (400, 422):
                # 非法参数 → 400 + error response
                self._record("7-8 ComputerSystem.Reset 400 含 error response", LEVEL_ERROR,
                             _is_error_response(rst_body),
                             f"HTTP {sc_rst}, keys={list(rst_body.keys()) if isinstance(rst_body,dict) else rst_body!r}")
            elif sc_rst == 200:
                # 非法参数但返回 200 → 必须是 error response（规范要求）
                self._record("7-8 ComputerSystem.Reset 200 含 error response（规范要求）",
                             LEVEL_ERROR, _is_error_response(rst_body),
                             f"HTTP 200, keys={list(rst_body.keys()) if isinstance(rst_body,dict) else rst_body!r}")
            elif sc_rst == 204:
                # 不应该因非法参数返回 204（204 表示成功执行）
                self._record("7-8 ComputerSystem.Reset 非法参数不应返回 204", LEVEL_WARNING,
                             False, "非法 ResetType 返回 204，BMC 可能未校验参数")
            else:
                self._record(f"7-8 ComputerSystem.Reset 非法参数 → 400/422（实际 {sc_rst}）",
                             LEVEL_WARNING, False, f"HTTP {sc_rst}")
            # 补充记录：文档原文要求（合规路径描述，用于记录规范条文）
            self._record("7-8 ComputerSystem.Reset 规范：204→None，200→error response（文档要求）",
                         LEVEL_INFO, True,
                         f"Reset URI={reset_uri}，上方用非法参数验证（不执行真实重启）")
        else:
            self._record("7-8 ComputerSystem.Reset Action URI 存在（#ComputerSystem.Reset）",
                         LEVEL_ERROR, False,
                         f"{self.systems_uri} Actions 中未找到 #ComputerSystem.Reset.target，BMC 未暴露此 Action")

    # ── 8. 通用 DMTF 补充 ────────────────────────────────
    def check_dmtf_general(self, token):
        self.log.info("=" * 60)
        self.log.info("【8】通用 DMTF 规范补充检查")

        # 8-1 主要资源必含 @odata.id / @odata.type
        for label, uri in [("ComputerSystem", self.systems_uri),
                            ("Chassis", self.chassis_uri),
                            ("Manager", self.managers_uri)]:
            sc, body, _ = self._request("GET", uri, token=token)
            if sc != 200 or not isinstance(body, dict):
                self._record(f"GET {label} 可访问", LEVEL_ERROR,
                             False, f"HTTP {sc}")
                continue
            for field in ("@odata.id", "@odata.type"):
                self._record(f"{label} 含 {field}（DMTF 必填）", LEVEL_WARNING,
                             field in body, f"{field}={body.get(field)!r}")
            self._record(f"{label} 含 @odata.context（DMTF 建议）", LEVEL_WARNING,
                         "@odata.context" in body,
                         f"@odata.context={body.get('@odata.context')!r}")

        # 8-2 集合含 Members@odata.count
        # Systems 集合 URI 从 root.Links 发现（_discover_uris 已缓存 root 结构，
        # 此处直接从 systems_uri 上溯一级）
        systems_col_uri = self.systems_uri.rsplit("/", 1)[0] if self.systems_uri else ""
        sc_col, body_col, _ = self._request(
            "GET", systems_col_uri, token=token
        )
        if sc_col == 200 and isinstance(body_col, dict):
            self._record("集合资源含 Members@odata.count（DMTF 必填）", LEVEL_WARNING,
                         "Members@odata.count" in body_col,
                         f"Members@odata.count={body_col.get('Members@odata.count')!r}")

        # 8-3 订阅场景：EventService 完整流程（文档"同步厂商信息"明确列出）
        sc_ev, body_ev, _ = self._request(
            "GET", self.event_svc_uri, token=token
        )
        self._record("EventService 可访问（订阅场景）", LEVEL_WARNING,
                     sc_ev == 200, f"HTTP {sc_ev}")
        if sc_ev == 200 and isinstance(body_ev, dict):
            # 8-3a Subscriptions 集合
            sub_link = (body_ev.get("Subscriptions") or {}).get("@odata.id", "")
            has_sub  = bool(sub_link)
            self._record("EventService 含 Subscriptions 集合链接", LEVEL_WARNING,
                         has_sub, f"Subscriptions.@odata.id={sub_link!r}")

            if sub_link:
                sc_sub_col, _, _ = self._request("GET", sub_link, token=token)
                self._record("GET Subscriptions 集合 → 200", LEVEL_WARNING,
                             sc_sub_col == 200, f"HTTP {sc_sub_col}")

                # 8-3b 创建订阅（POST）→ 201 + Location（用虚假 Destination，测接口合规性）
                sc_sub_c, body_sub_c, hdrs_sub_c = self._request(
                    "POST", sub_link,
                    body={
                        "Destination": "https://192.0.2.1:8443/redfish/events",  # 测试用 IP，不可达
                        "EventTypes": ["Alert"],
                        "Protocol": "Redfish",
                    },
                    token=token
                )
                sub_loc = hdrs_sub_c.get("location", "")
                self._record("POST 创建 EventService Subscription → 201", LEVEL_WARNING,
                             sc_sub_c == 201,
                             f"HTTP {sc_sub_c}（期望 201）")
                if sc_sub_c == 201:
                    self._record("POST 创建订阅响应含 Location header", LEVEL_WARNING,
                                 bool(sub_loc), f"Location={sub_loc!r}")
                    # 8-3c 删除订阅（清理）
                    if sub_loc:
                        sc_sub_d, _, _ = self._request("DELETE", sub_loc, token=token)
                        self._record("DELETE EventService Subscription → 200/204", LEVEL_WARNING,
                                     sc_sub_d in (200, 204), f"HTTP {sc_sub_d}")
                elif sc_sub_c in (400, 422):
                    # 虚假 Destination 被拒绝（部分 BMC 会校验地址可达性）
                    self._record("POST 创建订阅因 Destination 不可达被拒（400/422，可接受）",
                                 LEVEL_WARNING, True,
                                 f"HTTP {sc_sub_c}（BMC 校验 Destination 可达性）")
                else:
                    self._record(f"POST 创建订阅 → 201（实际 {sc_sub_c}）",
                                 LEVEL_WARNING, False, f"HTTP {sc_sub_c}")

        # 8-4 ServiceRoot 含 Links 字段（/redfish/v1 为 DMTF 强约定入口）
        sc_root, body_root, _ = self._request("GET", "/redfish/v1")
        if sc_root == 200 and isinstance(body_root, dict):
            self._record("ServiceRoot 含 Links 字段（DMTF 通用）", LEVEL_WARNING,
                         "Links" in body_root,
                         f"Links={'存在' if 'Links' in body_root else '缺失'}")

        # 8-5 HTTPS 强制（HTTP 明文应被拒绝或重定向）
        sc_http = -1  # 预声明：默认拒绝连接，符合预期
        try:
            http_req = urllib.request.Request(
                f"http://{self.BMC_IP}/redfish/v1", method="GET"
            )
            with urllib.request.urlopen(http_req, timeout=5) as resp:
                sc_http = resp.status
        except urllib.error.HTTPError as e:
            sc_http = e.code
        except Exception:
            sc_http = -1  # 拒绝连接 = 符合预期
        ok_https = sc_http in (-1, 301, 302, 308, 400, 403)
        self._record("HTTP 明文访问被拒绝/重定向（DMTF 安全建议）", LEVEL_WARNING,
                     ok_https,
                     f"HTTP明文 /redfish/v1 返回 {sc_http}（-1=连接拒绝，符合预期）")

    # ─────────────────────────────────────────────────────
    # 主流程
    # ─────────────────────────────────────────────────────
    def run_test(self):
        self.log.info(f"开始：{self.TEST_NAME}（{self.TEST_NUM}）")
        self.log.info(f"BMC IP：{self.BMC_IP}")

        # ── 1. ServiceRoot 必须允许匿名访问 ──
        self.check_redfish_version()

        # ── 0. 动态发现资源 URI；失败时禁止猜测 URI 或继续破坏性检查 ──
        if not self._discover_uris():
            self._summarize()
            return

        # ── 创建测试 Session ──
        self.log.info("=" * 60)
        self.log.info("【Session】创建测试 Session")
        sc_c, body_c, hdrs_c = self._request(
            "POST", self.sessions_uri,
            body={"UserName": self.USERNAME, "Password": self.PASSWORD}
        )
        self._session_create_sc   = sc_c
        self._session_create_evidence = dict(self._last_evidence)
        self._session_location    = hdrs_c.get("location", "")
        self._session_location_ok = bool(self._session_location)
        token = hdrs_c.get("x-auth-token", "")
        if not token and isinstance(body_c, dict):
            token = body_c.get("Token", "")
        self.log.info(f"  Session 创建 HTTP {sc_c}, Location={self._session_location}")
        if not token:
            self._record("创建认证 Session", LEVEL_ERROR, False,
                         f"HTTP {sc_c}，响应未提供认证 token，后续检查无法执行",
                         result=RESULT_BLOCKED)
            self._summarize()
            return

        # ── 创建临时 Session 用于 DELETE 验证 ──
        sc_t, _, hdrs_t = self._request(
            "POST", self.sessions_uri,
            body={"UserName": self.USERNAME, "Password": self.PASSWORD}
        )
        tmp_token = hdrs_t.get("x-auth-token", "")
        tmp_loc   = hdrs_t.get("location", "")
        if tmp_loc and (token or tmp_token):
            sc_del, _, _ = self._request("DELETE", tmp_loc, token=token or tmp_token)
            self._session_delete_sc = sc_del
            self._session_delete_evidence = dict(self._last_evidence)
        else:
            self._session_delete_sc = -1
            self._session_delete_evidence = {
                "method": "DELETE", "uri": tmp_loc, "status": None,
                "response": "temporary session was not created", "headers": {},
            }
        self.log.info(f"  临时 Session DELETE HTTP {self._session_delete_sc}")

        # ── 2~8 主检查 ──
        self.check_etags(token)
        self.check_http_methods(token)
        self.check_http_headers(token)
        self.check_status_codes(token)
        self.check_http_request(token)
        self.check_http_response(token)
        self.check_dmtf_general(token)

        # ── 清理 Session ──
        if self._session_location and token:
            self._request("DELETE", self._session_location, token=token)
            self.log.info("  测试 Session 已清理")

        # ── 汇总 ──
        self._summarize()

    def _summarize(self):
        self.log.info("=" * 60)
        self.log.info("【汇总】")

        failures = [f for f in self.findings if f["result"] == RESULT_FAIL]
        blocked  = [f for f in self.findings if f["result"] == RESULT_BLOCKED]
        skipped  = [f for f in self.findings if f["result"] == RESULT_SKIPPED]
        passes   = [f for f in self.findings if f["result"] == RESULT_PASS]

        self.log.info(f"  总检查项：{len(self.findings)}")
        self.log.info(f"  PASS：   {len(passes)}")
        self.log.info(f"  FAIL（不合规）：{len(failures)}")
        self.log.info(f"  BLOCKED（前置条件不足）：{len(blocked)}")
        self.log.info(f"  SKIPPED（能力不适用）：{len(skipped)}")

        if failures:
            self.log.error("━━ 不合规项（ERROR）")
            for e in failures:
                self.log.error(f"  ❌ {e['item']}: {e['detail']}")
        if blocked:
            self.log.warning("━━ 阻塞项（BLOCKED）")
            for entry in blocked:
                self.log.warning(f"  ⛔ {entry['item']}: {entry['detail']}")

        if failures:
            final = RESULT_FAIL
        elif blocked:
            final = RESULT_BLOCKED
        elif passes:
            final = RESULT_PASS
        else:
            final = RESULT_SKIPPED
        self.command_check_result = final
        self.log.info(f"  最终结果：{final}")
        self._write_result(final, self.findings)

# ─────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    exit_code = 0
    obj     = None
    try:
        obj = Protocol001DmtfCompliance()
        obj.run_test()
        exit_code = {
            RESULT_PASS: 0,
            RESULT_FAIL: 2,
            RESULT_SKIPPED: 3,
            RESULT_BLOCKED: 4,
        }.get(obj.command_check_result, 1)
    except KeyboardInterrupt:
        exit_code = 130
    except Exception as e:
        if obj:
            obj.log.error(f"脚本异常：{e}")
            obj.log.error(traceback.format_exc())
        else:
            print(f"初始化异常：{e}")
            traceback.print_exc()
        exit_code = 1
    finally:
        ec_path = (
            obj.exit_code_path if obj else
            os.path.join(os.getcwd(), f"result/bmc/{Protocol001DmtfCompliance.LOG_BASE_NAME}/exit_code")
        )
        os.makedirs(os.path.dirname(ec_path), exist_ok=True)
        with open(ec_path, "w") as f:
            f.write(str(exit_code))
    sys.exit(exit_code)
