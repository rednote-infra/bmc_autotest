#!/bin/python
"""
Author: Fengmian
Date: 2025/09/11
Usage: BMC 测试配置加载工具函数
Update:
  2025/06/28：新增
  2026/06/08：从 common_function 中分离为独立模块
  2026/06/08：删除 legacy 脚本后，精简为仅保留 load_config

提供以下工具函数：
  - load_config()    加载并返回 JSON 配置文件内容（dict）
"""

import json


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
