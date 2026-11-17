"""
Redfish Event 测试公共辅助函数（issue/9 引入，思路源自上游 PR #15，实现有调整）。

职责边界：SDK 负责"怎么调接口"，本模块只做"测什么、怎么判定"的测试决策逻辑，
不重复封装 SDK 已有能力。

提供三类辅助：
  1. subscription_event_types(client, preferred) — 订阅 EventTypes 决策：
     优先取 EventTypesForSubscription 与 preferred 的交集，交集为空传 None
     （SDK 对 None 使用厂商默认），避免硬编码 ["Alert"] 在不支持该类型的
     BMC 上创建失败。
  2. message_id_parameters(client, service) — SubmitTestEvent 模式探测：
     通过 @Redfish.ActionInfo 判断 BMC 是 legacy（EventType）还是
     modern（MessageId）参数形态。
  3. resolve_test_message(client, configured_id, message_args) — modern 模式
     的 MessageId 解析：优先用配置值，否则从 Base Registry 找 NumberOfArgs=0
     的消息（如 Base.1.x.Success）。Registry 不可发现时抛 ValueError，
     由调用方降级为 WARNING（不 FAIL）。
"""
from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Redfish Event 1.5.0 起订阅资源不再强制返回 EventTypes（EventDestination
# 类型演进为 EventDestination_v1_5_0 后 EventTypes 可为 null，改用
# MessageIds/RegistryPrefixes 等过滤字段）。
_MODERN_EVENT_TYPE_RE = re.compile(r"\.v(\d+)_(\d+)_(\d+)\.")


def modern_event_destination(odata_type: Optional[str]) -> bool:
    """判断订阅资源 @odata.type 是否为 EventDestination v1.5.0+（EventTypes 可空）。"""
    match = _MODERN_EVENT_TYPE_RE.search(odata_type or "")
    return bool(match and tuple(map(int, match.groups())) >= (1, 5, 0))


def subscription_event_types(client, preferred: Optional[List[str]] = None) -> Optional[List[str]]:
    """决策订阅创建使用的 EventTypes。

    策略（issue/9 评审确定，较上游 PR #15 原实现更稳）：
      1. 读取 BMC 声明的 EventTypesForSubscription；
      2. 与 preferred（默认 ["Alert"]）求交集——BMC 明确支持才显式传入；
      3. 交集为空（含 BMC 未声明该字段的情况）返回 None，
         由 SDK 使用厂商默认值，避免硬编码类型在不支持的 BMC 上创建失败。

    Returns:
        EventTypes 列表（显式传入）或 None（交由厂商默认）。
    """
    preferred = preferred or ["Alert"]
    try:
        supported = client.get_event_service().event_types_for_subscription or []
    except Exception:
        # EventService 读取失败时不阻塞创建，交由厂商默认
        return None
    if not supported:
        return None
    overlap = [t for t in preferred if t in supported]
    return overlap or None


def message_id_parameters(client, service) -> Optional[set]:
    """探测 SubmitTestEvent 支持的参数形态。

    Returns:
        - None：legacy 模式（仅支持 EventType，按 EventType 循环提交）；
        - set：modern 模式，返回 ActionInfo 声明为 Required 的参数名集合
          （服务未声明 Required 时为空集合，此时只提交 MessageId/MessageArgs）。

    判定依据（优先级）：
      1. ActionInfo 的 Parameters 含 MessageId → modern，返回 Required 子集；
      2. ActionInfo 存在但无 MessageId → legacy；
      3. 无 ActionInfo 但声明了 EventType@Redfish.AllowableValues → legacy；
      4. 两者皆无 → 按 modern 处理（DMTF 演进方向，EventType 已废弃）。
    """
    action = (service.actions or {}).get("#EventService.SubmitTestEvent", {})
    info_uri = action.get("@Redfish.ActionInfo")
    if info_uri:
        try:
            params = [
                param for param in client.get_raw(info_uri).get("Parameters", [])
                if param.get("Name")
            ]
        except Exception:
            params = []
        if params:
            if not any(param["Name"] == "MessageId" for param in params):
                return None
            return {param["Name"] for param in params if param.get("Required")}
    if action.get("EventType@Redfish.AllowableValues"):
        return None
    return {"MessageId", "MessageArgs"}


def resolve_test_message(client, configured_id: Optional[str] = None,
                         message_args: Optional[list] = None) -> Tuple[str, list]:
    """解析 modern 模式提交使用的 MessageId（及 MessageArgs）。

    优先级：
      1. conf 显式配置的 TestMessageId（含 TestMessageArgs）；
      2. 自动从 Base Registry 找 NumberOfArgs=0 的消息（如 Base.1.11.Success），
         无参数消息无需 MessageArgs，兼容性最好。

    Raises:
        ValueError：Registry 不可发现且未配置 TestMessageId。
        调用方应捕获并降级为 WARNING（不 FAIL），避免引入新误报源。
    """
    if configured_id:
        return configured_id, message_args or []
    for registry in client.get_registries():
        for location in registry.location or []:
            if not location.uri or location.language not in (None, "en"):
                continue
            try:
                data = client.get_raw(location.uri)
            except Exception:
                continue
            if data.get("RegistryPrefix") != "Base":
                continue
            success = data.get("Messages", {}).get("Success", {})
            if success.get("NumberOfArgs") != 0:
                continue
            major, minor = data["RegistryVersion"].split(".")[:2]
            return f"Base.{major}.{minor}.Success", []
    raise ValueError("无法从 Base Registry 自动解析 MessageId，且未配置 TestMessageId")
