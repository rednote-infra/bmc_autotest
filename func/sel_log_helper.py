"""
SEL LogService 定位公共逻辑（issue/9 引入，思路源自上游 PR #13 / commit 13a2591）。

多厂商 BMC 上 SEL 可能挂在 Systems 侧（如 SEL / SystemEventLog），
也可能挂在 Managers 侧（如 /redfish/v1/Managers/Self/LogServices/SEL）。
历史上 systems_007 与 managers_004a 各自实现查找逻辑，存在两类漂移风险：
  1. "Systems 优先 + 取第一个"——SEL 在 Managers 侧时误选 Systems 的
     BIOS/Crashdump 等非 SEL 服务（条目为空或测错对象，假 PASS / 误 FAIL）；
  2. 子串匹配（"sel" in name.lower()）——issue/8 已确认部分厂商 BMC 用
     "/Self" 命名资源，"self" 小写后包含 "sel"，名为 SelfTest 之类的服务
     会被误命中。

本模块统一实现**跨 Systems / Managers 两侧的 SEL 匹配选择**，供
systems_007 / managers_004a 等脚本共用，避免再漂移。

匹配规则（源自上游真机踩坑经验）：
  - SEL 严格：``\\bSEL\\b`` 独立大写词匹配（大小写敏感，避免子串误命中）；
  - 其他厂商命名（如 System Event Log）按小写关键字补充匹配；
  - 明确**不包含**独立的 "EventLog" 服务——很多 BMC 把 SEL 与独立的
    "EventLog" 服务并列存在（如 Managers 侧为 ['AuditLog', 'EventLog', 'SEL']），
    过宽的 "event log" 会把排在前面的 EventLog 误判为 SEL。
"""
from __future__ import annotations

import re
from typing import Optional, Tuple

from func.common_function import CommonFunction

# 严格匹配：SEL 必须是独立大写词（避免 "console"/"dismiss"/"self" 等子串误命中）。
_SEL_UPPER_RE = re.compile(r"\bSEL\b")
# Id 精确形态：SEL / SELLog / SELEventLog（xFusion 实际用 "SELLog"）
# 注意：\bSEL\b 匹配不到 "SELLog" —— "SEL" 后紧跟 "L" 均为 \w，不构成词边界，
# 这正是真机上 SEL 服务存在却被判缺失的原因。
_SEL_ID_EXACT_RE = re.compile(r"^SEL(Log|EventLog)?$", re.IGNORECASE)
# Id 前缀形态：SEL_xxx / SEL-xxx
_SEL_ID_PREFIX_RE = re.compile(r"^SEL[_-]", re.IGNORECASE)
# 其他厂商命名（小写关键字，作为补充）。
# 注意：只保留明确等价于 "System Event Log" 的写法，**不要**加 "event log"/
# "eventlog"——很多 BMC 把 SEL 与独立的 "EventLog" 服务并列存在，过宽的
# "event log" 会把独立的 EventLog 误判为 SEL（且它常排在 SEL 前面）。
SEL_NAME_HINTS = ("system event", "systemevent")


def _match_log_id(svc, log_id: str) -> bool:
    """按 Id 或 @odata.id 末尾段精确匹配（大小写敏感，与 BMC 保持一致）。"""
    if (svc.id or "") == log_id:
        return True
    return (svc.odata_id or "").rstrip("/").endswith(f"/{log_id}")


def _is_sel_log_service(log_svc) -> bool:
    """按 Id/Name 判断是否为 SEL 日志服务。

    - Id 形态匹配（最可靠）：SEL / SELLog / SELEventLog / SEL_xxx。
      用正则白名单而非子串匹配，避免 "Self"/"SelfTest" 被误命中。
    - 独立大写词匹配：name 或 id 含 ``\\bSEL\\b``（如 "System SEL"）。
    - 其他厂商命名（如 System Event Log）按小写关键字补充匹配；
      注意**不包含**独立的 "EventLog" 服务（避免与 SEL 并列存在时误判）。
    """
    svc_id = log_svc.id or ""
    name = log_svc.name or ""

    # 1) Id 精确/前缀形态 —— 覆盖 xFusion 的 "SELLog"
    if _SEL_ID_EXACT_RE.match(svc_id) or _SEL_ID_PREFIX_RE.match(svc_id):
        return True

    # 2) 独立大写词
    if _SEL_UPPER_RE.search(name) or _SEL_UPPER_RE.search(svc_id):
        return True

    # 3) 其他厂商命名
    low_name = name.lower()
    low_id = svc_id.lower()
    return any(hint in low_name or hint in low_id for hint in SEL_NAME_HINTS)


def find_sel_log_service(client, log_id: Optional[str] = None):
    """跨 Systems / Managers 两侧查找 SEL LogService。

    返回 ``(Log对象, source)``，``source`` 为 ``"Systems"`` 或 ``"Managers"``。

    选择策略：
      1. 优先在 Systems、再 Managers 两侧，按 ``log_id``（精确）或
         ``SEL_NAME_HINTS``（关键字）寻找真正的 SEL；明确匹配到才返回。
      2. 两侧都没匹配到 SEL 时，回退到第一个可用服务并记 WARNING
         （避免像旧实现那样直接取 Systems 第一个而误选 BIOS）。
      3. 两侧都不可用返回 ``(None, None)``。

    Args:
        client: 已初始化的 redfish_sdk.RedfishClient。
        log_id: 可选，显式指定 LogService Id（精确匹配优先于关键字）。
    """
    candidates = (
        ("Systems", client.get_system_log_services),
        ("Managers", client.get_manager_log_services),
    )
    fallback = None
    log_id_matched = False

    for source, getter in candidates:
        try:
            log_services = getter()
        except Exception as e:  # noqa: BLE001 — 单侧不可用不应中断整体查找
            CommonFunction.print_log("WARNING", f"{source} Log Services 不可用：{str(e)}")
            continue
        if not log_services:
            CommonFunction.print_log("INFO", f"{source} 侧未发现 LogService")
            continue

        available = [svc.id or svc.odata_id for svc in log_services]
        CommonFunction.print_log("INFO", f"{source} 侧 Log Services: {available}")

        # 1) 配置显式指定 LogId 时优先精确匹配
        if log_id:
            for svc in log_services:
                if _match_log_id(svc, log_id):
                    log_id_matched = True
                    if not _is_sel_log_service(svc):
                        # 配置命中了服务，但它不像 SEL —— 可能是配置写错，告警但不拦截
                        CommonFunction.print_log(
                            "WARNING",
                            f"配置 LogId='{log_id}' 命中服务 '{svc.id}'，"
                            f"但未匹配 SEL 特征（大写 SEL / {list(SEL_NAME_HINTS)}），"
                            f"可能不是 SEL 日志",
                        )
                    return svc, source

        # 2) 按关键字找 SEL（SEL 严格大写匹配，详见 _is_sel_log_service）
        for svc in log_services:
            if _is_sel_log_service(svc):
                return svc, source

        if fallback is None:
            fallback = (log_services[0], source)

    if log_id and not log_id_matched:
        CommonFunction.print_log(
            "WARNING",
            f"配置 LogId='{log_id}' 在 Systems/Managers 两侧均未找到对应 LogService，"
            f"将按关键字继续匹配",
        )

    if fallback is not None:
        svc, source = fallback
        CommonFunction.print_log(
            "WARNING",
            f"未匹配到 SEL LogService（大写 SEL / 关键字 {list(SEL_NAME_HINTS)}），"
            f"回退使用 {source} 侧第一个服务 '{svc.id}'，条目可能为空",
        )
        return svc, source

    CommonFunction.print_log("ERROR", "Systems 和 Managers 均未找到 Log Service")
    return None, None
