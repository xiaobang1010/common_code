"""Anthropic 缓存断点协议级 E2E 测试。

用本地 mock 端点替代真实 Anthropic 服务，走完整的 httpx 流式调用链路，
断言实际发出的请求体携带 cache_control 断点（system 静态段 + 消息尾部），
且响应 SSE 中的 cache_read/cache_creation 用量能被正确解析进 usage 事件。
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import pytest

import query.services.api.anthropic_llm as al


# mock 端点返回的最小 Anthropic SSE 流（含缓存用量字段）。
# 每个事件以「data: <json> + 空行」为边界（解析器按空行切分）
_SSE_BODY = "".join(
    f"data: {json.dumps(evt)}\n\n"
    for evt in [
        {
            "type": "message_start",
            "message": {"usage": {
                "input_tokens": 5,
                "cache_read_input_tokens": 1200,
                "cache_creation_input_tokens": 300,
            }},
        },
        {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "收到"},
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": 2},
        },
        {"type": "message_stop"},
    ]
)


class _CaptureHandler(BaseHTTPRequestHandler):
    """记录每次请求体，返回固定 SSE 流。"""

    captured: list[dict] = []

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        type(self).captured.append(json.loads(raw))
        body = _SSE_BODY.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # 静默
        pass


@pytest.fixture
def mock_endpoint(monkeypatch):
    """启动本地 mock 服务，把 Anthropic 连接配置指向它。"""
    _CaptureHandler.captured = []
    server = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    monkeypatch.setattr(
        al, "_get_anthropic_config",
        lambda: (f"http://127.0.0.1:{port}", "test-key", "test-model"),
    )
    # 缓存开关打桩为开启，避免测试触碰用户真实配置文件
    import startup.config
    monkeypatch.setattr(
        startup.config, "get_global_config",
        lambda: SimpleNamespace(prompt_cache_enabled=True),
    )
    yield f"http://127.0.0.1:{port}"
    server.shutdown()


async def _collect(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


def test_request_carries_breakpoints_and_usage_parses(mock_endpoint):
    """实发请求体带 system 静态段与消息尾部断点；缓存用量解析进 usage 事件。"""
    messages = [
        {"role": "system", "content": "静态段合并结果"},
        {"role": "system", "content": "动态段：项目信息"},
        {"role": "user", "content": "你好"},
    ]
    events = asyncio.run(_collect(
        al.query_model_with_streaming_anthropic(messages=messages, model="test-model")
    ))

    assert len(_CaptureHandler.captured) == 1
    payload = _CaptureHandler.captured[0]

    # system 为 block 数组，第一条（静态段）挂断点，动态段不挂
    assert isinstance(payload["system"], list)
    assert payload["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in payload["system"][1]
    # 最后一条消息最后一个 block 挂滚动断点
    last = payload["messages"][-1]
    assert last["content"][-1]["cache_control"] == {"type": "ephemeral"}

    # usage 事件携带缓存读取/写入量
    usage_events = [e for e in events if e.type == "usage" and e.usage]
    assert usage_events, "应有 usage 事件"
    first_usage = usage_events[0].usage
    assert first_usage["cache_read_input_tokens"] == 1200
    assert first_usage["cache_creation_input_tokens"] == 300
    # 文本增量正常产出
    assert any(e.type == "content" and e.content == "收到" for e in events)
