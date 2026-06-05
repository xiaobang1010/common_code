"""OpenAI 兼容 LLM 客户端模块。

提供线程安全的单例客户端实例、自定义 HTTP 客户端构建、
以及配置优先级解析（环境变量 > 配置文件 > 默认值）。

环境变量优先级：
  - base_url: LLM_BASE_URL > 配置文件 > 默认值
  - api_key:  LLM_API_KEY  > 配置文件 > 默认值
  - model:    LLM_MODEL    > 配置文件 > 默认值
"""

from __future__ import annotations

import os
import threading
import uuid
from typing import Any

import httpx
import openai

from startup.utils.config import get_global_config
from startup.utils.config_constants import (
    DEFAULT_LLM_API_KEY,
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ENV_LLM_MODEL,
)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

CLIENT_REQUEST_ID_HEADER = "x-client-request-id"

# 默认模型（当配置文件和环境变量均未指定时使用）
_FALLBACK_MODEL = "Qwen/Qwen3-235B-A22B"

# ---------------------------------------------------------------------------
# 单例状态（线程安全）
# ---------------------------------------------------------------------------

_client_lock = threading.Lock()
_client_instance: openai.OpenAI | None = None
_default_model_cache: str | None = None


# ---------------------------------------------------------------------------
# get_custom_headers — 解析 CC_CUSTOM_HEADERS 环境变量
# ---------------------------------------------------------------------------


def get_custom_headers() -> dict[str, str]:
    """解析 CC_CUSTOM_HEADERS 环境变量。

    格式：key1:value1,key2:value2
    冒号分隔键值，逗号分隔多对。
    """
    custom_headers: dict[str, str] = {}
    raw = os.environ.get("CC_CUSTOM_HEADERS", "")
    if not raw:
        return custom_headers

    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        colon_idx = pair.find(":")
        if colon_idx == -1:
            continue
        name = pair[:colon_idx].strip()
        value = pair[colon_idx + 1 :].strip()
        if name:
            custom_headers[name] = value

    return custom_headers


# ---------------------------------------------------------------------------
# build_http_client — 构建自定义 HTTP 客户端
# ---------------------------------------------------------------------------


def build_http_client() -> httpx.Client:
    """构建自定义 httpx.Client。

    功能：
      - 注入 x-client-request-id header（UUID）
      - 注入 CC_CUSTOM_HEADERS 中的自定义 header
      - 支持代理配置（HTTPS_PROXY / https_proxy / HTTP_PROXY / http_proxy）
    """
    headers: dict[str, str] = {
        CLIENT_REQUEST_ID_HEADER: str(uuid.uuid4()),
    }
    headers.update(get_custom_headers())

    # 代理配置
    proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
    )

    kwargs: dict[str, Any] = {
        "headers": headers,
        "timeout": httpx.Timeout(600.0, connect=10.0),
    }
    if proxy:
        kwargs["proxy"] = proxy

    return httpx.Client(**kwargs)


# ---------------------------------------------------------------------------
# 配置解析辅助
# ---------------------------------------------------------------------------


def _resolve_base_url() -> str:
    """解析 base_url，优先级：LLM_BASE_URL > 配置文件 > 默认值。"""
    return (
        os.environ.get(ENV_LLM_BASE_URL)
        or _get_config_field("llm_base_url")
        or DEFAULT_LLM_BASE_URL
    )


def _resolve_api_key() -> str | None:
    """解析 api_key，优先级：LLM_API_KEY > 配置文件 > 默认值。"""
    return (
        os.environ.get(ENV_LLM_API_KEY)
        or _get_config_field("llm_api_key")
        or DEFAULT_LLM_API_KEY
    )


def _resolve_model() -> str:
    """解析默认模型名，优先级：LLM_MODEL > 配置文件 > 默认值。"""
    return (
        os.environ.get(ENV_LLM_MODEL)
        or _get_config_field("llm_model")
        or DEFAULT_LLM_MODEL
        or _FALLBACK_MODEL
    )


def _get_config_field(field: str) -> str | None:
    """从全局配置中安全读取字段。"""
    try:
        config = get_global_config()
        return getattr(config, field, None)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# get_default_model — 获取默认模型名
# ---------------------------------------------------------------------------


def get_default_model() -> str:
    """获取默认模型名。

    线程安全，结果缓存。
    """
    global _default_model_cache

    if _default_model_cache is not None:
        return _default_model_cache

    model = _resolve_model()
    _default_model_cache = model
    return model


# ---------------------------------------------------------------------------
# get_llm_client — 获取 LLM 客户端实例（单例，缓存）
# ---------------------------------------------------------------------------


def get_llm_client() -> openai.OpenAI:
    """获取 OpenAI 兼容 LLM 客户端实例。

    单例模式，线程安全。首次调用时创建实例并缓存。
    后续调用直接返回缓存实例。

    配置来源：
      - base_url: LLM_BASE_URL > 配置文件 > 默认值
      - api_key:  LLM_API_KEY  > 配置文件 > 默认值
    """
    global _client_instance

    if _client_instance is not None:
        return _client_instance

    with _client_lock:
        # 双重检查锁定
        if _client_instance is not None:
            return _client_instance

        base_url = _resolve_base_url()
        api_key = _resolve_api_key() or "sk-placeholder"

        http_client = build_http_client()

        _client_instance = openai.OpenAI(
            base_url=base_url,
            api_key=api_key,
            http_client=http_client,
        )

        return _client_instance


# ---------------------------------------------------------------------------
# reset_client — 重置客户端（用于测试或配置变更后）
# ---------------------------------------------------------------------------


def reset_client() -> None:
    """重置缓存的客户端实例和模型名。

    主要用于测试场景或配置变更后需要重新创建客户端时。
    """
    global _client_instance, _default_model_cache

    with _client_lock:
        _client_instance = None
        _default_model_cache = None


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import json

    print("=" * 60)
    print("OpenAI 兼容 LLM 客户端测试")
    print("=" * 60)

    # ---- 测试 1: get_llm_client() 返回 openai.OpenAI 实例 ----
    print("\n--- 测试 1: get_llm_client() ---")
    try:
        client = get_llm_client()
        assert isinstance(client, openai.OpenAI), f"类型不匹配: {type(client)}"
        print(f"  客户端类型: {type(client).__name__}")
        print(f"  base_url: {client.base_url}")
        # 再次调用应返回同一实例
        client2 = get_llm_client()
        assert client is client2, "单例模式失败：两次调用返回不同实例"
        print("  [PASS] get_llm_client() 单例模式")
    except Exception as e:
        print(f"  [FAIL] get_llm_client() 失败: {e}")

    # ---- 测试 2: get_default_model() ----
    print("\n--- 测试 2: get_default_model() ---")
    try:
        model = get_default_model()
        assert isinstance(model, str) and len(model) > 0
        print(f"  默认模型: {model}")
        print("  [PASS] get_default_model()")
    except Exception as e:
        print(f"  [FAIL] get_default_model() 失败: {e}")

    # ---- 测试 3: build_http_client() ----
    print("\n--- 测试 3: build_http_client() ---")
    try:
        http_client = build_http_client()
        assert isinstance(http_client, httpx.Client)
        assert CLIENT_REQUEST_ID_HEADER in http_client.headers
        request_id = http_client.headers[CLIENT_REQUEST_ID_HEADER]
        # 验证是合法 UUID
        uuid.UUID(request_id)
        print(f"  x-client-request-id: {request_id}")
        print("  [PASS] build_http_client()")
        http_client.close()
    except Exception as e:
        print(f"  [FAIL] build_http_client() 失败: {e}")

    # ---- 测试 4: get_custom_headers() ----
    print("\n--- 测试 4: get_custom_headers() ---")
    try:
        # 无环境变量时应返回空
        os.environ.pop("CC_CUSTOM_HEADERS", None)
        headers = get_custom_headers()
        assert headers == {}, f"无环境变量时应返回空: {headers}"
        print("  无 CC_CUSTOM_HEADERS → 空 dict")

        # 设置环境变量
        os.environ["CC_CUSTOM_HEADERS"] = "X-App:test-app,X-Trace-Id:abc123"
        headers = get_custom_headers()
        assert headers.get("X-App") == "test-app"
        assert headers.get("X-Trace-Id") == "abc123"
        print(f"  解析结果: {headers}")
        print("  [PASS] get_custom_headers()")

        # 清理
        del os.environ["CC_CUSTOM_HEADERS"]
    except Exception as e:
        print(f"  [FAIL] get_custom_headers() 失败: {e}")
        os.environ.pop("CC_CUSTOM_HEADERS", None)

    # ---- 测试 5: 消息格式映射 ----
    print("\n--- 测试 5: 消息格式映射 ---")
    try:
        from query.services.api.message_format import (
            ChatMessage,
            MessageRole,
            to_openai_messages,
        )

        messages = [
            ChatMessage(role=MessageRole.SYSTEM, content="You are a helpful assistant."),
            ChatMessage(role=MessageRole.USER, content="Hello!"),
            ChatMessage(
                role=MessageRole.ASSISTANT,
                content=None,
                tool_calls=[
                    {
                        "id": "call_123",
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "arguments": '{"city": "Beijing"}',
                        },
                    }
                ],
            ),
            ChatMessage(
                role=MessageRole.TOOL,
                content='{"temp": 25}',
                tool_call_id="call_123",
            ),
            ChatMessage(role=MessageRole.ASSISTANT, content="The weather in Beijing is 25°C."),
        ]

        openai_msgs = to_openai_messages(messages)

        assert openai_msgs[0] == {"role": "system", "content": "You are a helpful assistant."}
        assert openai_msgs[1] == {"role": "user", "content": "Hello!"}
        assert openai_msgs[2]["role"] == "assistant"
        assert openai_msgs[2]["tool_calls"] is not None
        assert openai_msgs[3] == {"role": "tool", "tool_call_id": "call_123", "content": '{"temp": 25}'}
        assert openai_msgs[4] == {"role": "assistant", "content": "The weather in Beijing is 25°C."}

        for msg in openai_msgs:
            print(f"  {json.dumps(msg, ensure_ascii=False)}")
        print("  [PASS] 消息格式映射")
    except Exception as e:
        print(f"  [FAIL] 消息格式映射失败: {e}")

    # ---- 测试 6: 工具 Schema 转换 ----
    print("\n--- 测试 6: 工具 Schema 转换 ---")
    try:
        from pydantic import BaseModel

        from query.services.api.message_format import to_openai_tools
        from tools.protocol import Tool

        class SearchInput(BaseModel):
            query: str
            limit: int = 5

        search_tool = Tool(
            name="Search",
            description="Search the web",
            input_schema=SearchInput,
            execute=lambda inp, ctx: None,  # type: ignore
            prompt="Search the web",
        )

        openai_tools = to_openai_tools([search_tool])
        assert len(openai_tools) == 1
        schema = openai_tools[0]
        assert schema["type"] == "function"
        assert schema["function"]["name"] == "Search"
        assert "query" in schema["function"]["parameters"]["properties"]
        print(f"  {json.dumps(schema, ensure_ascii=False, indent=2)}")
        print("  [PASS] 工具 Schema 转换")
    except Exception as e:
        print(f"  [FAIL] 工具 Schema 转换失败: {e}")

    # ---- 测试 7: 环境变量优先级 ----
    print("\n--- 测试 7: 环境变量优先级 ---")
    try:
        # 重置客户端
        reset_client()

        # 设置 LLM 环境变量
        os.environ[ENV_LLM_BASE_URL] = "https://llm.example.com/v1"
        os.environ[ENV_LLM_API_KEY] = "sk-llm-key"

        base_url = _resolve_base_url()
        api_key = _resolve_api_key()

        assert base_url == "https://llm.example.com/v1", f"LLM_BASE_URL 应优先: {base_url}"
        assert api_key == "sk-llm-key", f"LLM_API_KEY 应优先: {api_key}"
        print(f"  base_url (LLM_* 优先): {base_url}")
        print(f"  api_key  (LLM_* 优先): {api_key[:6]}...")

        # 清理
        del os.environ[ENV_LLM_BASE_URL]
        del os.environ[ENV_LLM_API_KEY]
        reset_client()

        print("  [PASS] 环境变量优先级")
    except Exception as e:
        print(f"  [FAIL] 环境变量优先级失败: {e}")
        # 清理
        for var in [ENV_LLM_BASE_URL, ENV_LLM_API_KEY]:
            os.environ.pop(var, None)
        reset_client()

    # ---- 测试 8: 线程安全 ----
    print("\n--- 测试 8: 线程安全 ---")
    try:
        import concurrent.futures

        reset_client()

        def get_client():
            return get_llm_client()

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(get_client) for _ in range(50)]
            results = [f.result() for f in futures]

        # 所有结果应为同一实例
        assert all(r is results[0] for r in results), "线程安全失败：返回了不同实例"
        print(f"  50 个并发调用全部返回同一实例")
        print("  [PASS] 线程安全")
    except Exception as e:
        print(f"  [FAIL] 线程安全测试失败: {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
