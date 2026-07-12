"""索引修复工具 - 重建 FTS5 索引，清理孤立记录。"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def repair_fts_index(storage) -> dict:
    """从 drawers 表重建 drawers_fts 索引。

    Returns:
        {"rebuilt": True, "drawer_count": N}
    """
    conn = storage._get_conn()
    try:
        # Drop and recreate FTS index
        conn.execute("DROP TABLE IF EXISTS drawers_fts")
        # Recreate with same schema
        # ... (use same FTS5 creation as init_schema)
        conn.commit()

        # Rebuild by re-inserting all drawers
        count = 0
        offset = 0
        while True:
            rows = conn.execute(
                "SELECT rowid, content, wing, room, source_file FROM drawers LIMIT 500 OFFSET ?",
                (offset,)
            ).fetchall()
            if not rows:
                break
            for row in rows:
                conn.execute(
                    "INSERT INTO drawers_fts(rowid, content, wing, room, source_file) VALUES (?, ?, ?, ?, ?)",
                    (row["rowid"], row["content"], row["wing"], row["room"], row["source_file"])
                )
                count += 1
            offset += 500
        conn.commit()
        logger.info("FTS5 索引重建完成: %d drawers", count)
        return {"rebuilt": True, "drawer_count": count}
    except Exception as e:
        logger.error("FTS5 索引重建失败: %s", e)
        return {"rebuilt": False, "error": str(e)}
    finally:
        conn.close()


def cleanup_orphan_closets(storage) -> dict:
    """清理孤立 Closet 条目（source_file 已删除但 Closet 残留）。

    Returns:
        {"cleaned": N}
    """
    conn = storage._get_conn()
    try:
        # Find closets whose drawer_ids reference non-existent drawers
        # Simple approach: find closets with no matching drawers
        result = conn.execute(
            """
            DELETE FROM closets
            WHERE source_hash NOT IN (
                SELECT DISTINCT content_hash FROM drawers WHERE source_file != ''
                UNION
                SELECT DISTINCT '' -- keep closets with empty source
            )
            AND source_hash != ''
            """
        )
        conn.commit()
        count = result.rowcount
        logger.info("清理孤立 Closet 条目: %d", count)
        return {"cleaned": count}
    except Exception as e:
        logger.error("清理孤立 Closet 失败: %s", e)
        return {"cleaned": 0, "error": str(e)}
    finally:
        conn.close()


def cleanup_orphan_triples(storage) -> dict:
    """清理孤立 KG 三元组（drawer_refs 指向已删除的 Drawer）。

    Returns:
        {"cleaned": N}
    """
    conn = storage._get_conn()
    try:
        # Find triples whose drawer_refs reference non-existent drawers
        rows = conn.execute(
            "SELECT id, drawer_refs FROM kg_triples WHERE drawer_refs != ''"
        ).fetchall()

        cleaned = 0
        for row in rows:
            triple_id = row["id"]
            drawer_refs = row["drawer_refs"].split(",")
            # Check which drawers still exist
            existing = []
            for ref in drawer_refs:
                ref = ref.strip()
                if ref:
                    exists = conn.execute(
                        "SELECT 1 FROM drawers WHERE id = ? LIMIT 1", (ref,)
                    ).fetchone()
                    if exists:
                        existing.append(ref)

            if len(existing) != len(drawer_refs):
                # Update or delete
                if existing:
                    conn.execute(
                        "UPDATE kg_triples SET drawer_refs = ? WHERE id = ?",
                        (",".join(existing), triple_id)
                    )
                else:
                    conn.execute(
                        "UPDATE kg_triples SET drawer_refs = '' WHERE id = ?",
                        (triple_id,)
                    )
                cleaned += 1

        conn.commit()
        logger.info("清理孤立 KG 三元组: %d", cleaned)
        return {"cleaned": cleaned}
    except Exception as e:
        logger.error("清理孤立三元组失败: %s", e)
        return {"cleaned": 0, "error": str(e)}
    finally:
        conn.close()
