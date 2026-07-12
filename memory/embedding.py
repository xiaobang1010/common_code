"""向量嵌入模型 - 支持 embedding API 和本地模型。

优先级：配置的 embedding API > sentence-transformers 本地模型 > None（降级纯 BM25）
"""

from __future__ import annotations

import logging
import struct
from typing import Any

logger = logging.getLogger(__name__)


class EmbeddingProvider:
    """向量嵌入提供器。
    
    支持两种模式：
    1. OpenAI compatible embedding API（通过项目 LLM 配置的 base_url + api_key）
    2. sentence-transformers 本地模型（all-MiniLM-L6-v2，可选依赖）
    
    模型选择优先级：API > 本地 > None（降级）
    """
    
    def __init__(self, config: dict | None = None):
        self._mode: str | None = None  # "api" | "local" | None
        self._model_name: str = ""
        self._dimension: int = 0
        self._client: Any = None
        self._local_model: Any = None
        self._cache: dict[str, list[float]] = {}  # in-memory cache
        
        config = config or {}
        self._init_provider(config)
    
    def _init_provider(self, config: dict) -> None:
        """初始化嵌入提供器，按优先级尝试。"""
        # Try API mode first
        api_key = config.get("api_key") or config.get("llm_api_key")
        base_url = config.get("base_url") or config.get("llm_base_url")
        model = config.get("embedding_model", "text-embedding-3-small")
        
        if api_key and base_url:
            try:
                import httpx
                self._client = httpx.Client(timeout=30.0)
                self._mode = "api"
                self._model_name = model
                # Determine dimension by making a test embedding
                test = self.embed("test")
                if test:
                    self._dimension = len(test)
                    logger.info("Embedding API 模式: model=%s, dim=%d", model, self._dimension)
                    return
            except Exception as e:
                logger.warning("Embedding API 初始化失败: %s", e)
        
        # Try local mode
        try:
            from sentence_transformers import SentenceTransformer
            self._local_model = SentenceTransformer("all-MiniLM-L6-v2")
            self._mode = "local"
            self._model_name = "all-MiniLM-L6-v2"
            test = self.embed("test")
            if test:
                self._dimension = len(test)
                logger.info("Embedding 本地模式: model=%s, dim=%d", self._model_name, self._dimension)
                return
        except ImportError:
            logger.info("sentence-transformers 未安装，跳过本地嵌入模式")
        except Exception as e:
            logger.warning("本地嵌入模型初始化失败: %s", e)
        
        # Fallback: no embedding
        self._mode = None
        logger.info("Embedding 不可用，降级为纯 BM25 模式")
    
    @property
    def available(self) -> bool:
        """嵌入是否可用。"""
        return self._mode is not None
    
    @property
    def model_name(self) -> str:
        """当前模型名。"""
        return self._model_name
    
    @property
    def dimension(self) -> int:
        """向量维度。"""
        return self._dimension
    
    def embed(self, text: str) -> list[float] | None:
        """单文本嵌入。
        
        Returns:
            向量列表，不可用时返回 None
        """
        if not self.available or not text:
            return None
        
        # Check in-memory cache
        cache_key = f"{self._model_name}:{hash(text)}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        result = None
        
        if self._mode == "api":
            result = self._embed_api(text)
        elif self._mode == "local":
            result = self._embed_local(text)
        
        if result:
            # Cache (limit size)
            if len(self._cache) < 1000:
                self._cache[cache_key] = result
        
        return result
    
    def embed_batch(self, texts: list[str]) -> list[list[float] | None]:
        """批量嵌入。
        
        Returns:
            向量列表，不可用的条目为 None
        """
        if not self.available:
            return [None] * len(texts)
        
        if self._mode == "api":
            return self._embed_batch_api(texts)
        elif self._mode == "local":
            return self._embed_batch_local(texts)
        return [None] * len(texts)
    
    def _embed_api(self, text: str) -> list[float] | None:
        """通过 embedding API 嵌入。"""
        try:
            # Get config from global config
            from startup.utils.config import get_global_config
            config = get_global_config()
            api_key = config.llm_api_key
            base_url = config.llm_base_url or "https://api.openai.com/v1"
            
            if not api_key:
                return None
            
            response = self._client.post(
                f"{base_url}/embeddings",
                json={
                    "model": self._model_name,
                    "input": text,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            return data["data"][0]["embedding"]
        except Exception as e:
            logger.warning("API 嵌入失败: %s", e)
            return None
    
    def _embed_batch_api(self, texts: list[str]) -> list[list[float] | None]:
        """批量 API 嵌入。"""
        try:
            from startup.utils.config import get_global_config
            config = get_global_config()
            api_key = config.llm_api_key
            base_url = config.llm_base_url or "https://api.openai.com/v1"
            
            if not api_key:
                return [None] * len(texts)
            
            response = self._client.post(
                f"{base_url}/embeddings",
                json={
                    "model": self._model_name,
                    "input": texts,
                },
                headers={"Authorization": f"Bearer {api_key}"},
            )
            response.raise_for_status()
            data = response.json()
            # Sort by index to maintain order
            embeddings = sorted(data["data"], key=lambda x: x["index"])
            return [e["embedding"] for e in embeddings]
        except Exception as e:
            logger.warning("批量 API 嵌入失败: %s", e)
            return [None] * len(texts)
    
    def _embed_local(self, text: str) -> list[float] | None:
        """通过本地模型嵌入。"""
        try:
            vec = self._local_model.encode(text)
            return vec.tolist()
        except Exception as e:
            logger.warning("本地嵌入失败: %s", e)
            return None
    
    def _embed_batch_local(self, texts: list[str]) -> list[list[float] | None]:
        """批量本地嵌入。"""
        try:
            vecs = self._local_model.encode(texts)
            return [v.tolist() for v in vecs]
        except Exception as e:
            logger.warning("批量本地嵌入失败: %s", e)
            return [None] * len(texts)


# Vector serialization helpers
def vector_to_bytes(vector: list[float]) -> bytes:
    """将向量序列化为 bytes（float32 little-endian）。"""
    return struct.pack(f'{len(vector)}f', *vector)

def bytes_to_vector(data: bytes) -> list[float]:
    """将 bytes 反序列化为向量。"""
    count = len(data) // 4
    return list(struct.unpack(f'{count}f', data))
