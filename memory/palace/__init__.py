"""记忆宫殿核心模块 - Palace 数据结构与索引。

统一管理 Palace 特性的数据模型、ID 生成、文本清理、壁橱索引和碰撞检测。
"""

from memory.palace.models import Drawer, ClosetEntry, KGTriple
from memory.palace.ids import content_hash, generate_drawer_id, generate_triple_id
from memory.palace.sanitize import sanitize_content
from memory.palace.closet import ClosetExtractor, ClosetIndexer
from memory.palace.collision_scan import assert_no_collisions
from memory.palace.bm25 import bm25_scores, tokenize
from memory.palace.knowledge_graph import KnowledgeGraph

__all__ = [
    "Drawer",
    "ClosetEntry",
    "KGTriple",
    "content_hash",
    "generate_drawer_id",
    "generate_triple_id",
    "sanitize_content",
    "ClosetExtractor",
    "ClosetIndexer",
    "assert_no_collisions",
    "bm25_scores",
    "tokenize",
    "KnowledgeGraph",
    "PalaceManager",
]

# PalaceManager 协调者
from memory.palace.manager import PalaceManager
