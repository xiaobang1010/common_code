"""ID 生成与内容哈希工具。

提供内容去重和唯一 ID 生成功能：
  - content_hash: SHA-256 哈希
  - generate_drawer_id: 抽屉 ID 生成
  - generate_triple_id: 三元组 ID 生成
"""

from __future__ import annotations

import hashlib


def content_hash(text: str) -> str:
    """计算文本的 SHA-256 哈希值，用于内容去重。

    Args:
        text: 原始文本

    Returns:
        SHA-256 十六进制摘要（64 字符）
    """
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def generate_drawer_id(wing: str, room: str, content: str) -> str:
    """根据 wing + room + content 生成抽屉唯一 ID。

    ID 格式：drawer_{sha256(wing|room|content)[:16]}
    相同 wing/room/content 会生成相同 ID，用于内容去重。

    Args:
        wing: 顶层命名空间（project/person/domain）
        room: wing 内的子分类
        content: 抽屉原始文本

    Returns:
        16 字符哈希前缀的抽屉 ID
    """
    raw = f"{wing}|{room}|{content}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"drawer_{h}"


def generate_triple_id(subject: str, predicate: str, object: str) -> str:
    """根据 subject + predicate + object 生成三元组唯一 ID。

    ID 格式：triple_{sha256(subject|predicate|object)[:16]}
    相同三元组生成相同 ID，用于幂等插入。

    Args:
        subject: 主体实体
        predicate: 关系类型
        object: 客体实体或值

    Returns:
        16 字符哈希前缀的三元组 ID
    """
    raw = f"{subject}|{predicate}|{object}"
    h = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return f"triple_{h}"
