"""记忆功能开关（memory-feature-switch）测试。

覆盖：
- 默认关闭不加载 / 开关开启时启动加载（server.__main__ 启动路径）
- 开启后后台异步加载（feature API + start_loading 透传）
- unload 生命周期：已加载 / 从未加载 / 加载失败 / 加载中（检查点 1 前）/
  回调中（检查点 2 前）/ 加载结束任意时机 / worker 异常提前退出
- 持久化：POST 后 GET 立即一致、config.json 落盘、重启读取一致
- load_memory_plugins 幂等（重复调用不重建实例）
- GET 纯查询不触发加载、status_snapshot 四态正确
- enabled=True 无后端、第三方后端（无透传方法）兜底
- 关闭→开启循环（真实插件加载）无 ChromaStore 锁/资源异常

所有并发用例用 threading.Event 同步时机，不用 sleep。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

import startup.config as config_module
from memory.embedding.provider import JasperEmbeddingProvider
from query.services.memory.registry import get_registry, load_memory_plugins

# ---------------------------------------------------------------------------
# 公共工具
# ---------------------------------------------------------------------------


class FakeModel:
    """假 SentenceTransformer：单文本返回一个向量，批量返回向量列表。"""

    def encode(self, text, batch_size=None):
        if isinstance(text, list):
            return [[0.5] * 384 for _ in text]
        return [0.5] * 384


class FakeMemoryBackend:
    """无真实依赖的假记忆后端：可观察 start_loading/unload/embedding_status。"""

    def __init__(self) -> None:
        self.start_calls = 0
        self.loaded = threading.Event()
        self.unload_calls = 0

    def start_loading(self) -> None:
        self.start_calls += 1
        threading.Thread(target=self._load_worker, daemon=True).start()

    def _load_worker(self) -> None:
        time.sleep(0.05)
        self.loaded.set()

    def embedding_status(self) -> dict:
        return {
            "loading": self.start_calls > 0 and not self.loaded.is_set(),
            "available": self.loaded.is_set(),
        }

    def unload(self) -> None:
        self.unload_calls += 1
        self.loaded.clear()
        self.start_calls = 0


class MinimalMemoryBackend:
    """第三方最小后端：只实现协议方法，无透传方法（hasattr 兜底验证用）。"""

    async def store(self, session_id: str, key: str, content: str) -> None:
        pass

    async def retrieve(self, session_id: str, key: str) -> str | None:
        return None

    async def search(self, query: str, limit: int = 5) -> list[dict]:
        return []

    async def clear(self, session_id: str) -> None:
        pass


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """隔离配置：临时 HOME + 重置配置系统缓存 + 初始化。

    每个测试独立读写自己 HOME 下的 config.json，不污染真实配置。
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    config_module._config_reading_allowed = False
    config_module._global_config_cache = None
    config_module.enable_configs()
    yield tmp_path
    config_module._config_reading_allowed = False
    config_module._global_config_cache = None


@pytest.fixture
def clean_registry():
    """清空注册表，测试后恢复。"""
    registry = get_registry()
    saved_providers = dict(registry._providers)
    saved_active = registry._active
    for name in list(registry.list_providers()):
        registry.unregister(name)
    yield registry
    registry._providers.clear()
    registry._providers.update(saved_providers)
    registry._active = saved_active


@pytest.fixture
def controlled_provider(monkeypatch):
    """构造使用受控慢加载的 provider：gate.set() 才完成加载。"""
    gate = threading.Event()
    calls: list[int] = []

    def fake_load(self):
        calls.append(1)
        gate.wait(timeout=5)
        self._model = FakeModel()
        self._available = True

    monkeypatch.setattr(JasperEmbeddingProvider, "_load_model", fake_load)
    provider = JasperEmbeddingProvider()
    return provider, gate, calls


def _join_worker(provider: JasperEmbeddingProvider, timeout: float = 5) -> bool:
    """等后台加载线程结束（成功返回 True）。"""
    return provider.wait_loaded(timeout=timeout)


def _make_client():
    """构造 TestClient（路由已注册，配置在 isolated_config 中初始化）。"""
    from fastapi.testclient import TestClient
    from server.app import app

    return TestClient(app)


def _set_memory_enabled(enabled: bool) -> None:
    """直接改全局配置（等价于 POST 的持久化部分）。"""
    config = config_module.get_global_config()
    config.memory_enabled = enabled
    config_module.save_global_config(config)


# ---------------------------------------------------------------------------
# 启动路径：默认关闭 / 开关开启
# ---------------------------------------------------------------------------


def test_startup_skips_load_when_disabled(isolated_config, clean_registry, monkeypatch):
    """默认关闭：启动路径不调用 load_memory_plugins，注册表为空。"""
    from server.__main__ import load_memory_plugins_if_enabled

    called: list[int] = []
    # load_memory_plugins_if_enabled 内部 from-import，monkeypatch 目标模块属性
    monkeypatch.setattr(
        "query.services.memory.registry.load_memory_plugins",
        lambda: called.append(1),
    )

    result = load_memory_plugins_if_enabled()

    assert result is False
    assert called == []
    assert clean_registry.list_providers() == []


def test_startup_loads_when_enabled(isolated_config, clean_registry, monkeypatch):
    """开关开启时启动：正常调用 load_memory_plugins 并注册后端。"""
    from server.__main__ import load_memory_plugins_if_enabled

    _set_memory_enabled(True)

    def fake_load():
        clean_registry.register("fake-backend", MinimalMemoryBackend())

    monkeypatch.setattr("query.services.memory.registry.load_memory_plugins", fake_load)

    result = load_memory_plugins_if_enabled()

    assert result is True
    assert clean_registry.list_providers() == ["fake-backend"]


def test_restore_active_from_config(isolated_config, clean_registry):
    """启动恢复激活后端：config.memory.active 指向已注册后端时生效。"""
    b1, b2 = MinimalMemoryBackend(), MinimalMemoryBackend()
    clean_registry.register("a", b1)
    clean_registry.register("b", b2)
    config = config_module.get_global_config()
    config.memory = {"active": "b"}
    config_module.save_global_config(config)

    clean_registry.restore_active_after_load()

    assert clean_registry.get_active_name() == "b"


# ---------------------------------------------------------------------------
# feature API：开启后台加载 / GET 纯查询 / 持久化 / 兜底
# ---------------------------------------------------------------------------


def test_enable_triggers_background_loading(
    isolated_config, clean_registry, tmp_path
):
    """POST 开启：加载插件（幂等跳过假后端）+ start_loading 触发后台加载。"""
    backend = FakeMemoryBackend()
    clean_registry.register("memory-palace", backend)
    client = _make_client()

    r = client.post("/api/memory/feature", json={"enabled": True})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    # start_loading 已触发后台加载
    assert backend.start_calls == 1
    assert backend.loaded.wait(timeout=5)
    # 注册表未被真实插件重建（幂等跳过）
    assert clean_registry.get_active() is backend
    # GET 反映就绪
    r = client.get("/api/memory/feature")
    assert r.json() == {"enabled": True, "loading": False, "available": True}


def test_disable_unloads_then_unregisters(isolated_config, clean_registry, tmp_path):
    """POST 关闭：先 unload 释放再注销后端。"""
    backend = FakeMemoryBackend()
    backend.loaded.set()  # 模拟已加载
    clean_registry.register("memory-palace", backend)
    client = _make_client()
    _set_memory_enabled(True)

    r = client.post("/api/memory/feature", json={"enabled": False})
    assert r.status_code == 200
    assert r.json() == {"ok": True}

    assert backend.unload_calls == 1
    assert clean_registry.list_providers() == []
    r = client.get("/api/memory/feature")
    assert r.json() == {"enabled": False, "loading": False, "available": False}


def test_get_feature_pure_query_when_disabled(isolated_config, clean_registry, tmp_path):
    """GET 纯查询：关闭状态下短路，不触碰 provider（不触发加载）。"""
    backend = FakeMemoryBackend()
    clean_registry.register("memory-palace", backend)
    client = _make_client()

    r = client.get("/api/memory/feature")
    assert r.status_code == 200
    assert r.json() == {"enabled": False, "loading": False, "available": False}
    assert backend.start_calls == 0


def test_get_feature_enabled_no_backend(isolated_config, clean_registry, tmp_path):
    """enabled=True 但无注册后端：返回 {True, False, False}，不报 500。"""
    _set_memory_enabled(True)
    client = _make_client()

    r = client.get("/api/memory/feature")
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "loading": False, "available": False}


def test_third_party_backend_fallback(isolated_config, clean_registry, tmp_path):
    """第三方后端（无 embedding_status/start_loading/unload）：GET/POST 兜底。"""
    backend = MinimalMemoryBackend()
    # 注册为 memory-palace 名：load_memory_plugins 幂等跳过，避免真实插件混入
    clean_registry.register("memory-palace", backend)
    _set_memory_enabled(True)
    client = _make_client()

    # GET：无 embedding_status → 兜底返回，不 500
    r = client.get("/api/memory/feature")
    assert r.status_code == 200
    assert r.json() == {"enabled": True, "loading": False, "available": False}

    # POST 开启：无 start_loading → 跳过触发，不 500，后端仍注册
    r = client.post("/api/memory/feature", json={"enabled": True})
    assert r.status_code == 200
    assert clean_registry.list_providers() == ["memory-palace"]

    # POST 关闭：无 unload → 跳过释放，仅注销
    r = client.post("/api/memory/feature", json={"enabled": False})
    assert r.status_code == 200
    assert clean_registry.list_providers() == []


def test_persist_roundtrip_immediate_and_restart(isolated_config, tmp_path):
    """持久化：POST 后 GET 立即一致；config.json 落盘；重启读取一致。"""
    client = _make_client()

    client.post("/api/memory/feature", json={"enabled": True})
    # 立即一致（save_global_config 同步缓存）
    assert client.get("/api/memory/feature").json()["enabled"] is True
    # 落盘：True 保留
    config_path = tmp_path / ".agent" / "config.json"
    data = json.loads(config_path.read_text(encoding="utf-8"))
    assert data.get("memoryEnabled") is True

    # 重启读取一致（缓存重置后从磁盘恢复）
    config_module._global_config_cache = None
    assert config_module.get_global_config().memory_enabled is True

    # False 缺省兜底：写回 False 后字段被默认值过滤，读回仍为 False
    client.post("/api/memory/feature", json={"enabled": False})
    config_module._global_config_cache = None
    assert config_module.get_global_config().memory_enabled is False


# ---------------------------------------------------------------------------
# load_memory_plugins 幂等
# ---------------------------------------------------------------------------


def test_load_memory_plugins_idempotent(isolated_config, clean_registry):
    """重复调用不重建实例（同名后端跳过工厂，实例引用不变）。"""
    load_memory_plugins()
    names = clean_registry.list_providers()
    assert "memory-palace" in names
    first = clean_registry.get_active()

    load_memory_plugins()
    second = clean_registry.get_active()

    assert second is first  # 同一实例，未被重建
    # 清理真实插件实例（避免跨测试残留）
    for name in list(clean_registry.list_providers()):
        clean_registry.unregister(name)


def test_toggle_loop_with_real_plugin_no_chroma_lock_error(
    isolated_config, clean_registry, tmp_path
):
    """关闭→开启循环（真实插件加载）：无 ChromaStore 锁/资源异常。"""
    client = _make_client()
    for _ in range(3):
        r = client.post("/api/memory/feature", json={"enabled": True})
        assert r.status_code == 200
        r = client.post("/api/memory/feature", json={"enabled": False})
        assert r.status_code == 200
    assert clean_registry.list_providers() == []


# ---------------------------------------------------------------------------
# unload 生命周期（provider 层）
# ---------------------------------------------------------------------------


def test_unload_after_loaded_reloadable(controlled_provider):
    """已加载 unload：释放后状态复位，再次触发可重载。"""
    provider, gate, _ = controlled_provider
    gate.set()
    assert _join_worker(provider) is True
    assert provider.status_snapshot() == (False, True)

    provider.unload()
    assert provider.status_snapshot() == (False, False)
    assert provider._available is False
    assert provider._model is None

    # 再次触发可重载（gate 已 set，秒过）
    assert _join_worker(provider) is True
    assert provider.status_snapshot() == (False, True)


def test_unload_never_loaded_noop(controlled_provider):
    """从未加载 unload：无副作用，后续可正常加载。"""
    provider, gate, _ = controlled_provider
    assert provider.status_snapshot() == (False, False)

    provider.unload()
    assert provider.status_snapshot() == (False, False)
    assert provider._load_started is False

    gate.set()
    assert _join_worker(provider) is True


def test_unload_after_failure_resets_retry(monkeypatch):
    """加载失败（持久降级）unload：受控重置为可重试态，再次触发成功。"""
    provider = JasperEmbeddingProvider()

    def failing_load(self):
        raise RuntimeError("模型损坏")

    monkeypatch.setattr(JasperEmbeddingProvider, "_load_model", failing_load)
    assert provider.wait_loaded(timeout=5) is False
    assert provider.status_snapshot() == (False, False)
    assert provider._load_started is True  # 持久降级：不重试

    provider.unload()
    assert provider._load_started is False  # 受控重置可重试

    # 换成成功加载，再次触发成功
    monkeypatch.setattr(JasperEmbeddingProvider, "_load_model", lambda self: None)

    def good_load(self):
        self._model = FakeModel()
        self._available = True

    monkeypatch.setattr(JasperEmbeddingProvider, "_load_model", good_load)
    assert provider.wait_loaded(timeout=5) is True
    assert provider.status_snapshot() == (False, True)


def test_unload_during_load_before_checkpoint1(controlled_provider):
    """加载中 unload（检查点 1 前）：不触发补嵌回调、worker 自清、可重开。"""
    provider, gate, _ = controlled_provider
    callback_ran: list[int] = []
    provider.add_loaded_callback(lambda: callback_ran.append(1))

    # 触发加载，worker 挂在 gate.wait（检查点 1 之前）
    assert provider.available is False
    assert provider.loading is True

    provider.unload()
    gate.set()
    assert _join_worker(provider) is False  # 旧 worker 收尾，模型已被卸载

    assert callback_ran == []  # 不触发补嵌回调
    assert provider.status_snapshot() == (False, False)
    assert provider._load_started is False  # 状态自清

    # 重新加载成功
    assert _join_worker(provider) is True
    assert provider.status_snapshot() == (False, True)


def test_unload_during_callback_window(controlled_provider):
    """回调执行中 unload（检查点 1 后、检查点 2 前）：unload 快速返回，
    回调内 embed 降级无副作用，检查点 2 自清后可重载。"""
    provider, gate, _ = controlled_provider
    unload_done = threading.Event()
    callback_done = threading.Event()
    embed_results: list = []

    def callback():
        # 回调（补嵌）执行中：等主线程 unload 完成后再嵌入，验证降级
        unload_done.wait(timeout=5)
        embed_results.append(provider.embed("窗口内写入"))
        callback_done.set()

    provider.add_loaded_callback(callback)
    assert provider.available is False  # 触发后台加载
    gate.set()

    # 等回调开始执行（_callbacks_active 置位）
    for _ in range(200):
        if provider._callbacks_active:
            break
        time.sleep(0.005)
    assert provider._callbacks_active is True

    provider.unload()  # 回调中：不等待，快速返回
    assert provider.status_snapshot()[0] is False  # available 已为 False
    unload_done.set()
    assert _join_worker(provider) is False  # 模型已被卸载

    # 等回调执行完再断言（embed 在 unload 后执行）
    assert callback_done.wait(timeout=5)
    # 回调内 embed 返回 None（降级走 BM25），无副作用
    assert embed_results == [None]
    assert provider._callbacks_active is False
    assert provider._load_started is False  # 检查点 2 自清

    # 重新加载成功
    assert _join_worker(provider) is True


def test_unload_after_load_any_timing_clean(controlled_provider):
    """加载结束任意时机 unload：最终状态一致、无残留（覆盖检查点 2 后窗口）。"""
    provider, gate, _ = controlled_provider
    gate.set()

    for _ in range(20):
        # 交替时机：加载完成前/后立即 unload，模拟竞争窗口
        if _ % 2 == 0:
            assert _join_worker(provider) is True
        provider.unload()
        assert provider.status_snapshot() == (False, False)
        assert provider._unload_requested is False  # 无标志残留
        # 重新触发（保证下一轮有已加载状态）
        assert _join_worker(provider) is True


def test_worker_exception_self_clears(monkeypatch):
    """worker 异常提前退出：状态自清无滞留，unload 后重开可加载。"""
    provider = JasperEmbeddingProvider()
    callback_ran: list[int] = []
    provider.add_loaded_callback(lambda: callback_ran.append(1))

    def exploding_load(self):
        raise RuntimeError("加载线程内异常")

    monkeypatch.setattr(JasperEmbeddingProvider, "_load_model", exploding_load)
    assert provider.wait_loaded(timeout=5) is False

    assert callback_ran == []  # 异常路径不触发回调
    assert provider._callbacks_active is False  # 无标志滞留
    assert provider.status_snapshot() == (False, False)

    provider.unload()  # 分支 5：线程已退出，直接最终判定

    def good_load(self):
        self._model = FakeModel()
        self._available = True

    monkeypatch.setattr(JasperEmbeddingProvider, "_load_model", good_load)
    assert provider.wait_loaded(timeout=5) is True


# ---------------------------------------------------------------------------
# status_snapshot 四态
# ---------------------------------------------------------------------------


def test_status_snapshot_states(controlled_provider):
    """status_snapshot 四态：未触发 / 加载中 / 完成 / unload 后。"""
    provider, gate, _ = controlled_provider

    # 未触发：loading=False, available=False
    assert provider.status_snapshot() == (False, False)

    # 加载中：loading=True
    assert provider.available is False
    assert provider.status_snapshot() == (True, False)

    # 完成：available=True
    gate.set()
    assert _join_worker(provider) is True
    assert provider.status_snapshot() == (False, True)

    # unload 后：复位
    provider.unload()
    assert provider.status_snapshot() == (False, False)
