#!/bin/python
"""
Author: Fengmian
Date: 2025/09/11
Usage: 公共函数
Update:
2025/06/28：新增
2026/06/08：精简未使用函数，仅保留 print_log / add_key_value_to_json / find_value_by_key
2026/10/26：新增跨平台工具（issue/9，思路源自上游 PR #6 / commit 144b591）——
  is_windows() / ping_cmd() / ipmitool_bin()：Windows 与 Linux 的 ping 参数、
  ipmitool 可执行名差异统一收敛，供所有脚本复用（原各脚本裸写
  ["ping", "-c", "1", "-W", "2", host] 在 Windows 下不可用）
"""

import json
import os
import re
import subprocess
import sys
import time


def is_windows() -> bool:
    """当前是否为 Windows 平台"""
    return sys.platform.startswith("win")


def ping_cmd(host: str, timeout_sec: int = 2) -> list:
    """构建单次 ping 命令（跨平台）

    Windows 用 -n（次数）/-w（超时，毫秒）；Linux/macOS 用 -c（次数）/-W（超时，秒）。
    :param host:        目标 IP 或主机名
    :param timeout_sec: 单次 ping 超时秒数（Windows 内部换算为毫秒）
    :return: subprocess.run 可用的命令列表
    """
    if is_windows():
        return ["ping", "-n", "1", "-w", str(int(timeout_sec * 1000)), host]
    return ["ping", "-c", "1", "-W", str(timeout_sec), host]


def ping_once(host: str, timeout_sec: int = 2) -> bool:
    """执行单次 ping，返回是否通（跨平台）"""
    try:
        r = subprocess.run(
            ping_cmd(host, timeout_sec),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        return r.returncode == 0
    except Exception:
        return False


def ipmitool_bin() -> str:
    """ipmitool 可执行名（Windows 下无原生 ipmitool，调用方应先 is_windows() 判断跳过）"""
    return "ipmitool.exe" if is_windows() else "ipmitool"


# ---------------------------------------------------------------------------
# 时区偏移归一化
# ---------------------------------------------------------------------------
_TZ_PREFIX_RE = re.compile(r"^(UTC|GMT)", re.IGNORECASE)
_TZ_OFFSET_RE = re.compile(r"^([+-])(\d{1,2}):?(\d{2})?$")


def normalize_offset(value) -> str:
    """把各种时区偏移写法归一成 ``"+08:00"`` / ``"-05:00"``。

    Redfish 规范（DSP0266）**明文允许** ``UTC+hh:mm`` 这种带前缀的形式，
    不同厂商返回的形态也不一致，直接做字符串比较必然误判。实测遇到的形态::

        "UTC+08:00"  -> "+08:00"
        "GMT+08:00"  -> "+08:00"
        "+08:00"     -> "+08:00"
        "+8:00"      -> "+08:00"      （小时不补零）
        "UTC"        -> ""            （仅 UTC，无偏移量）
        "Asia/Tokyo" -> ""            （时区名，非偏移格式，需调用方另行处理）
        None / ""    -> ""

    Args:
        value: 原始偏移字符串（或 None）

    Returns:
        归一化后的 ``±hh:mm`` 字符串；无法识别时返回 ``""``。
    """
    s = str(value or "").strip()
    if not s:
        return ""
    s = _TZ_PREFIX_RE.sub("", s).strip()
    if not s.startswith(("+", "-")):
        return ""
    m = _TZ_OFFSET_RE.match(s)
    if not m:
        return s
    sign, hh, mm = m.group(1), int(m.group(2)), m.group(3) or "00"
    return "%s%02d:%s" % (sign, hh, mm)


def offsets_equal(a, b) -> bool:
    """语义比较两个时区偏移，``UTC+08:00`` 与 ``+08:00`` 视为相等。"""
    return normalize_offset(a) == normalize_offset(b)


class CommonFunction:
    """通用工具函数集合

    当前保留方法：
      - print_log()          分级日志打印（ERROR/INFO/DEBUG/WARNING）
      - add_key_value_to_json()  向 JSON 结果文件写入键值对
      - find_value_by_key()  递归搜索字典中指定键的值

    模块级函数（非类方法）：
      - is_windows() / ping_cmd() / ping_once() / ipmitool_bin()  跨平台工具
    """

    def __init__(self):
        pass

    @staticmethod
    def print_log(level, content):
        """打印日志输出，分级别，加入时间戳，分流到 stdout/stderr
        :param level: ERROR → stderr；其余（INFO / DEBUG / WARNING / LOG）→ stdout
        :param content: 日志内容字符串"""
        timestamp = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime())
        if level == "ERROR":
            print(f'[{timestamp}] [{level}] {content}', file=sys.stderr, flush=True)
        else:
            print(f'[{timestamp}] [{level}] {content}', file=sys.stdout, flush=True)

    def add_key_value_to_json(self, file, path, key=None, value=None):
        """添加键值对到 JSON 文件，自动创建缺失的中间路径
        :param file:  JSON 文件路径（绝对路径）
        :param path:  点分隔的嵌套路径，如 "summary" 或 "detail.cycle"
        :param key:   键名（None 表示向列表追加 value）
        :param value: 要写入的值"""
        # 读取或初始化
        if os.path.exists(file):
            with open(file, "r") as f:
                data = json.load(f)
        else:
            data = {}
            os.makedirs(os.path.dirname(file), exist_ok=True)

        clean_path = path.strip('.')
        parts = clean_path.split('.') if clean_path else []

        current = data
        if not parts:
            if key is None:
                if not isinstance(data, list):
                    raise TypeError("根层级需要是列表才能追加值")
                data.append(value)
            else:
                data[key] = value
        else:
            for part in parts[:-1]:
                if part not in current:
                    current[part] = {}
                elif not isinstance(current[part], dict):
                    raise TypeError(f"路径 '{part}' 不是字典类型")
                current = current[part]

            last_key = parts[-1]
            if key is None:
                if last_key not in current:
                    current[last_key] = []
                elif not isinstance(current[last_key], list):
                    raise TypeError(f"字段 '{last_key}' 不是列表类型")
                current[last_key].append(value)
            else:
                if last_key not in current:
                    current[last_key] = {}
                elif not isinstance(current[last_key], dict):
                    raise TypeError(f"字段 '{last_key}' 不是字典类型")
                current[last_key][key] = value

        with open(file, 'w') as f:
            json.dump(data, f, indent=4, ensure_ascii=False)
            self.print_log("DEBUG", f"数据已导入 {file} 中")

    def find_value_by_key(self, data, target_key):
        """递归搜索字典/列表中指定键的值
        :param data:       JSON 数据（dict 或 list）
        :param target_key: 目标键名，字符串
        :return: 找到的值，未找到则返回 None"""
        found_value = None
        if isinstance(data, dict):
            if target_key in data:
                value = data[target_key]
                if value is not None:
                    return value
                else:
                    found_value = None
            for value in data.values():
                result = self.find_value_by_key(value, target_key)
                if result is not None:
                    return result
        elif isinstance(data, list):
            for item in data:
                result = self.find_value_by_key(item, target_key)
                if result is not None:
                    return result
        return found_value
