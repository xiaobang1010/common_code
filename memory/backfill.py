"""记忆补嵌模块。

模型加载完成后，对加载窗口内以无向量状态（_no_embedding 标记）写入的记录
执行补嵌，恢复其向量召回能力。循环扫描直到清空或达到总处理上限。
"""

from __future__ import annotations

import logging
from typing import Any

from memory.vector_db.store import _NO_EMBEDDING_KEY

logger = logging.getLogger(__name__)

# 单批扫描上限
_DEFAULT_BATCH_LIMIT = 5000
# 单次补嵌总处理上限（防止异常情况下无限循环）
_DEFAULT_TOTAL_LIMIT = 50000


def backfill_missing_embeddings(
    store: Any,
    provider: Any,
    batch_limit: int = _DEFAULT_BATCH_LIMIT,
    total_limit: int = _DEFAULT_TOTAL_LIMIT,
) -> int:
    """对存储中无向量记录执行补嵌。

    循环扫描：每轮取一批无向量记录 -> 批量重嵌 -> 过滤失败项 -> 批量 update
    并清除标记；直到无缺失记录、单批不满或达到总处理上限。

    Args:
        store: ChromaStore 实例
        provider: JasperEmbeddingProvider 实例（须已加载完成）
        batch_limit: 单批扫描上限
        total_limit: 单次调用总处理上限

    Returns:
        成功补嵌的条数
    """
    # 模型不可用时跳过（正常不会走到：回调只在加载成功后触发）
    if provider is None or not provider.available:
        logger.info("补嵌跳过：embedding 不可用")
        return 0

    total_done = 0
    while total_done < total_limit:
        batch = store.get_missing_embeddings(limit=batch_limit)
        if not batch:
            # 没有待补记录，正常结束
            break

        vectors = provider.embed_batch([item["content"] for item in batch])

        # 过滤嵌入失败的项，把 _no_embedding 标记改为 False
        # （chromadb update 对 metadata 是合并语义，必须显式传键才能覆盖）
        ids: list[str] = []
        embeddings: list[list[float]] = []
        metadatas: list[dict] = []
        for item, vec in zip(batch, vectors):
            if vec is None:
                continue
            meta = dict(item["metadata"])
            meta[_NO_EMBEDDING_KEY] = False
            ids.append(item["id"])
            embeddings.append(vec)
            metadatas.append(meta)

        if ids and not store.update_embeddings(ids, embeddings, metadatas):
            logger.warning("补嵌 update 失败，中断本轮补嵌")
            break
        total_done += len(ids)

        if len(ids) < len(batch):
            # 本批有嵌入失败的项，继续只会拿到同一批，退出避免死循环
            logger.warning(
                "补嵌本批 %d 条中 %d 条嵌入失败，中止补嵌",
                len(batch),
                len(batch) - len(ids),
            )
            break
        if len(batch) < batch_limit:
            # 本批不满说明已扫完全部待补记录
            break

    if total_done >= total_limit:
        logger.warning("补嵌达到总处理上限 %d，可能存在大量无向量记录", total_limit)
    logger.info("补嵌完成: 共 %d 条", total_done)
    return total_done
