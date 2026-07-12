"""后挖掘图分析 - 构建跨 Wing 关联图。

主题隧道（topic tunnels）：跨 Wing 连接共享主题
走廊（hallways）：Wing 内实体共现连接
实体隧道（entity tunnels）：跨 Wing 实体隧道
"""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timezone
from collections import defaultdict

from memory.storage import PalaceStorage

logger = logging.getLogger(__name__)


class HallwayBuilder:
    """后挖掘图分析构建器。"""

    def __init__(self, storage: PalaceStorage):
        self.storage = storage

    def build_all(self) -> dict:
        """构建所有关联图。

        Returns:
            {"topic_tunnels": N, "hallways": N, "entity_tunnels": N}
        """
        topic_count = self.build_topic_tunnels()
        hallway_count = self.build_hallways()
        entity_count = self.build_entity_tunnels()
        return {
            "topic_tunnels": topic_count,
            "hallways": hallway_count,
            "entity_tunnels": entity_count,
        }

    def build_topic_tunnels(self) -> int:
        """构建主题隧道：跨 Wing 连接共享主题。

        扫描所有 Wing 的 Closet，找到共享 topic，创建隧道记录。
        """
        # Get all wings
        wings = self.storage.list_wings()
        if len(wings) < 2:
            return 0

        # Collect topics per wing
        wing_topics: dict[str, set[str]] = {}
        for wing_name, _ in wings:
            rooms = self.storage.list_rooms(wing_name)
            for room_name, _ in rooms:
                # Get drawers for this wing+room
                drawers = self.storage.list_drawers(wing=wing_name, room=room_name, limit=50)
                for drawer in drawers:
                    # Search closets for this source
                    from memory.models import content_hash
                    source_hash = content_hash(drawer.source_file)
                    closets = self.storage.get_closets_by_source(source_hash)
                    for closet in closets:
                        topics = [t.strip() for t in closet.topic.split(";") if t.strip()]
                        if wing_name not in wing_topics:
                            wing_topics[wing_name] = set()
                        wing_topics[wing_name].update(topics)

        # Find shared topics across wings
        created = 0
        wing_list = list(wing_topics.keys())
        for i, wing_a in enumerate(wing_list):
            for wing_b in wing_list[i+1:]:
                shared = wing_topics[wing_a] & wing_topics[wing_b]
                for topic in shared:
                    self._create_hallway(
                        hallway_type="topic_tunnel",
                        wing_from=wing_a, wing_to=wing_b,
                        entity=topic, drawer_ids=""
                    )
                    created += 1

        return created

    def build_hallways(self) -> int:
        """构建走廊：Wing 内实体共现连接。

        同 Wing 内多个 Drawer 出现相同实体，创建走廊记录。
        """
        wings = self.storage.list_wings()
        created = 0

        for wing_name, _ in wings:
            # Get all drawers for this wing
            drawers = self.storage.list_drawers(wing=wing_name, limit=500)
            entity_drawers: dict[str, list[str]] = defaultdict(list)

            for drawer in drawers:
                # Search closets for entities
                from memory.models import content_hash
                source_hash = content_hash(drawer.source_file)
                closets = self.storage.get_closets_by_source(source_hash)
                for closet in closets:
                    entities = [e.strip() for e in closet.entities.split(";") if e.strip()]
                    for entity in entities:
                        entity_drawers[entity].append(drawer.id)

            # Create hallways for entities appearing in multiple drawers
            for entity, drawer_ids in entity_drawers.items():
                if len(drawer_ids) >= 2:
                    self._create_hallway(
                        hallway_type="hallway",
                        wing_from=wing_name, wing_to=wing_name,
                        entity=entity, drawer_ids=",".join(drawer_ids[:10])
                    )
                    created += 1

        return created

    def build_entity_tunnels(self) -> int:
        """构建实体隧道：跨 Wing 出现相同实体。

        不同 Wing 出现相同实体，创建隧道。
        """
        wings = self.storage.list_wings()
        if len(wings) < 2:
            return 0

        wing_entities: dict[str, set[str]] = {}
        for wing_name, _ in wings:
            drawers = self.storage.list_drawers(wing=wing_name, limit=500)
            entities = set()
            for drawer in drawers:
                from memory.models import content_hash
                source_hash = content_hash(drawer.source_file)
                closets = self.storage.get_closets_by_source(source_hash)
                for closet in closets:
                    ents = [e.strip() for e in closet.entities.split(";") if e.strip()]
                    entities.update(ents)
            wing_entities[wing_name] = entities

        # Find shared entities across wings
        created = 0
        wing_list = list(wing_entities.keys())
        for i, wing_a in enumerate(wing_list):
            for wing_b in wing_list[i+1:]:
                shared = wing_entities[wing_a] & wing_entities[wing_b]
                for entity in shared:
                    self._create_hallway(
                        hallway_type="entity_tunnel",
                        wing_from=wing_a, wing_to=wing_b,
                        entity=entity, drawer_ids=""
                    )
                    created += 1

        return created

    def _create_hallway(self, hallway_type: str, wing_from: str, wing_to: str,
                        entity: str, drawer_ids: str) -> None:
        """创建走廊/隧道记录。"""
        now = datetime.now(timezone.utc).isoformat()
        # Generate ID
        raw = f"{hallway_type}|{wing_from}|{wing_to}|{entity}"
        hallway_id = f"hallway_{hashlib.sha256(raw.encode()).hexdigest()[:16]}"

        conn = self.storage._get_conn()
        try:
            conn.execute(
                """
                INSERT OR IGNORE INTO hallways (id, type, wing_from, wing_to, entity, drawer_ids, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (hallway_id, hallway_type, wing_from, wing_to, entity, drawer_ids, now)
            )
            conn.commit()
        except Exception as e:
            logger.warning("创建 hallway 失败: %s", e)
        finally:
            conn.close()
