"""
BMC 管理网口（Manager EthernetInterfaces）公共工具函数。

背景：BMC 通常有 1~2 个管理网口——
  - 专用管理口（Detect）：承载 BMC 管理 IP，须启用并配置静态 IP；
  - Sharelink(NCSI) 口：与业务网口共用链路，我方规范要求默认关闭。

问题：Redfish 标准与 SDK 均未提供"网口角色"字段。部分厂商用 OEM 扩展
  `Oem.Public.NCSIModeSelect` 标识 Sharelink 口，但并非所有厂商都实现，
  仅靠该字段判断会导致 Sharelink 口被误判为专用口。

约定（issue/21）：厂商默认**序号最小的网口（eth0）为专用口**，其余
  （eth1 ...）为 Sharelink 口；仅有一个网口的机型，该口即为专用口。
本模块按此约定定位网口角色，供 managers_003 / managers_026 共用，
避免脚本层级互相引用。

Usage:
    from func.bmc_network_function import classify_eth_roles, ROLE_DETECT, ROLE_SHARELINK
    for iface, role in classify_eth_roles(client.get_manager_ethernet_interfaces()):
        ...
"""

import re

# 网口角色常量
ROLE_DETECT    = "Detect"       # 专用管理口
ROLE_SHARELINK = "Sharelink"    # Sharelink(NCSI) 口

# 从 Id / Name 末尾提取序号（"eth0"→0, "eth1"→1, "1"→1）
_IFACE_INDEX_RE = re.compile(r"(\d+)\s*$")


def _iface_index(iface, fallback: int) -> int:
    """提取网口序号：优先 Id，其次 Name；均无数字时回落到列表位置。

    兼容 SDK 模型对象（属性访问）与原始 dict（键访问）。
    """
    for attr in ("id", "name"):
        val = getattr(iface, attr, None)
        if val is None and isinstance(iface, dict):
            val = iface.get(attr) or iface.get(attr.capitalize())
        if val:
            m = _IFACE_INDEX_RE.search(str(val))
            if m:
                return int(m.group(1))
    return fallback


def classify_eth_roles(interfaces) -> list:
    """按约定判定各管理网口的角色。

    规则：序号最小的网口为专用口（Detect），其余为 Sharelink 口。
      - 单网口机型（仅 eth0）：该口 = Detect
      - 双网口机型（eth0 + eth1）：eth0 = Detect，eth1 = Sharelink
      - 三口及以上：序号最小 = Detect，其余 = Sharelink

    :param interfaces: get_manager_ethernet_interfaces() 返回的网口列表
    :return: list[tuple[iface, role]]，按序号升序排列
    """
    if not interfaces:
        return []

    indexed = [(_iface_index(iface, idx), idx, iface)
               for idx, iface in enumerate(interfaces)]
    indexed.sort(key=lambda t: (t[0], t[1]))

    result = []
    for pos, (_, _, iface) in enumerate(indexed):
        result.append((iface, ROLE_DETECT if pos == 0 else ROLE_SHARELINK))
    return result


def find_sharelink_ifaces(interfaces) -> list:
    """返回被判定为 Sharelink(NCSI) 口的网口列表（可能为空，如单网口机型）。"""
    return [iface for iface, role in classify_eth_roles(interfaces)
            if role == ROLE_SHARELINK]


def find_detect_ifaces(interfaces) -> list:
    """返回被判定为专用管理口（Detect）的网口列表。"""
    return [iface for iface, role in classify_eth_roles(interfaces)
            if role == ROLE_DETECT]
