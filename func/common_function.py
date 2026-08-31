#!/bin/python
"""
Author: Fengmian
Date: 2025/09/11
Usage: 公共函数
Update:
2025/06/28：新增
2026/06/08：精简未使用函数，仅保留 print_log / add_key_value_to_json / find_value_by_key
"""

import json
import os
import sys
import time
class CommonFunction:
    """通用工具函数集合

    当前保留方法：
      - print_log()          分级日志打印（ERROR/INFO/DEBUG/WARNING）
      - add_key_value_to_json()  向 JSON 结果文件写入键值对
      - find_value_by_key()  递归搜索字典中指定键的值
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
