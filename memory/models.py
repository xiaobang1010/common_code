"""记忆宫殿数据模型 - Palace 隐喻的核心数据结构。

定义三层数据模型：
  - Drawer（抽屉）：最小存储单元，存放原始文本片段
  - ClosetEntry（壁橱条目）：搜索索引指针，指向一组抽屉
  - KGTriple（知识图谱三元组）：结构化事实，带时间有效期
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# Drawer - 抽屉（最小存储单元）
# ---------------------------------------------------------------------------


@dataclass
class Drawer:
    """抽屉 - Palace 的最小存储单元。

    每个抽屉存放一段原始文本（约 800 字符/块），附带来源、时间、重要性等元数据。

    Attributes:
        id: 唯一 ID（基于内容哈希）
        wing: 顶层命名空间（project/person/domain）
        room: wing 内的子分类
        content: 原始文本内容（约 800 字符/块）
        source_file: 来源文件路径
        filed_at: 入库时间（ISO 8601）
        authored_at: 内容原始创建时间（ISO 8601）
        chunk_index: 来源文件内的分块索引（0-based）
        importance: 重要性评分（0.0-1.0，默认 0.5）
        source_mtime: 来源文件的 mtime，用于增量更新
        content_hash: 内容的 SHA-256 哈希，用于去重
    """

    id: str
    wing: str
    room: str
    content: str
    source_file: str = ""
    filed_at: str = ""
    authored_at: str = ""
    chunk_index: int = 0
    importance: float = 0.5
    source_mtime: float | None = None
    content_hash: str = ""


# ---------------------------------------------------------------------------
# ClosetEntry - 壁橱条目（搜索索引指针）
# ---------------------------------------------------------------------------


@dataclass
class ClosetEntry:
    """壁橱条目 - 搜索索引指针，指向一组抽屉。

    壁橱是对抽屉的二级索引：按来源文件聚合，记录主题、实体、日期范围，
    以及关联的抽屉 ID 列表。用于快速定位相关抽屉。

    Attributes:
        id: 唯一 ID
        source_hash: 来源文件路径的哈希
        topic: 动词 + 上下文短语（描述这组抽屉的内容主题）
        entities: 分号分隔的实体名
        date_line: 日期与行范围（格式：YYYY-MM-DD:Lstart-Lend）
        drawer_ids: 逗号分隔的抽屉 ID
        created_at: 创建时间（ISO 8601）
    """

    id: str
    source_hash: str
    topic: str = ""
    entities: str = ""
    date_line: str = ""
    drawer_ids: str = ""
    created_at: str = ""


# ---------------------------------------------------------------------------
# KGTriple - 知识图谱三元组
# ---------------------------------------------------------------------------


@dataclass
class KGTriple:
    """知识图谱三元组 - 带时间有效期的结构化事实。

    用于存储实体间关系，支持时间线查询和事实失效。
    valid_to 为 None 表示该事实当前仍然有效。

    Attributes:
        id: 唯一 ID
        subject: 主体实体
        predicate: 关系类型
        object: 客体实体或值
        valid_from: 事实生效时间（ISO 8601）
        valid_to: 事实失效时间（ISO 8601），None 表示仍有效
        drawer_refs: 关联抽屉 ID（逗号分隔）
        created_at: 创建时间（ISO 8601）
        confidence: 置信度评分（0.0-1.0，默认 1.0）
        source_file: 提取该事实的来源文件
        source_drawer_id: 来源抽屉 ID，用于溯源
        extracted_at: 三元组提取时间（ISO 8601）
    """

    id: str
    subject: str
    predicate: str
    object: str
    valid_from: str = ""
    valid_to: str | None = None
    drawer_refs: str = ""
    created_at: str = ""
    confidence: float = 1.0
    source_file: str = ""
    source_drawer_id: str = ""
    extracted_at: str = ""
