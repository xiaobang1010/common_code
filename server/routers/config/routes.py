"""配置相关路由：LLM 配置读写、自定义供应商管理。

从 app.py 提取，原路由装饰器 @app.* 改为 @router.*。
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from query.services.api.client import get_default_model, reset_client
from query.services.api.providers import get_registry
import server.state
from startup.config import (
    CustomLLMModel,
    CustomLLMProvider,
    get_global_config,
    save_global_config,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# GET /api/config - 读取 LLM 配置
# ---------------------------------------------------------------------------


@router.get("/api/config")
def get_config() -> dict:
    """读取 LLM 配置接口。

    返回 {"llm_base_url", "llm_api_key", "llm_model",
          "llm_providers", "active_provider", "active_model"}。
    配置系统未初始化等异常情况下返回空值和 error 字段。
    """
    try:
        config = get_global_config()
        return {
            "llm_base_url": config.llm_base_url or "",
            "llm_api_key": config.llm_api_key or "",
            "llm_model": config.llm_model or "",
            "llm_providers": [
                CustomLLMProvider.from_dict(p).to_dict()
                for p in config.llm_providers
            ],
            "active_provider": config.active_provider,
            "active_model": config.active_model,
        }
    except Exception as e:
        return {
            "llm_base_url": "",
            "llm_api_key": "",
            "llm_model": "",
            "llm_providers": [],
            "active_provider": None,
            "active_model": None,
            "error": str(e),
        }


# ---------------------------------------------------------------------------
# POST /api/config - 写入 LLM 配置
# ---------------------------------------------------------------------------


@router.post("/api/config")
def set_config(body: dict) -> dict:
    """写入 LLM 配置接口。

    请求体：{"llm_base_url", "llm_api_key", "llm_model"}，只更新传入的字段。
    返回 {"ok": true} 或 {"ok": false, "error": "..."}
    """
    try:
        config = get_global_config()
        # 只更新传入的字段，不覆盖未传的字段
        if "llm_base_url" in body:
            config.llm_base_url = body["llm_base_url"]
        if "llm_api_key" in body:
            config.llm_api_key = body["llm_api_key"]
        if "llm_model" in body:
            config.llm_model = body["llm_model"]
        save_global_config(config)

        # 配置变更后：重置 LLM 客户端缓存 + 更新 AppState 的 model 字段
        # 否则引擎还会用旧模型名调 API，报 Invalid model id
        reset_client()
        state = server.state.app_state.get_state()
        from query.services.api.client import get_default_model
        state.model = get_default_model()

        # 同步更新引擎的模型名
        from dataclasses import replace as _replace
        new_model = get_default_model()
        if new_model and server.state.engine.config.model != new_model:
            server.state.engine._config = _replace(server.state.engine.config, model=new_model)

        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GET/POST /api/config/subagents - 子智能体执行底座配置
# ---------------------------------------------------------------------------


@router.get("/api/config/subagents")
def get_subagents_config() -> dict:
    """读取全局配置 subagents 段（camelCase 键，与前端约定一致）。"""
    try:
        return {"ok": True, "subagents": get_global_config().subagents.to_dict()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/config/subagents")
def set_subagents_config(body: dict) -> dict:
    """写入全局配置 subagents 段（部分更新，含数值校验）。

    请求体键（均可选）：modelOverrides / defaultModel / autoBackgroundMs /
    inactivityTimeoutMs / maxTurnsDefault / tokenBudgetDefault。
    数值字段必须为非负整数，模型覆盖必须为字符串映射，否则 400。
    """
    from startup.config.types import SubagentsConfig

    try:
        config = get_global_config()
        current = config.subagents.to_dict()
        merged = {**current, **{k: v for k, v in body.items() if k in current}}

        overrides = merged.get("modelOverrides", {})
        if not isinstance(overrides, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in overrides.items()
        ):
            return {"ok": False, "error": "modelOverrides 必须是字符串到字符串的映射"}
        for key in (
            "autoBackgroundMs",
            "inactivityTimeoutMs",
            "maxTurnsDefault",
            "tokenBudgetDefault",
        ):
            value = merged.get(key)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                return {"ok": False, "error": f"{key} 必须为非负整数"}
        if not isinstance(merged.get("defaultModel", ""), str):
            return {"ok": False, "error": "defaultModel 必须为字符串"}

        config.subagents = SubagentsConfig.from_dict(merged)
        save_global_config(config)
        return {"ok": True, "subagents": config.subagents.to_dict()}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# GET /api/llm-providers - 列出自定义 LLM 供应商
# ---------------------------------------------------------------------------


@router.get("/api/llm-providers")
def list_custom_llm_providers() -> dict:
    """列出自定义 LLM 供应商。"""
    config = get_global_config()
    providers = [CustomLLMProvider.from_dict(p).to_dict() for p in config.llm_providers]
    return {
        "providers": providers,
        "active_provider": config.active_provider,
        "active_model": config.active_model,
    }


# ---------------------------------------------------------------------------
# POST /api/llm-providers - 添加自定义 LLM 供应商
# ---------------------------------------------------------------------------


@router.post("/api/llm-providers")
def add_custom_llm_provider(body: dict) -> dict:
    """添加自定义 LLM 供应商。

    请求体：{"name", "base_url", "api_key", "api_format", "models": [...]}
    """
    config = get_global_config()

    # 判断是否是第一个自定义供应商
    is_first = len(config.llm_providers) == 0

    # 生成唯一 ID
    provider_id = str(uuid.uuid4())

    # 解析模型列表
    models = [CustomLLMModel.from_dict(m) for m in body.get("models", [])]

    # 创建供应商对象
    provider = CustomLLMProvider(
        id=provider_id,
        name=body.get("name", ""),
        base_url=body.get("base_url", ""),
        api_key=body.get("api_key", ""),
        api_format=body.get("api_format", "openai"),
        models=models,
    )

    # 添加到配置
    config.llm_providers.append(provider.to_dict())

    # 如果是第一个供应商，自动激活
    if is_first:
        config.active_provider = provider_id
        config.active_model = models[0].model_id if models else None

    save_global_config(config)

    # 注册到 registry
    registry = get_registry()
    registry.register_custom(provider)

    # 如果是第一个供应商，激活并更新客户端
    if is_first:
        registry.set_active(provider_id)
        if models:
            registry.set_active_model(models[0].model_id)
        reset_client()
        state = server.state.app_state.get_state()
        state.model = get_default_model()
        # 同步更新引擎的模型名
        from dataclasses import replace as _replace
        new_model = get_default_model()
        if new_model and server.state.engine.config.model != new_model:
            server.state.engine._config = _replace(server.state.engine.config, model=new_model)

    return {"provider": provider.to_dict()}


# ---------------------------------------------------------------------------
# PUT /api/llm-providers/{provider_id} - 更新自定义 LLM 供应商
# ---------------------------------------------------------------------------


@router.put("/api/llm-providers/{provider_id}")
def update_custom_llm_provider(provider_id: str, body: dict) -> dict:
    """更新自定义 LLM 供应商。

    请求体同添加，但不需要生成新 ID。
    """
    config = get_global_config()

    # 查找供应商
    found_idx = None
    for i, p in enumerate(config.llm_providers):
        if p.get("id") == provider_id:
            found_idx = i
            break

    if found_idx is None:
        return {"ok": False, "error": f"供应商不存在: {provider_id}"}

    old_provider = CustomLLMProvider.from_dict(config.llm_providers[found_idx])

    # 更新字段
    old_provider.name = body.get("name", old_provider.name)
    old_provider.base_url = body.get("base_url", old_provider.base_url)
    old_provider.api_key = body.get("api_key", old_provider.api_key)
    old_provider.api_format = body.get("api_format", old_provider.api_format)
    if "models" in body:
        old_provider.models = [
            CustomLLMModel.from_dict(m) for m in body["models"]
        ]

    # 保存配置
    config.llm_providers[found_idx] = old_provider.to_dict()
    save_global_config(config)

    # 重新注册到 registry（更新配置）
    registry = get_registry()
    registry.register_custom(old_provider)

    # 重置 LLM 客户端缓存
    reset_client()

    # 如果更新的是当前激活的供应商，更新 AppState 和引擎模型
    if config.active_provider == provider_id:
        state = server.state.app_state.get_state()
        state.model = get_default_model()
        # 同步更新引擎的模型名
        from dataclasses import replace as _replace
        new_model = get_default_model()
        if new_model and server.state.engine.config.model != new_model:
            server.state.engine._config = _replace(server.state.engine.config, model=new_model)

    return {"provider": old_provider.to_dict()}


# ---------------------------------------------------------------------------
# DELETE /api/llm-providers/{provider_id} - 删除自定义 LLM 供应商
# ---------------------------------------------------------------------------


@router.delete("/api/llm-providers/{provider_id}")
def delete_custom_llm_provider(provider_id: str) -> dict:
    """删除自定义 LLM 供应商。"""
    config = get_global_config()

    # 查找供应商是否存在
    provider_data = None
    for p in config.llm_providers:
        if p.get("id") == provider_id:
            provider_data = p
            break

    if provider_data is None:
        return {"ok": False, "error": f"供应商不存在: {provider_id}"}

    was_active = config.active_provider == provider_id

    # 从配置中移除
    config.llm_providers = [
        p for p in config.llm_providers if p.get("id") != provider_id
    ]

    # 如果删除的是激活的供应商，自动切换到列表中的第一个
    if was_active:
        if config.llm_providers:
            first = CustomLLMProvider.from_dict(config.llm_providers[0])
            config.active_provider = first.id
            config.active_model = first.models[0].model_id if first.models else None
        else:
            config.active_provider = None
            config.active_model = None

    save_global_config(config)

    # 从 registry 中移除（unregister 会自动切换 _active）
    registry = get_registry()
    registry.unregister(provider_id)

    # 如果删除的是激活的供应商，需要更新 registry 的激活模型
    if was_active and config.llm_providers:
        first = CustomLLMProvider.from_dict(config.llm_providers[0])
        if first.models:
            registry.set_active_model(first.models[0].model_id)

    # 重置 LLM 客户端缓存
    reset_client()

    # 更新 AppState
    state = server.state.app_state.get_state()
    state.model = get_default_model()

    return {"ok": True}


# ---------------------------------------------------------------------------
# POST /api/llm-providers/{provider_id}/test - 测试供应商连通性
# ---------------------------------------------------------------------------


@router.post("/api/llm-providers/{provider_id}/test")
def test_custom_llm_provider(provider_id: str) -> dict:
    """测试自定义 LLM 供应商连通性。"""
    config = get_global_config()

    # 查找供应商
    provider_data = None
    for p in config.llm_providers:
        if p.get("id") == provider_id:
            provider_data = p
            break

    if provider_data is None:
        return {"ok": False, "error": f"供应商不存在: {provider_id}"}

    provider = CustomLLMProvider.from_dict(provider_data)

    # 使用第一个模型测试
    if not provider.models:
        return {"ok": False, "error": "供应商没有配置模型"}

    model_id = provider.models[0].model_id

    try:
        if provider.api_format == "anthropic":
            # Anthropic 格式用 httpx 发请求
            import httpx

            url = f"{provider.base_url.rstrip('/')}/v1/messages"
            headers = {
                "x-api-key": provider.api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            }
            payload = {
                "model": model_id,
                "max_tokens": 1,
                "messages": [{"role": "user", "content": "hi"}],
            }
            with httpx.Client(timeout=15.0) as http_client:
                resp = http_client.post(url, headers=headers, json=payload)
            if resp.status_code >= 400:
                return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text}"}
        else:
            # OpenAI 格式用 openai SDK 创建临时客户端
            import openai as openai_sdk

            temp_client = openai_sdk.OpenAI(
                base_url=provider.base_url,
                api_key=provider.api_key or "sk-placeholder",
                timeout=15.0,
            )
            temp_client.chat.completions.create(
                model=model_id,
                messages=[{"role": "user", "content": "hi"}],
                max_tokens=1,
            )

        return {"ok": True, "message": "连接成功"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# POST /api/llm-providers/activate - 激活供应商和模型
# ---------------------------------------------------------------------------


@router.post("/api/llm-providers/activate")
def activate_llm_provider(body: dict) -> dict:
    """激活 LLM 供应商和模型。

    请求体：{"provider_id": "...", "model_id": "..."}
    """
    config = get_global_config()

    provider_id = body.get("provider_id", "")
    model_id = body.get("model_id", "")

    # 验证供应商存在
    provider_data = None
    for p in config.llm_providers:
        if p.get("id") == provider_id:
            provider_data = p
            break

    if provider_data is None:
        return {"ok": False, "error": f"供应商不存在: {provider_id}"}

    # 验证模型存在
    provider = CustomLLMProvider.from_dict(provider_data)
    model_ids = [m.model_id for m in provider.models]
    if model_id not in model_ids:
        return {"ok": False, "error": f"模型不存在: {model_id}"}

    # 更新配置
    config.active_provider = provider_id
    config.active_model = model_id
    save_global_config(config)

    # 更新 registry
    registry = get_registry()
    registry.set_active(provider_id)
    registry.set_active_model(model_id)

    # 重置 LLM 客户端缓存
    reset_client()

    # 更新 AppState
    state = server.state.app_state.get_state()
    state.model = get_default_model()

    # 同步更新引擎的模型名（config 是 frozen dataclass，需要用 replace 创建新配置）
    from dataclasses import replace as _replace
    new_model = get_default_model()
    if new_model and server.state.engine.config.model != new_model:
        server.state.engine._config = _replace(server.state.engine.config, model=new_model)

    return {"ok": True, "provider_id": provider_id, "model_id": model_id}
