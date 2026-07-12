"""Jasper embedding 提供器。

使用 infgrad/Jasper-Token-Compression-600M 模型。
模型在安装时（或首次启动时）通过 download.py 下载到本目录下，
JasperEmbeddingProvider 只负责加载已下载的模型，不触发下载。
"""

from __future__ import annotations

import logging
import struct
from pathlib import Path

logger = logging.getLogger(__name__)

# 模型名和参数
_JASPER_MODEL_NAME = "infgrad/Jasper-Token-Compression-600M"
# Jasper 原始输出 2048 维，通过 Matryoshka 截断到 384 维
_TARGET_DIMENSION = 384
# 原始向量维度
_FULL_DIMENSION = 2048
# 模型缓存目录（memory/embedding/）
_CACHE_DIR = Path(__file__).parent


class JasperEmbeddingProvider:
    """Jasper embedding 提供器。

    使用 infgrad/Jasper-Token-Compression-600M 模型。
    模型需预先通过 download_model() 或 CLI 命令 download-embedding-model 下载。
    如果模型未下载，available 返回 False，降级为纯 BM25 模式。

    处理流程：
    1. 检查模型是否已下载（不触发下载）
    2. 用 sentence-transformers 加载已下载的模型
    3. 输出 2048 维原始向量 -> Matryoshka 截断到 384 维 -> L2 归一化
    """

    def __init__(self) -> None:
        self._model = None
        self._available = False
        self._load_model()

    def _load_model(self) -> None:
        """加载已下载的 Jasper 模型。不触发下载。"""
        # 先检查模型是否已下载
        from memory.embedding.download import is_model_downloaded, _LOCAL_MODEL_DIR

        if not is_model_downloaded():
            logger.warning(
                "Jasper 模型未下载，embedding 不可用。"
                "请运行 download-embedding-model 命令下载模型。"
            )
            return

        try:
            from sentence_transformers import SentenceTransformer
        except ImportError:
            logger.warning("sentence-transformers 未安装，embedding 不可用")
            return

        try:
            # 从本地目录加载（git clone 下载的完整仓库，含 custom_st.py 等自定义模块）
            self._model = SentenceTransformer(
                str(_LOCAL_MODEL_DIR),
                device="cpu",
                trust_remote_code=True,
            )
            self._available = True
            logger.info(
                "Jasper 模型加载成功: model=%s, dim=%d->%d",
                _JASPER_MODEL_NAME,
                _FULL_DIMENSION,
                _TARGET_DIMENSION,
            )
        except Exception as e:
            logger.warning("Jasper 模型加载失败: %s", e)
            self._model = None
            self._available = False

    @property
    def available(self) -> bool:
        """嵌入是否可用。"""
        return self._available

    @property
    def model_name(self) -> str:
        """当前模型名。"""
        return _JASPER_MODEL_NAME

    @property
    def dimension(self) -> int:
        """向量维度（截断后）。"""
        return _TARGET_DIMENSION

    def _post_process(self, raw_vec) -> list[float] | None:
        """对原始向量做 Matryoshka 截断和 L2 归一化。"""
        try:
            import numpy as np

            vec = np.asarray(raw_vec)
            # Matryoshka 截断：取前 384 维
            truncated = vec[:_TARGET_DIMENSION]
            # L2 归一化
            norm = np.linalg.norm(truncated)
            if norm > 0:
                truncated = truncated / norm
            return truncated.tolist()
        except Exception as e:
            logger.warning("向量后处理失败: %s", e)
            return None

    def embed(self, text: str) -> list[float] | None:
        """单文本嵌入。

        Returns:
            384 维归一化向量列表，不可用时返回 None
        """
        if not self._available or not text:
            return None

        try:
            raw_vec = self._model.encode(text)
            return self._post_process(raw_vec)
        except Exception as e:
            logger.warning("嵌入失败: %s", e)
            return None

    def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        """批量嵌入。

        Returns:
            向量列表，不可用的条目为 None
        """
        if not self._available:
            return [None] * len(texts)

        if not texts:
            return []

        try:
            raw_vecs = self._model.encode(texts, batch_size=32)
            results: list[list[float] | None] = []
            for raw_vec in raw_vecs:
                results.append(self._post_process(raw_vec))
            return results
        except Exception as e:
            logger.warning("批量嵌入失败: %s", e)
            return [None] * len(texts)


def vector_to_bytes(vector: list[float]) -> bytes:
    """将向量序列化为 bytes（float32 little-endian）。"""
    return struct.pack(f'{len(vector)}f', *vector)


def bytes_to_vector(data: bytes) -> list[float]:
    """将 bytes 反序列化为向量。"""
    count = len(data) // 4
    return list(struct.unpack(f'{count}f', data))
