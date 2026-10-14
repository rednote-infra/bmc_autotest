#!/bin/python
"""
Author: Fengmian
Date: 2025/09/11
Usage: BMC 测试配置加载工具函数
Update:
  2025/06/28：新增
  2026/06/08：从 common_function 中分离为独立模块
  2026/06/08：删除 legacy 脚本后，精简为仅保留 load_config
  2026/09/16：新增按项目区分的单例资源 ID 解析（resolve_uri / get_singleton_id）

提供以下工具函数：
  - load_config()        加载并返回 JSON 配置文件内容（dict）
  - load_project_name()  读取 conf/bmc/bmc_runner_suites.json 的 project 字段
  - get_singleton_id()   按项目返回 Redfish 单例资源 ID（安擎 → Self，其他 → 1）
  - resolve_uri()        按项目把 URI 中的单例资源 ID 归一化

单例资源 ID 说明
----------------
Redfish 中 Systems / Managers / Chassis 均为「单实例集合」，其资源 ID 由厂商固件决定：

  - 安擎（AnQing）项目固件：/redfish/v1/Systems/Self、/redfish/v1/Managers/Self、
                            /redfish/v1/Chassis/Self
  - 其他项目固件：           /redfish/v1/Systems/1、/redfish/v1/Managers/1、
                            /redfish/v1/Chassis/1

因此脚本内统一书写「/1」形式的 URI，运行时由 resolve_uri() 按项目自动归一化：

    uri = resolve_uri("/redfish/v1/Systems/1")   # 安擎 → /redfish/v1/Systems/Self
                                                 # 其他 → /redfish/v1/Systems/1

只替换集合下「第一个资源 ID 段」，更深层的同名字段（如 /Systems/1/Processors/1）不受影响。
"""

import json
import os
import re

# ── 项目区分配置 ────────────────────────────────────────────────────────────────
# 项目名从 conf/bmc/bmc_runner_suites.json 的顶层 project 字段读取（避免新增配置文件）
_PROJECT_CONF_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "conf", "bmc", "bmc_runner_suites.json",
)

# 「安擎」项目别名（中英文、大小写均可识别）
_ANQING_ALIASES = {"anqing", "安擎", "enginetech"}

# 单例资源 ID：默认项目 / 安擎项目
SINGLETON_ID_DEFAULT = "1"
SINGLETON_ID_ANQING  = "Self"

# 受管的单例集合（仅这三个集合下的第一个资源 ID 段参与替换）
SINGLETON_COLLECTIONS = ("Systems", "Managers", "Chassis")

# 匹配：可选 scheme+host 前缀 + /redfish/v1/<单例集合>/1（后接 / 或结尾）
_SINGLETON_RE = re.compile(
    r"^(?P<prefix>https?://[^/]+)?"
    r"(?P<base>/redfish/v1/(?:" + "|".join(SINGLETON_COLLECTIONS) + r")/)"
    r"1(?=/|$)"
)

# 进程内缓存（配置文件一次读取即可）
_project_name_cache = None


def load_config(config_path: str) -> dict:
    """
    加载 JSON 配置文件并返回解析后的字典。

    :param config_path: JSON 文件路径（绝对路径或相对于工作目录）
    :return: 解析后的配置字典
    :raises FileNotFoundError: 文件不存在时
    :raises json.JSONDecodeError: JSON 格式错误时
    """
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_project_name() -> str:
    """
    读取当前被测项目名，取值优先级：
      1. conf/bmc/bmc_runner_suites.json 顶层 ``project`` 字段
      2. 环境变量 ``BMC_PROJECT``
      3. 空字符串（视为非安擎项目）

    :return: 项目名字符串，如 "AnQing" / "ZTE"；未配置时返回 ""
    """
    global _project_name_cache
    if _project_name_cache is not None:
        return _project_name_cache

    name = ""
    try:
        conf = load_config(_PROJECT_CONF_PATH)
        name = str(conf.get("project", "") or "").strip()
    except Exception:
        name = ""

    if not name:
        name = str(os.environ.get("BMC_PROJECT", "") or "").strip()

    _project_name_cache = name
    return name


def is_anqing_project() -> bool:
    """当前项目是否为「安擎」（名称匹配中文/英文任一写法，忽略大小写和空格）。"""
    name = load_project_name().replace(" ", "").lower()
    return bool(name) and name in _ANQING_ALIASES


def get_singleton_id() -> str:
    """
    按项目返回 Redfish 单例资源 ID。

    :return: 安擎项目 → ``"Self"``；其他项目 → ``"1"``
    """
    return SINGLETON_ID_ANQING if is_anqing_project() else SINGLETON_ID_DEFAULT


def resolve_uri(uri):
    """
    按项目把 Redfish URI 中的单例资源 ID 归一化。

    仅替换 ``/redfish/v1/{Systems|Managers|Chassis}/`` 下的**第一个**资源 ID 段，
    且仅当该段为 ``1`` 时才替换（严格匹配，不做模糊改写）。非字符串入参原样返回。

    :param uri: 相对路径（``/redfish/v1/Systems/1``）或完整 URL
    :return: 归一化后的 URI

    示例::

        resolve_uri("/redfish/v1/Systems/1")            # 安擎 → /redfish/v1/Systems/Self
        resolve_uri("/redfish/v1/Systems/1/Processors") # 安擎 → /redfish/v1/Systems/Self/Processors
        resolve_uri("/redfish/v1/Systems/1/Processors/1")  # 末尾的 /1 保持不变
    """
    if not isinstance(uri, str):
        return uri

    singleton_id = get_singleton_id()
    if singleton_id == SINGLETON_ID_DEFAULT:
        return uri   # 非安擎项目：/1 即为正确值，零改动

    return _SINGLETON_RE.sub(
        lambda m: (m.group("prefix") or "") + m.group("base") + singleton_id,
        uri,
    )


def resolve_uris(uris):
    """对 URI 列表逐项执行 :func:`resolve_uri`（返回新列表，不修改入参）。"""
    if not isinstance(uris, (list, tuple)):
        return uris
    return [resolve_uri(u) for u in uris]
