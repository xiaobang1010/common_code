"""embedding provider 后台线程懒加载 + 补嵌测试。

核心验证点：
- 首次访问触发后台加载但不阻塞，并发只启动一个线程
- 加载完成自动生效（available 变 True、embed 返回真实向量）
- 加载失败：event 置位但 available 仍为 False，且不重试
- loading 是纯状态查询，不触发加载
- 加载窗口内写入的无向量记录在加载完成后被补嵌

所有用例用 threading.Event 同步加载完成时机，不用 sleep。
"""

from __future__ import annotations

import threading

import pytest

from memory.backfill import backfill_missing_embeddings
from memory.embedding.provider import JasperEmbeddingProvider
from memory.vector_db.store import _NO_EMBEDDING_KEY, ChromaStore


class FakeModel:
    """假 SentenceTransformer：单文本返回一个向量，批量返回向量列表。"""

    def encode(self, text, batch_size=None):
        if isinstance(text, list):
            return [[0.5] * 384 for _ in text]
        return [0.5] * 384


@pytest.fixture
def slow_provider(monkeypatch):
    """构造使用受控慢加载的 provider：finish.set() 才完成加载。"""
    finish = threading.Event()
    calls: list[int] = []

    def fake_load(self):
        calls.append(1)
        finish.wait(timeout=5)
        self._model = FakeModel()
        self._available = True

    monkeypatch.setattr(JasperEmbeddingProvider, "_load_model", fake_load)
    provider = JasperEmbeddingProvider()
    return provider, finish, calls


def _join_worker(provider: JasperEmbeddingProvider) -> bool:
    """等后台加载线程结束（成功返回 True），用例收尾用。"""
    return provider.wait_loaded(timeout=5)


# --- provider 主体 ---


def test_construct_does_not_load(slow_provider):
    provider, _, calls = slow_provider
    assert provider._available is False
    assert provider._load_started is False
    assert provider.loading is False
    assert calls == []


def test_loading_property_does_not_trigger_load(slow_provider):
    provider, finish, calls = slow_provider
    assert provider.loading is False
    assert calls == []
    finish.set()
    assert _join_worker(provider)


def test_first_access_returns_immediately_and_starts_load(slow_provider):
    provider, finish, calls = slow_provider
    # 不 set finish，加载线程会一直挂着：available 必须立即返回 False
    assert provider.available is False
    assert provider.loading is True
    assert calls == [1]
    finish.set()
    assert _join_worker(provider)


def test_concurrent_first_access_starts_single_thread(slow_provider):
    provider, finish, calls = slow_provider
    barrier = threading.Barrier(4)
    results: list[bool] = []

    def access():
        barrier.wait()
        results.append(provider.available)

    threads = [threading.Thread(target=access) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert calls == [1]  # 只启动一个加载线程
    assert all(r is False for r in results)  # 全部立即返回 False
    finish.set()
    assert _join_worker(provider)


def test_load_complete_auto_effective(slow_provider):
    provider, finish, _ = slow_provider
    assert provider.available is False
    finish.set()
    assert _join_worker(provider) is True
    assert provider.loading is False
    assert provider.available is True
    vec = provider.embed("你好")
    assert vec is not None and len(vec) == 384
    vecs = provider.embed_batch(["a", "b"])
    assert len(vecs) == 2 and all(v is not None for v in vecs)


def test_callback_fires_after_event_set(slow_provider):
    provider, finish, _ = slow_provider
    observed_available: list[bool] = []

    def callback():
        # 回调执行时 available 必须已对外为 True（补嵌期间写入自带向量）
        observed_available.append(provider.available)

    provider.add_loaded_callback(callback)
    finish.set()
    assert _join_worker(provider) is True
    assert observed_available == [True]


def test_callback_registered_after_load_runs_immediately(slow_provider):
    provider, finish, _ = slow_provider
    finish.set()
    assert _join_worker(provider) is True
    ran: list[int] = []

    def callback():
        ran.append(1)

    provider.add_loaded_callback(callback)
    assert ran == [1]


def test_load_failure_no_retry_and_loading_resets(monkeypatch):
    """加载失败：event 置位、loading 复位，available 仍 False，不重试。"""
    finish = threading.Event()
    calls: list[int] = []

    def fake_load(self):
        calls.append(1)
        finish.wait(timeout=5)
        # 不置 _available，模拟加载失败

    monkeypatch.setattr(JasperEmbeddingProvider, "_load_model", fake_load)
    provider = JasperEmbeddingProvider()

    assert provider.available is False
    assert provider.loading is True
    finish.set()
    assert provider.wait_loaded(timeout=5) is False
    assert provider.loading is False
    assert provider.available is False
    # 再次访问不重试
    assert provider.available is False
    assert calls == [1]


# --- 补嵌 ---


@pytest.fixture
def store(tmp_path):
    """临时目录的真实 ChromaStore。"""
    s = ChromaStore(db_path=tmp_path / "chroma")
    assert s.available
    return s


def test_upsert_none_uses_placeholder_mark(store):
    store.upsert_drawer("id1", "有向量", [0.1] * 384, {"wing": "w"})
    store.upsert_drawer("id2", "无向量", None, {"wing": "w"})

    missing = store.get_missing_embeddings()
    assert [m["id"] for m in missing] == ["id2"]
    assert missing[0]["metadata"][_NO_EMBEDDING_KEY] is True

    # 无向量占位记录不被向量检索召回
    hits = store.query_drawers([0.1] * 384, n_results=5)
    assert [h["id"] for h in hits] == ["id1"]


def test_legacy_records_without_mark_still_retrievable(store):
    """回归：改造前写入的存量记录 metadata 没有 _no_embedding 键，
    不能被 where 误伤（$ne: True 同时命中缺键旧记录与有向量新记录）。"""
    col = store._collection
    # 直接绕过 store 层模拟存量数据：metadata 无 _no_embedding 键
    col.upsert(
        ids=["legacy"],
        documents=["存量记录"],
        embeddings=[[0.1] * 384],
        metadatas=[{"wing": "w"}],
    )
    store.upsert_drawer("placeholder", "占位", None, {"wing": "w"})
    store.upsert_drawer("new_ok", "新有向量", [0.2] * 384, {"wing": "w"})

    hits = store.query_drawers([0.1] * 384, n_results=5)
    assert {h["id"] for h in hits} == {"legacy", "new_ok"}

    # 带业务 where 的嵌套 $and 场景同样不误伤
    hits = store.query_drawers([0.1] * 384, n_results=5, where={"wing": "w"})
    assert {h["id"] for h in hits} == {"legacy", "new_ok"}


def test_backfill_restores_vectors(store, slow_provider):
    provider, finish, _ = slow_provider
    store.upsert_drawer("id1", "有向量", [0.1] * 384, {"wing": "w"})
    store.upsert_drawer("id2", "无向量", None, {"wing": "w"})

    finish.set()
    assert provider.wait_loaded(timeout=5) is True
    assert provider.available is True

    n = backfill_missing_embeddings(store, provider)
    assert n == 1
    assert store.get_missing_embeddings() == []
    # 补嵌后标记为 False 且可被召回
    hits = store.query_drawers([0.5] * 384, n_results=5)
    assert {h["id"] for h in hits} == {"id1", "id2"}


def test_backfill_empty_is_noop(store, slow_provider):
    provider, finish, _ = slow_provider
    finish.set()
    assert provider.wait_loaded(timeout=5) is True
    assert backfill_missing_embeddings(store, provider) == 0


def test_backfill_batches_in_loop(store, slow_provider):
    """缺失记录超过单批上限时循环补完。"""
    provider, finish, _ = slow_provider
    for i in range(5):
        store.upsert_drawer(f"id{i}", f"无向量{i}", None, {"wing": "w"})

    finish.set()
    assert provider.wait_loaded(timeout=5) is True

    n = backfill_missing_embeddings(store, provider, batch_limit=2, total_limit=10)
    assert n == 5
    assert store.get_missing_embeddings() == []


def test_backfill_skips_when_provider_unavailable(store, slow_provider):
    provider, finish, _ = slow_provider
    store.upsert_drawer("id2", "无向量", None, {"wing": "w"})

    # 模型未加载完成：backfill 直接跳过（正常不会走到，防御性）
    assert provider.available is False
    assert backfill_missing_embeddings(store, provider) == 0
    assert len(store.get_missing_embeddings()) == 1
    finish.set()
    assert _join_worker(provider)


def test_palace_auto_backfills_window_writes(store, slow_provider):
    """端到端：PalaceManager 注册回调，加载窗口内写入在加载完成后被补嵌。"""
    from memory.palace.manager import PalaceManager

    provider, finish, _ = slow_provider
    palace = PalaceManager(chroma_store=store, embedding_provider=provider)
    # 在 palace 之后注册完成哨兵：回调按注册顺序执行，哨兵触发时补嵌已完成
    backfill_done = threading.Event()
    provider.add_loaded_callback(backfill_done.set)

    # 模型未加载：写入走无向量路径（零向量占位 + 标记）
    palace.add_drawer("wing", "room", "窗口内写入的内容", importance=0.5)
    assert len(store.get_missing_embeddings()) == 1

    # 加载完成 -> 回调自动补嵌（等哨兵确认补嵌执行完毕，避免断言竞态）
    finish.set()
    assert provider.wait_loaded(timeout=5) is True
    assert backfill_done.wait(timeout=5) is True
    assert store.get_missing_embeddings() == []

    # 补嵌后记录可被向量召回
    hits = store.query_drawers([0.5] * 384, n_results=5)
    assert len(hits) == 1
