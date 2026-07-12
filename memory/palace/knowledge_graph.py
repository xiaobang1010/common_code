"""时序知识图谱 - 基于 SQLite 的实体关系图谱。

三元组结构：(subject, predicate, object) 附带时间有效性。
支持 as_of 时间点查询、事实失效（非删除）、时间线查询。
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone

from memory.palace.models import KGTriple
from memory.palace.ids import generate_triple_id
from memory.sqlite_store import PalaceStorage

logger = logging.getLogger(__name__)


class KnowledgeGraph:
    """时序知识图谱管理器。

    封装 PalaceStorage 的 KG 三元组操作，提供：
    - add_triple: 添加带时间有效期的三元组
    - query_entity: 查询实体关系（支持 as_of 时间点过滤、方向选择）
    - query_timeline: 查询实体完整时间线
    - invalidate: 标记旧事实失效（设置 valid_to）
    - supersede: 原子替换事实（关闭旧事实 + 打开新事实）
    - query_relationship: 按关系类型查询
    - list_entities: 列出所有已知实体
    - format_triple: 格式化三元组为可读字符串

    时序语义：
    - valid_from: 事实开始为真的时间点
    - valid_to: 事实不再为真的时间点（半开区间 [valid_from, valid_to)）
    - as_of 查询：返回 valid_from <= as_of AND (valid_to IS NULL OR valid_to > as_of) 的三元组
    - 失效而非删除：事实变化时设置 valid_to，保留历史
    """

    def __init__(self, storage: PalaceStorage):
        self._storage = storage

    def add_triple(self, subject: str, predicate: str, object: str,
                   valid_from: str | None = None,
                   drawer_refs: str = "",
                   confidence: float = 1.0,
                   source_file: str = "",
                   source_drawer_id: str = "",
                   extracted_at: str = "") -> KGTriple:
        """添加三元组。

        Args:
            subject: 主体实体
            predicate: 关系类型（如 child_of, works_on, loves, has_issue）
            object: 客体实体或值
            valid_from: 生效时间（ISO 8601），None 则用当前 UTC 时间
            drawer_refs: 关联 Drawer ID（逗号分隔）
            confidence: 置信度评分（0.0-1.0，默认 1.0）
            source_file: 提取该事实的来源文件
            source_drawer_id: 来源抽屉 ID，用于溯源
            extracted_at: 三元组提取时间（ISO 8601）

        Returns:
            创建的 KGTriple 对象（若已存在则返回现有）
        """
        now = datetime.now(timezone.utc).isoformat()
        triple_id = generate_triple_id(subject, predicate, object)
        triple = KGTriple(
            id=triple_id,
            subject=subject,
            predicate=predicate,
            object=object,
            valid_from=valid_from or now,
            valid_to=None,
            drawer_refs=drawer_refs,
            created_at=now,
            confidence=confidence,
            source_file=source_file,
            source_drawer_id=source_drawer_id,
            extracted_at=extracted_at or now,
        )
        inserted = self._storage.add_triple(triple)
        if not inserted:
            # 三元组已存在，查询并返回现有记录
            for existing in self._storage.query_triples_by_entity(subject):
                if existing.id == triple_id:
                    return existing
        return triple

    def query_entity(self, entity: str, as_of: str | None = None,
                     direction: str = "outgoing") -> list[KGTriple]:
        """查询实体关系。

        Args:
            entity: 实体名
            as_of: 时间点过滤（ISO 8601），只返回该时间点有效的事实。
                   None 则用当前 UTC 时间，即只返回当前有效的事实。
            direction: outgoing (subject=entity), incoming (object=entity), both

        Returns:
            三元组列表，按 valid_from 排序
        """
        if as_of is None:
            as_of = datetime.now(timezone.utc).isoformat()
        if direction == "both":
            # 查询双向并合并去重
            outgoing = self._storage.query_triples_by_entity(entity, as_of)
            incoming = self._storage.query_triples_by_object(entity, as_of)
            seen: set[str] = set()
            result: list[KGTriple] = []
            for t in outgoing + incoming:
                if t.id not in seen:
                    seen.add(t.id)
                    result.append(t)
            result.sort(key=lambda t: t.valid_from)
            return result
        elif direction == "incoming":
            return self._storage.query_triples_by_object(entity, as_of)
        else:
            return self._storage.query_triples_by_entity(entity, as_of)

    def query_timeline(self, entity: str) -> list[KGTriple]:
        """查询实体的完整时间线。

        返回 subject 或 object 等于 entity 的所有三元组，
        按 valid_from 排序，包含已失效的事实。

        Args:
            entity: 实体名

        Returns:
            三元组列表，按 valid_from 排序
        """
        return self._storage.query_timeline(entity)

    def invalidate(self, subject: str, predicate: str, object: str,
                   ended: str | None = None) -> int:
        """使匹配的三元组失效。

        设置 valid_to 为 ended，不删除记录。

        Args:
            subject: 主体实体
            predicate: 关系类型
            object: 客体实体
            ended: 失效时间（ISO 8601），None 则用当前 UTC 时间

        Returns:
            失效的三元组数量
        """
        ended = ended or datetime.now(timezone.utc).isoformat()
        return self._storage.invalidate_triple(subject, predicate, object, ended)

    def supersede(self, subject: str, predicate: str, old_object: str,
                  new_object: str, at: str | None = None) -> dict:
        """原子替换事实 - 在单一事务内关闭旧事实 + 打开新事实。

        Args:
            subject: 主体实体
            predicate: 关系类型
            old_object: 旧客体
            new_object: 新客体
            at: 边界时间，None 则用当前 UTC

        Returns:
            {"invalidated": count, "added": triple_dict}
        """
        at = at or datetime.now(timezone.utc).isoformat()
        # Normalize to YYYY-MM-DDT00:00:00Z if date-only
        if len(at) == 10:
            at = at + "T00:00:00Z"

        # Invalidate old fact
        count = self.invalidate(subject, predicate, old_object, ended=at)

        # Add new fact
        triple = self.add_triple(subject, predicate, new_object, valid_from=at)

        return {"invalidated": count, "added": self._triple_to_dict(triple)}

    def query_relationship(self, predicate: str,
                            as_of: str | None = None) -> list[KGTriple]:
        """按关系类型查询，支持时序过滤。

        Args:
            predicate: 关系类型
            as_of: 时间点过滤（ISO 8601），None 则用当前 UTC 时间

        Returns:
            三元组列表，按 valid_from 排序
        """
        if as_of is None:
            as_of = datetime.now(timezone.utc).isoformat()
        return self._storage.query_triples_by_predicate(predicate, as_of)

    def _triple_to_dict(self, triple: KGTriple) -> dict:
        """将 KGTriple 对象转换为 dict。"""
        return {
            "id": triple.id,
            "subject": triple.subject,
            "predicate": triple.predicate,
            "object": triple.object,
            "valid_from": triple.valid_from,
            "valid_to": triple.valid_to,
            "drawer_refs": triple.drawer_refs,
            "created_at": triple.created_at,
        }

    def list_entities(self) -> list[str]:
        """列出知识图谱中的所有已知实体（主体和客体去重）。

        Returns:
            实体名列表
        """
        return self._storage.list_all_entities()

    def add_entity(self, name: str, entity_type: str = "",
                   properties: str = "{}") -> bool:
        """添加实体记录。

        Args:
            name: 实体名称
            entity_type: 实体类型
            properties: 实体属性（JSON 字符串）

        Returns:
            True 插入成功，False 表示实体已存在（重复）
        """
        entity_id = f"entity_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:16]}"
        return self._storage.add_entity(entity_id, name, entity_type, properties)

    def get_entity(self, name: str) -> dict | None:
        """按名称获取实体。

        Args:
            name: 实体名称

        Returns:
            实体字典 {id, name, type, properties}，不存在返回 None
        """
        return self._storage.get_entity(name)

    def format_triple(self, triple: KGTriple) -> str:
        """格式化三元组为可读字符串。

        格式：[valid_from ~ valid_to] subject --predicate--> object
        valid_to 为 None 时显示 "now"

        Returns:
            格式化的字符串
        """
        valid_to = triple.valid_to if triple.valid_to else "now"
        return (f"[{triple.valid_from} ~ {valid_to}] "
                f"{triple.subject} --{triple.predicate}--> {triple.object}")

    def format_triples(self, triples: list[KGTriple]) -> str:
        """格式化多个三元组为可读文本。

        Returns:
            多行格式化字符串
        """
        return "\n".join(self.format_triple(t) for t in triples)
