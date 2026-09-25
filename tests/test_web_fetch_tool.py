"""WebFetch 工具测试：scheme 校验、抓取转换、缓存命中、错误文案可行动。"""

from __future__ import annotations

import pytest

from tools.implementations.runtime.errors import ToolExecutionError
from tools.implementations.web_fetch_tool.handler import (
    MAX_BODY_BYTES,
    MAX_CONTENT_CHARS,
    format_model_content,
    handle_web_fetch,
    validate_url,
)
from tools.implementations.web_fetch_tool.schema import WebFetchInput
from tools.implementations.web_fetch_tool.tool import get_web_fetch_tool
from tools.utils.schema import tool_to_openai_schema


def test_rejects_non_http_scheme():
    with pytest.raises(ToolExecutionError):
        validate_url("file:///etc/passwd")
    with pytest.raises(ToolExecutionError):
        validate_url("ftp://example.com/x")


def test_rejects_missing_host():
    with pytest.raises(ToolExecutionError):
        validate_url("https://")


@pytest.mark.asyncio
async def test_fetch_and_cache(workspace, monkeypatch):
    """首次抓取转 markdown；同 URL 二次调用命中缓存。"""
    calls = {"n": 0}

    class FakeResp:
        status_code = 200
        history: list = []
        url = "https://example.com/page"
        content = b"<html><head><style>x{}</style></head><body><h1>Hi</h1><p>Body</p></body></html>"
        headers = {"content-type": "text/html; charset=utf-8"}
        encoding = "utf-8"

        def raise_for_status(self) -> None: ...

    class FakeClient:
        def __init__(self, *a, **k): ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            calls["n"] += 1
            return FakeResp()

    import tools.implementations.web_fetch_tool.handler as handler

    monkeypatch.setattr(handler.httpx, "AsyncClient", FakeClient)

    r1 = await handle_web_fetch(WebFetchInput(url="https://example.com/page", prompt="总结"))
    assert calls["n"] == 1
    assert "# Hi" in r1["content"]  # ATX 标题
    assert "style" not in r1["content"]  # 样式剔除

    r2 = await handle_web_fetch(WebFetchInput(url="https://example.com/page", prompt="总结"))
    assert calls["n"] == 1  # 缓存命中，未再请求
    assert r2["from_cache"] is True

    text = format_model_content(r2)
    assert "缓存" in text
    assert "https://example.com/page" in text


@pytest.mark.asyncio
async def test_http_error_actionable_message(monkeypatch):
    """4xx 错误文案要提示换来源，而不是让模型重试同一地址。"""

    class FakeResp:
        status_code = 403
        history: list = []
        url = "https://example.com/deny"
        content = b""
        headers = {"content-type": "text/plain"}
        encoding = "utf-8"

    class FakeClient:
        def __init__(self, *a, **k): ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    import tools.implementations.web_fetch_tool.handler as handler

    monkeypatch.setattr(handler.httpx, "AsyncClient", FakeClient)

    with pytest.raises(ToolExecutionError) as exc_info:
        await handle_web_fetch(WebFetchInput(url="https://example.com/deny", prompt="x"))
    assert "换其他来源" in exc_info.value.message


@pytest.mark.asyncio
async def test_content_truncated_over_limit(monkeypatch):
    """正文超过截断上限时保留截断标记，避免超长内容灌进模型上下文。"""

    class FakeResp:
        status_code = 200
        history: list = []
        url = "https://example.com/long"
        content = ("a" * (MAX_CONTENT_CHARS + 5000)).encode("utf-8")
        headers = {"content-type": "text/plain; charset=utf-8"}
        encoding = "utf-8"

    class FakeClient:
        def __init__(self, *a, **k): ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    import tools.implementations.web_fetch_tool.handler as handler

    monkeypatch.setattr(handler.httpx, "AsyncClient", FakeClient)

    r = await handle_web_fetch(WebFetchInput(url="https://example.com/long", prompt="x"))
    assert r["content"].endswith("（内容过长已截断）")
    assert len(r["content"]) <= MAX_CONTENT_CHARS + 20


@pytest.mark.asyncio
async def test_body_too_large_rejected(monkeypatch):
    """响应体超 5MB 上限直接拒绝，不进转换流程，文案提示换更精确端点。"""

    class FakeResp:
        status_code = 200
        history: list = []
        url = "https://example.com/huge"
        content = b"x" * (MAX_BODY_BYTES + 1)
        headers = {"content-type": "application/octet-stream"}
        encoding = "utf-8"

    class FakeClient:
        def __init__(self, *a, **k): ...

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url):
            return FakeResp()

    import tools.implementations.web_fetch_tool.handler as handler

    monkeypatch.setattr(handler.httpx, "AsyncClient", FakeClient)

    with pytest.raises(ToolExecutionError) as exc_info:
        await handle_web_fetch(WebFetchInput(url="https://example.com/huge", prompt="x"))
    assert "上限" in exc_info.value.message


def test_web_fetch_registered_and_described():
    """工具池含 WebFetch，且模型侧描述为完整说明书。"""
    tool = get_web_fetch_tool()
    schema = tool_to_openai_schema(tool)
    assert schema["function"]["description"] == tool.prompt
    assert "WebFetch" in [t.name for t in __import__("tools").get_tools()]
