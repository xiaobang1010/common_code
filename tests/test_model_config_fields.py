"""模型配置新字段（输入类型/推理等级/推理参数映射）测试。

覆盖：
- CustomLLMModel 新字段 round-trip、存量配置默认值
- 校验器：input_types 枚举与缺 text 补正、等级空值/重复、映射非法 JSON/未知占位符
- get_model_config provider 分支带回新字段、supports_vision 派生
- 路由：POST/PUT 非法模型返回 400 + ok/error 可读、合法保存后 GET 回显新字段
"""

from __future__ import annotations

import dataclasses
import types

import pytest

import startup.config as config_module
from server.routers.config import routes as config_routes
from startup.config import CustomLLMModel, CustomLLMProvider
from startup.model.config import get_model_config


# ---------------------------------------------------------------------------
# 公共 fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_config(tmp_path, monkeypatch):
    """隔离配置：临时 HOME + 重置配置系统缓存 + 初始化。"""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    config_module._config_reading_allowed = False
    config_module._global_config_cache = None
    config_module.enable_configs()
    yield tmp_path
    config_module._config_reading_allowed = False
    config_module._global_config_cache = None


@pytest.fixture
def client(isolated_config, monkeypatch):
    """TestClient + 隔离掉供应商保存路径上的全局副作用（注册表/客户端/引擎）。"""
    from fastapi.testclient import TestClient
    from server.app import app

    class FakeRegistry:
        def __init__(self):
            self.custom = []

        def register_custom(self, provider):
            self.custom.append(provider)

        def set_active(self, name):
            return True

        def set_active_model(self, model_id):
            return None

    fake = FakeRegistry()
    monkeypatch.setattr(config_routes, "get_registry", lambda: fake)
    monkeypatch.setattr(config_routes, "reset_client", lambda: None)
    monkeypatch.setattr(config_routes, "get_default_model", lambda: "test-model")
    import server.state as server_state
    monkeypatch.setattr(
        server_state, "app_state",
        types.SimpleNamespace(get_state=lambda: types.SimpleNamespace(model=None)),
    )
    @dataclasses.dataclass
    class FakeEngineConfig:
        model: str = "other"

    monkeypatch.setattr(
        server_state, "engine",
        types.SimpleNamespace(config=FakeEngineConfig(), _config=None),
    )
    return TestClient(app)


def _model_entry(**over) -> dict:
    base = {"model_id": "qwen-test", "context_window": 1000000, **over}
    return base


# ---------------------------------------------------------------------------
# 存储层 round-trip 与默认值
# ---------------------------------------------------------------------------


class TestCustomLLMModelRoundTrip:
    def test_round_trip_new_fields(self):
        m = CustomLLMModel(
            model_id="glm-x",
            context_window=200000,
            max_output_tokens=64000,
            input_types=["text", "image", "pdf"],
            reasoning_levels=["disabled", "low", "max"],
            reasoning_params_map='{"disabled": {"thinking": {"type": "disabled"}}}',
        )
        assert CustomLLMModel.from_dict(m.to_dict()) == m

    def test_legacy_dict_gets_defaults(self):
        # 存量配置只有旧三字段，加载后新字段按默认值补齐
        m = CustomLLMModel.from_dict(
            {"model_id": "old", "context_window": 128000, "max_output_tokens": 4096}
        )
        assert m.input_types == ["text"]
        assert m.reasoning_levels == []
        assert m.reasoning_params_map == ""

    def test_provider_round_trip(self):
        p = CustomLLMProvider(
            id="pid", name="n", base_url="http://x", models=[
                CustomLLMModel(model_id="a", input_types=["text", "image"]),
            ]
        )
        assert CustomLLMProvider.from_dict(p.to_dict()) == p


# ---------------------------------------------------------------------------
# 校验器行为
# ---------------------------------------------------------------------------


class TestValidateAndFixModels:
    def test_text_autofix(self):
        raw = [_model_entry(input_types=["image"])]
        assert config_routes._validate_and_fix_models(raw) is None
        assert raw[0]["input_types"] == ["text", "image"]

    def test_invalid_enum_rejected(self):
        raw = [_model_entry(input_types=["audio"])]
        err = config_routes._validate_and_fix_models(raw)
        assert err and "输入类型" in err

    def test_empty_and_duplicate_levels_rejected(self):
        assert "空值" in config_routes._validate_and_fix_models(
            [_model_entry(reasoning_levels=["low", ""])]
        )
        assert "重复" in config_routes._validate_and_fix_models(
            [_model_entry(reasoning_levels=["low", "low"])]
        )

    def test_bad_json_rejected(self):
        err = config_routes._validate_and_fix_models(
            [_model_entry(reasoning_params_map="{oops")]
        )
        assert err and "JSON" in err

    def test_unknown_placeholder_rejected(self):
        err = config_routes._validate_and_fix_models(
            [_model_entry(
                reasoning_levels=["low"],
                reasoning_params_map='{"a": "{other}"}',
            )]
        )
        assert err and "reasoningLevel" in err

    def test_per_level_form_accepted(self):
        assert config_routes._validate_and_fix_models([_model_entry(
            reasoning_levels=["disabled", "low"],
            reasoning_params_map='{"disabled": {"thinking": {"type": "disabled"}}, "low": {"reasoning_effort": "low"}}',
        )]) is None

    def test_non_dict_model_rejected(self):
        assert config_routes._validate_and_fix_models(["x"]) is not None


# ---------------------------------------------------------------------------
# get_model_config 带回新字段与派生
# ---------------------------------------------------------------------------


class TestGetModelConfig:
    def test_provider_branch_carries_fields(self, isolated_config):
        from startup.config import get_global_config, save_global_config
        gc = get_global_config()
        gc.llm_providers = [CustomLLMProvider(
            id="p1", name="P", base_url="http://x",
            models=[CustomLLMModel(
                model_id="my-vision-model",
                input_types=["text", "image"],
                reasoning_levels=["low", "max"],
                reasoning_params_map='{"reasoning_effort": "{reasoningLevel}"}',
            )],
        ).to_dict()]
        save_global_config(gc)

        cfg = get_model_config("my-vision-model")
        assert cfg.input_types == ("text", "image")
        assert cfg.supports_vision is True
        assert cfg.reasoning_levels == ("low", "max")
        assert cfg.reasoning_params_map == '{"reasoning_effort": "{reasoningLevel}"}'
        assert cfg.window_source == "provider"

    def test_builtin_vision_derivation(self):
        assert get_model_config("gpt-4o").supports_vision is True
        assert get_model_config("deepseek-chat").supports_vision is False
        assert get_model_config("deepseek-chat").input_types == ("text",)

    def test_unknown_model_defaults_text_only(self):
        cfg = get_model_config("totally-unknown-model-xyz")
        assert cfg.input_types == ("text",)
        assert cfg.reasoning_levels == ()


class TestReasoningPresets:
    """内置预设库：已知模型开箱即有等级，显式配置优先于预设。"""

    @pytest.fixture(autouse=True)
    def _isolated(self, isolated_config):
        """预设断言不读真实用户配置，避免环境干扰。"""

    def test_qwen3_series_preset(self):
        cfg = get_model_config("qwen3.8-flash")
        assert cfg.reasoning_levels == ("low", "medium", "xhigh")
        assert cfg.reasoning_params_map == '{"reasoning_effort": "{reasoningLevel}"}'

    def test_qwen_namespaced_preset(self):
        # 带命名空间前缀注册的 qwen 模型同样命中
        assert get_model_config("Qwen/Qwen3-235B-A22B").reasoning_levels == (
            "low", "medium", "xhigh",
        )

    def test_glm_53_preset(self):
        cfg = get_model_config("ZHIPU/GLM-5.3")
        assert cfg.reasoning_levels == ("low", "high", "max")
        assert '"thinking"' in cfg.reasoning_params_map

    def test_o_series_preset(self):
        assert get_model_config("o3-mini").reasoning_levels == ("low", "medium", "high")

    def test_known_but_unpreset_model_empty(self):
        # 已知但不在预设系列的模型（deepseek-chat）不套预设
        assert get_model_config("deepseek-chat").reasoning_levels == ()

    def test_user_config_wins_over_preset(self, isolated_config):
        from startup.config import get_global_config, save_global_config
        gc = get_global_config()
        gc.llm_providers = [CustomLLMProvider(
            id="p", name="P", base_url="http://x",
            models=[CustomLLMModel(
                model_id="qwen3.8-flash",
                reasoning_levels=["only"],
                reasoning_params_map='{"effort": "{reasoningLevel}"}',
            )],
        ).to_dict()]
        save_global_config(gc)
        cfg = get_model_config("qwen3.8-flash")
        assert cfg.reasoning_levels == ("only",)

    def test_get_providers_echoes_preset(self, client):
        """自定义供应商模型未配等级时，GET 回显合并预设（前端选择器开箱可见）。"""
        r = client.post("/api/llm-providers", json={
            "name": "Ali", "base_url": "http://x/v1",
            "models": [{"model_id": "qwen3.8-flash", "context_window": 1000000}],
        })
        assert r.status_code == 200
        # 保存的原始配置不带等级
        assert r.json()["provider"]["models"][0]["reasoning_levels"] == []
        # GET 回显合并了预设
        g = client.get("/api/llm-providers").json()
        m = g["providers"][0]["models"][0]
        assert m["reasoning_levels"] == ["low", "medium", "xhigh"]
        assert m["reasoning_params_map"] == '{"reasoning_effort": "{reasoningLevel}"}'


# ---------------------------------------------------------------------------
# 路由层：400 拒绝与回显
# ---------------------------------------------------------------------------


class TestProviderRoutes:
    def test_post_invalid_levels_400(self, client):
        r = client.post("/api/llm-providers", json={
            "name": "P", "base_url": "http://x",
            "models": [_model_entry(reasoning_levels=["low", "low"])],
        })
        assert r.status_code == 400
        body = r.json()
        assert body["ok"] is False and "重复" in body["error"]

    def test_post_invalid_map_400(self, client):
        r = client.post("/api/llm-providers", json={
            "name": "P", "base_url": "http://x",
            "models": [_model_entry(reasoning_params_map="[1,2]")],
        })
        assert r.status_code == 400
        assert "JSON" in r.json()["error"]

    def test_post_valid_saves_and_get_echoes(self, client):
        r = client.post("/api/llm-providers", json={
            "name": "P", "base_url": "http://x", "api_format": "openai",
            "models": [_model_entry(
                max_output_tokens=131072,
                input_types=["image"],  # 缺 text，应被补正
                reasoning_levels=["low", "medium", "xhigh"],
                reasoning_params_map='{"reasoning_effort": "{reasoningLevel}"}',
            )],
        })
        assert r.status_code == 200
        saved = r.json()["provider"]["models"][0]
        assert saved["input_types"] == ["text", "image"]
        assert saved["reasoning_levels"] == ["low", "medium", "xhigh"]
        assert saved["max_output_tokens"] == 131072

        g = client.get("/api/llm-providers").json()
        m = g["providers"][0]["models"][0]
        assert m["input_types"] == ["text", "image"]
        assert m["reasoning_params_map"] == '{"reasoning_effort": "{reasoningLevel}"}'

    def test_put_full_replace_validates(self, client):
        r = client.post("/api/llm-providers", json={
            "name": "P", "base_url": "http://x",
            "models": [_model_entry(input_types=["text", "image"])],
        })
        pid = r.json()["provider"]["id"]
        # 全量替换语义：不带新字段的提交会被重建为默认值（前端恒提交全字段）
        bad = client.put(f"/api/llm-providers/{pid}", json={
            "name": "P", "models": [_model_entry(reasoning_levels=["a", "a"])],
        })
        assert bad.status_code == 400
        ok = client.put(f"/api/llm-providers/{pid}", json={
            "name": "P",
            "models": [_model_entry(
                reasoning_levels=["low"],
                reasoning_params_map='{"reasoning_effort": "{reasoningLevel}"}',
            )],
        })
        assert ok.status_code == 200
        assert ok.json()["provider"]["models"][0]["reasoning_levels"] == ["low"]
