"""Jasper embedding 提供器。

使用 infgrad/Jasper-Token-Compression-600M 模型。
模型在安装时（或首次启动时）通过 download.py 下载到本目录下，
JasperEmbeddingProvider 只负责加载已下载的模型，不触发下载。
"""

from __future__ import annotations

import logging
import struct
import threading
from pathlib import Path
from typing import Callable

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
    2. 首次使用时才用 sentence-transformers 加载模型（懒加载，避免拖慢启动）
    3. 输出 2048 维原始向量 -> Matryoshka 截断到 384 维 -> L2 归一化
    """

    def __init__(self) -> None:
        self._model = None
        self._available = False
        # 是否已启动加载线程：启动后不再重复启动，加载失败也不重试（持久降级）
        self._load_started = False
        # 加载完成事件：成功失败都会置位。承载 happens-before（读方通过
        # is_set() 保证能看到后台线程对 _available 的写入），成功与否看 _available
        self._loaded_event = threading.Event()
        # 加载成功后的回调（如补嵌），加载线程遍历前对列表做快照
        self._on_loaded_callbacks: list[Callable[[], None]] = []
        # 加载状态锁：保证只启动一个加载线程，回调注册与启动互斥
        self._load_lock = threading.Lock()
        # 加载线程引用（测试用 join）
        self._load_thread: threading.Thread | None = None
        # 卸载请求标志：unload() 置位后由 worker 检查点或 unload 最终判定清除
        self._unload_requested = False
        # worker 是否已进入回调区：unload 据此判断"回调执行中"分支
        self._callbacks_active = False

    def _ensure_loading(self) -> None:
        """首次访问时启动后台加载线程，立即返回不阻塞。"""
        if self._load_started or self._loaded_event.is_set():
            return
        with self._load_lock:
            if self._load_started or self._loaded_event.is_set():
                return
            self._load_started = True
            self._load_thread = threading.Thread(
                target=self._load_worker,
                name="jasper-embedding-loader",
                daemon=True,
            )
            self._load_thread.start()

    def _load_worker(self) -> None:
        """后台加载线程体。

        顺序是关键：加载成功置 _available=True -> finally 中置 _loaded_event
        （对外宣告"加载结束"）-> event 置位后、成功的前提下才触发 on_loaded
        回调。这样回调（补嵌）执行期间 available 已对外为 True，新写入自带
        向量，不会产生新的无向量记录。

        卸载协作：finally 内置 event 后执行检查点 1（触发回调前）、回调循环
        的 finally 内执行检查点 2（回调结束后），两处都持锁检查卸载请求；
        置位则自清状态（跳过回调）并直接返回——无论 unload 何时置位、线程
        因何路径退出，线程退出前状态必被清理，无标志滞留。
        """
        success = False
        try:
            self._load_model()
            success = self._available
        except Exception as e:
            # 兜底：加载线程内的未预期异常（如测试注入），记录后按失败处理
            logger.warning("Jasper 模型加载线程异常: %s", e)
            success = False
        finally:
            # 置 event 宣告"加载结束"（成功失败都会置位，任何退出路径都保证）
            self._loaded_event.set()
        # 检查点 1：event 置位后、触发回调前，持锁检查卸载请求
        with self._load_lock:
            if self._unload_requested:
                self._reset_after_unload()
                return
            # 仅加载成功才进入回调区（_callbacks_active 供 unload 判断）
            self._callbacks_active = success
            callbacks = list(self._on_loaded_callbacks) if success else []
        try:
            if success:
                for callback in callbacks:
                    try:
                        callback()
                    except Exception as e:
                        logger.warning("on_loaded 回调执行失败: %s", e)
        finally:
            # 检查点 2：回调结束后持锁检查卸载请求（覆盖回调期间 unload 的窗口）
            with self._load_lock:
                self._callbacks_active = False
                if self._unload_requested:
                    self._reset_after_unload()

    def _reset_after_unload(self) -> None:
        """卸载自清：释放模型并重置加载状态，恢复可加载。

        与 unload() 最终判定共用同一字段集合，幂等。仅在持 _load_lock 时调用。
        """
        self._model = None
        self._available = False
        self._unload_requested = False
        self._load_started = False
        self._loaded_event = threading.Event()
        self._callbacks_active = False
        self._load_thread = None

    def unload(self) -> None:
        """释放已加载模型并重置状态，使后续可重新加载。

        三段锁纪律：① 持锁写入卸载标志并完成分支判定；② 分支 4 释放锁后
        join 加载线程（等待期间 worker 可正常取锁执行检查点，不会死锁）；
        ③ 重新持锁做最终判定与重置（与 _ensure_loading 的锁内启动互斥，
        不会出现重置 _load_started 与启动新线程交错导致双加载线程）。

        分支说明：
        1. 从未加载：仅清卸载标志，无其他副作用
        2. 加载进行中（event 未置位）：不等待，worker 检查点 1 必执行自清
        3. 回调执行中（_callbacks_active）：不等待，worker 检查点 2 自清
        4. 加载已结束且回调未开始/已结束、线程存活：锁外 join 后最终判定
        5. 线程已退出：直接最终判定
        """
        with self._load_lock:
            self._unload_requested = True
            self._available = False
            self._model = None
            thread = self._load_thread
            if thread is None:
                # 分支 1：从未加载
                self._unload_requested = False
                return
            if not self._loaded_event.is_set():
                # 分支 2：加载进行中，不等待
                if not thread.is_alive():
                    # 兜底：线程已死，直接最终判定（正常路径下检查点 1 会自清）
                    self._reset_after_unload()
                return
            if self._callbacks_active:
                # 分支 3：回调执行中，不等待
                if not thread.is_alive():
                    # 兜底：线程已死，直接最终判定（正常路径下检查点 2 会自清）
                    self._reset_after_unload()
                return
            # 分支 4/5：加载已结束且回调未在跑
            if not thread.is_alive():
                # 分支 5：线程已退出
                self._reset_after_unload()
                return
            join_thread = thread
        # ② 释放锁后 join（worker 已过检查点 2 则即将退出，毫秒级返回）
        join_thread.join()
        # ③ 重新持锁最终判定：标志仍置位才重置（worker 自清过则跳过）
        with self._load_lock:
            if self._unload_requested:
                self._reset_after_unload()

    def status_snapshot(self) -> tuple[bool, bool]:
        """只读状态快照 (loading, available)。不触发加载。

        与 available/loading 属性语义一致，但不会调用 _ensure_loading——
        供 feature API 等外部纯查询使用，避免关闭状态下轮询重新触发模型加载。
        """
        return (
            self._load_started and not self._loaded_event.is_set(),
            self._loaded_event.is_set() and self._available,
        )

    def add_loaded_callback(self, callback: Callable[[], None]) -> None:
        """注册加载完成回调。

        若模型已加载完成（event 已置位）则立即同步调用；否则追加进列表，
        由加载线程在成功后调用。加载线程遍历前对列表做快照，与注册无竞态。
        """
        if self._loaded_event.is_set():
            callback()
            return
        with self._load_lock:
            if self._loaded_event.is_set():
                callback()
                return
            self._on_loaded_callbacks.append(callback)

    def wait_loaded(self, timeout: float | None = None) -> bool:
        """等待模型加载完成（成功或失败），返回是否成功加载。

        供测试同步与外部等待模型就绪：加载中触发加载，完成后返回结果。
        """
        self._ensure_loading()
        self._loaded_event.wait(timeout)
        return self._available

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
        """嵌入是否可用（首次访问触发后台加载）。

        event 只保证可见性（happens-before），成功与否看 _available：
        加载失败时 event 置位但 _available 为 False，对外仍为不可用。
        """
        self._ensure_loading()
        return self._loaded_event.is_set() and self._available

    @property
    def loading(self) -> bool:
        """是否正在加载中。纯状态查询，不触发加载。"""
        return self._load_started and not self._loaded_event.is_set()

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
        self._ensure_loading()
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
        self._ensure_loading()
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
