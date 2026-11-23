"""
LogService Entries 有界采样公共逻辑。

背景（性能问题）
----------------
历史实现统一走 ``client.get_manager_log_entries(log_id)``，它会把 Entries
集合下的条目**逐条 GET 全量展开**：

    log_entries = self.client.get_manager_log_entries(hit_id)   # N 次 HTTP
    for entry in log_entries[:10]:                              # 却只用前 10 条

真机实测（xFusion iBMC，Redfish 1.20.1）：
    SEL       519  条 → 118 s
    OperateLog 9487 条 → 1991 s（被 runner 120 s 强制杀死）
    SecurityLog 1415 条 → 249 s

根本浪费：98% 以上的 HTTP 往返拿到的数据从未被使用。

优化依据（真机抓包确认的 Redfish 行为）
---------------------------------------
iBMC 的 Entries 集合首页（``GET /Entries``）**已经把成员内容内联返回**，
并且同时给出总量与分页游标：

    {
      "Members@odata.count": 1415,
      "Members": [ {完整条目 1}, ..., {完整条目 32} ],     ← 已内联，无需再 GET
      "Members@odata.nextLink": "/Entries?$skip=32&$top=32"
    }

因此只需 1 次请求即可同时拿到：总数（用于统计）+ 首批完整条目（用于合规
性抽样校验）。总耗时从「N 次往返」降到「1 次往返」。

设计要点
--------
1. **总数以 ``Members@odata.count`` 为准**，MUST NOT 用 ``len(Members)``——
   后者只是当前页大小，会把 1415 条误报成 32 条。
2. **优先直接解析首页内联成员**；只有当首页成员是「裸链接」（仅有
   ``@odata.id``、无 ``Name``/``EntryType`` 等字段）时，才针对抽样范围内
   的条目逐条 GET 补全。这样既兼容内联型 BMC，也兼容链接型 BMC。
3. **可选随机补充抽样**：默认再取一页中段数据，避免只看到最早的条目
   （历史日志往往集中在开头，中段的格式问题会被漏掉）。取不到不算失败，
   由调用方决定是否降级。
4. helper 只负责「取数据」，不做判定——所有 checks 语义仍由各用例自己
   决定，保证报告标签与历史版本一致。
"""
from __future__ import annotations

import random
from typing import Any, Dict, List

from redfish_sdk.models.logs import LogEntry

# 首页内联成员通常为 32 条（iBMC 默认页大小），足够合规性校验使用。
# 若首页条数不足，则按此上限逐条 GET 补足。
DEFAULT_SAMPLE_SIZE = 32
# 中段补充抽样的条数上限（性能与覆盖面的折中）。
DEFAULT_EXTRA_SAMPLE = 16
# 需要「逐条 GET 完整补全」的前几条。
# 原因：集合首页（含 $expand）返回的成员是**摘要形态**，缺少
# ``@odata.type`` / ``@odata.etag`` / ``Links`` / ``Persistency`` 等字段，
# 而各用例的 DMTF 合规性校验会检查 ``@odata.type``（Required 字段）。
# 校验只针对前 10 条，故仅对这 10 条补全，请求数从 N 降到 10。
DEFAULT_HYDRATE_FIRST = 10
# 摘要形态成员缺少、而单条 GET 才有的标志字段；据此判断是否需要补全。
_FULL_ONLY_FIELDS = ("@odata.type", "@odata.etag")


def _is_full_member(member: Any) -> bool:
    """判断首页返回的成员是否已内联完整内容。

    链接型 BMC 只返回 ``{"@odata.id": "..."}``，此时无法做字段校验，
    需要逐条 GET 补全。内联型（iBMC/xFusion）会带上 ``Name`` /
    ``EntryType`` / ``Id`` 等字段。
    """
    if not isinstance(member, dict):
        return False
    # 除链接外还有任何业务字段即视为内联完整
    return any(k not in ("@odata.id", "@odata.context", "@odata.type") for k in member)


def _member_links(collection: Dict[str, Any]) -> List[str]:
    """从集合响应中提取成员链接列表（保序，去空）。"""
    links: List[str] = []
    for member in collection.get("Members") or []:
        if isinstance(member, dict):
            link = member.get("@odata.id")
            if link:
                links.append(link)
    return links


def _try_expand_page(
    client: Any, entries_url: str, result: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """尝试用 ``?$expand=.($levels=1)`` 一次取回首页内联成员。

    iBMC 的 SEL 等日志服务默认只返回裸 ``@odata.id`` 链接，直接逐条 GET
    会退化成 N 次往返（实测 418 条 ≈ 50 次请求 / 8.6 s）。加上 ``$expand``
    后首页即可内联完整条目，1 次请求即可拿到 32 条完整数据。

    注意：部分 BMC 会**静默忽略** ``$expand`` 并返回裸链接或空集合，
    因此必须校验展开结果确实包含业务字段，否则返回空列表让调用方回退。
    """
    sep = "&" if "?" in entries_url else "?"
    expand_url = f"{entries_url}{sep}$expand=.($levels=1)"
    try:
        page = client.get_raw(expand_url)
        result["requests"] += 1
    except Exception:  # noqa: BLE001 - 不支持则回退
        return []
    if not isinstance(page, dict):
        return []
    members = [m for m in (page.get("Members") or []) if isinstance(m, dict)]
    if members and all(_is_full_member(m) for m in members):
        return members
    return []


def _needs_hydrate(member: Dict[str, Any]) -> bool:
    """判断集合首页返回的成员是否为「摘要形态」，需要逐条 GET 补全。

    iBMC 集合首页（即使带 ``$expand``）返回的成员缺少 ``@odata.type`` 等
    字段，而 DMTF 合规性校验会检查 ``@odata.type`` 是否为 Required。
    """
    return not any(f in member for f in _FULL_ONLY_FIELDS)


def _hydrate_prefix(
    client: Any,
    entries: List[Dict[str, Any]],
    count: int,
    result: Dict[str, Any],
) -> None:
    """对前 ``count`` 条做逐条 GET 补全（原地替换为完整响应）。

    合规性校验只读前 10 条，因此只补全这些，避免退化为全量展开。
    补全失败的单条保留摘要版本，由用例按其字段缺失情况自行判定。
    """
    for idx in range(min(count, len(entries))):
        member = entries[idx]
        if not _needs_hydrate(member):
            continue
        link = member.get("@odata.id")
        if not link:
            continue
        try:
            full = client.get_raw(link)
            result["requests"] += 1
        except Exception:  # noqa: BLE001 - 补全失败保留摘要版本
            continue
        if isinstance(full, dict):
            entries[idx] = full


def fetch_log_entries_sampled(
    client: Any,
    entries_url: str,
    sample_size: int = DEFAULT_SAMPLE_SIZE,
    extra_sample: int = DEFAULT_EXTRA_SAMPLE,
    allow_random: bool = True,
    hydrate_first: int = DEFAULT_HYDRATE_FIRST,
) -> Dict[str, Any]:
    """有界地读取 LogService Entries：1~2 次请求拿到总数 + 抽样条目。

    Args:
        client: ``RedfishClient`` 实例，需要提供 ``get_raw(odata_id)``。
        entries_url: ``log.entries.odata_id``（动态发现的 Entries 集合地址）。
        sample_size: 首页不足时，最多逐条 GET 补足到该条数。
        extra_sample: 中段补充抽样的条数上限；``0`` 表示不做补充抽样。
        allow_random: 是否允许中段随机抽样（关闭后行为完全确定）。
        hydrate_first: 对前 N 条做逐条 GET 完整补全。集合首页返回的是摘要
            形态（缺 ``@odata.type`` 等），而合规性校验只针对前 10 条，
            因此默认只补全 10 条而不是全部。

    Returns:
        dict:
            ``total``      —— 条目总数（取自 ``Members@odata.count``）
            ``entries``    —— 抽样到的 ``LogEntry`` 模型列表（保持服务端顺序）
            ``sampled``    —— ``entries`` 的条数
            ``is_partial`` —— True 表示只取到部分条目（total > sampled）
            ``requests``   —— 实际发出的 HTTP 请求次数（用于日志诊断）
            ``error``      —— 顶层读取失败时的错误描述，成功为 None

    说明：``entries`` 返回 SDK 的 ``LogEntry`` 模型而非原始 dict，是为了让
    调用方的字段访问写法（``entry.id`` / ``entry.created``）保持不变。
    """
    result: Dict[str, Any] = {
        "total": 0,
        "entries": [],
        "sampled": 0,
        "is_partial": False,
        "requests": 0,
        "error": None,
    }

    if not entries_url:
        result["error"] = "entries_url 为空"
        return result

    # ---- 第 1 次请求：集合首页（含总数 + 首批成员） ----
    try:
        collection = client.get_raw(entries_url)
        result["requests"] += 1
    except Exception as exc:  # noqa: BLE001 - 上层统一转成 FAIL，需保留原始信息
        result["error"] = f"读取 Entries 集合失败：{exc}"
        return result

    if not isinstance(collection, dict):
        result["error"] = f"Entries 集合返回类型异常：{type(collection).__name__}"
        return result

    # 总数以 Members@odata.count 为准（首页 Members 只是当前页）
    raw_total = collection.get("Members@odata.count")
    members = collection.get("Members") or []

    entries: List[Dict[str, Any]] = []
    need_fill: List[str] = []

    if members and all(_is_full_member(m) for m in members if isinstance(m, dict)):
        # 内联型：首页成员已是完整条目，直接使用
        entries = [m for m in members if isinstance(m, dict)]
    else:
        # 链接型：先试一次 $expand 内联（支持则省掉 N 次逐条 GET），
        # 不支持再退回逐条 GET 补全。SEL 等部分服务默认返回裸链接。
        need_fill = _member_links(collection)
        expanded = _try_expand_page(client, entries_url, result)
        if expanded:
            entries = expanded
    try:
        total = int(raw_total) if raw_total is not None else len(entries)
    except (TypeError, ValueError):
        total = len(entries)

    # ---- 链接型：按抽样上限逐条 GET 补全（$expand 已成功则跳过） ----
    if need_fill and not entries:
        for link in need_fill[:max(1, sample_size)]:
            try:
                item = client.get_raw(link)
                result["requests"] += 1
            except Exception:  # noqa: BLE001 - 单条失败不影响整体判定
                continue
            if isinstance(item, dict):
                entries.append(item)
        total = max(total, len(entries))

    # ---- 可选：中段补充抽样，覆盖「只在中间日志出现」的格式问题 ----
    if allow_random and extra_sample > 0 and total > len(entries) and members:
        page_size = len(members)
        # 随机挑一个中段偏移（避开首页），按页对齐
        candidates = [off for off in range(page_size, total, page_size)]
        if candidates:
            offset = random.choice(candidates)  # noqa: S311 - 抽样非安全用途
            sep = "&" if "?" in entries_url else "?"
            page_url = f"{entries_url}{sep}$skip={offset}&$top={page_size}"
            # 中段页同样尝试 $expand，避免链接型服务退化成逐条 GET
            expanded_mid = _try_expand_page(client, page_url, result)
            if expanded_mid:
                for member in expanded_mid:
                    if len(entries) >= sample_size + extra_sample:
                        break
                    entries.append(member)
            else:
                try:
                    page = client.get_raw(page_url)
                    result["requests"] += 1
                    if isinstance(page, dict):
                        for member in page.get("Members") or []:
                            if len(entries) >= sample_size + extra_sample:
                                break
                            if isinstance(member, dict) and _is_full_member(member):
                                entries.append(member)
                except Exception:  # noqa: BLE001 - 补充抽样失败不影响主流程
                    pass

    # 前 N 条补全完整字段（摘要形态缺 @odata.type 等，合规性校验需要）
    if hydrate_first > 0:
        _hydrate_prefix(client, entries, hydrate_first, result)

    # 原始 JSON -> LogEntry 模型，保证调用方字段访问写法不变
    models: List[LogEntry] = []
    for raw in entries:
        try:
            models.append(LogEntry.model_validate(raw))
        except Exception:  # noqa: BLE001 - 单条解析失败不阻断整体校验
            continue

    result["total"] = total
    result["entries"] = models
    result["sampled"] = len(models)
    result["is_partial"] = total > len(models)
    return result


def describe_sampling(info: Dict[str, Any]) -> str:
    """把采样结果格式化成一行日志，便于在 runlog 里直观看清开销。"""
    if info.get("error"):
        return f"Entries 读取失败：{info['error']}"
    return (
        f"Entries 总数 {info.get('total', 0)} 条，"
        f"抽样 {info.get('sampled', 0)} 条"
        + ("（部分采样）" if info.get("is_partial") else "（全量）")
        + f"，HTTP 请求 {info.get('requests', 0)} 次"
    )
