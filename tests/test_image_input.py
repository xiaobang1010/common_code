"""图片输入链路测试。

覆盖：
- 附件兜底校验五类拒绝（数量/大小/mime/一致性/非视觉模型）与合法归一化
- content parts 构造：有图 parts / 无图纯字符串
- /api/state 占位字面值与响应副本替换（引擎原消息不动）
- _visible_user_indexes 三分支（list 提取 text 块 / 纯图片可见 / 其他形态跳过）
- engine.submitMessage 接受 parts、hook 入参保持纯文本
- Anthropic image_url → image source 块转换；OpenAI dict 直通（message_format 零改动断言）
- token 估算：每图固定 1500、不随图片字节大小变化、纯文本口径不变
"""

from __future__ import annotations

import base64
import json

import pytest

from query.services.api.anthropic_llm import _convert_user_content
from query.services.api.llm import _build_messages
from query.utils.tokens import (
    IMAGE_TOKEN_COST,
    count_context_tokens,
    count_image_blocks,
    estimate_tokens_for_messages,
)
from server.routers.chat import routes as chat_routes
from startup.model.config import ModelConfig


def _data_url(size_bytes: int = 1024, mime: str = "image/png") -> str:
    payload = base64.b64encode(b"x" * size_bytes).decode()
    return f"data:{mime};base64,{payload}"


def _fake_vision(with_image: bool = True):
    types = ("text", "image") if with_image else ("text",)
    return ModelConfig(name="m", context_window=200000, max_output_tokens=8192, input_types=types)


@pytest.fixture
def vision_model(monkeypatch):
    import query.services.api.client as client_module
    import startup.model.config as model_config_module

    monkeypatch.setattr(client_module, "get_default_model", lambda: "m")
    monkeypatch.setattr(model_config_module, "get_model_config", lambda x: _fake_vision(True))
    return monkeypatch


@pytest.fixture
def text_only_model(monkeypatch):
    import query.services.api.client as client_module
    import startup.model.config as model_config_module

    monkeypatch.setattr(client_module, "get_default_model", lambda: "m")
    monkeypatch.setattr(model_config_module, "get_model_config", lambda x: _fake_vision(False))
    return monkeypatch


# ---------------------------------------------------------------------------
# 附件校验五类
# ---------------------------------------------------------------------------


class TestValidateImages:
    def test_valid_normalized(self, vision_model):
        imgs, err = chat_routes._validate_images([
            {"name": "a.png", "mime": "image/png", "data_url": _data_url()},
        ])
        assert err is None
        assert imgs[0]["mime"] == "image/png"

    def test_count_limit(self, vision_model):
        items = [{"mime": "image/png", "data_url": _data_url(64)}] * 5
        _, err = chat_routes._validate_images(items)
        assert err and "最多" in err

    def test_size_limit_decoded(self, vision_model):
        # 解码后 6MB（构造按解码后字节口径）
        _, err = chat_routes._validate_images([
            {"mime": "image/png", "data_url": _data_url(6 * 1024 * 1024)},
        ])
        assert err and "5MB" in err

    def test_bad_mime(self, vision_model):
        _, err = chat_routes._validate_images([
            {"mime": "application/pdf", "data_url": _data_url(mime="application/pdf")},
        ])
        assert err and "image/*" in err

    def test_mime_mismatch(self, vision_model):
        _, err = chat_routes._validate_images([
            {"mime": "image/jpeg", "data_url": _data_url(mime="image/png")},
        ])
        assert err and "不一致" in err

    def test_non_vision_model_rejected(self, text_only_model):
        _, err = chat_routes._validate_images([
            {"mime": "image/png", "data_url": _data_url()},
        ])
        assert err and "不支持图片" in err

    def test_malformed_entries(self, vision_model):
        assert chat_routes._validate_images(["x"])[1] is not None
        assert chat_routes._validate_images([{"mime": "image/png"}])[1] is not None
        assert chat_routes._validate_images([{"mime": "image/png", "data_url": "http://x"}])[1] is not None


# ---------------------------------------------------------------------------
# parts 构造与占位
# ---------------------------------------------------------------------------


class TestPartsConstruction:
    def test_with_images_builds_parts(self):
        parts = chat_routes._build_user_content("看图", [
            {"name": "", "mime": "image/png", "data_url": "data:image/png;base64,AAA"},
        ])
        assert parts == [
            {"type": "text", "text": "看图"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
        ]

    def test_pure_image_no_text_block(self):
        parts = chat_routes._build_user_content("", [
            {"name": "", "mime": "image/png", "data_url": "data:image/png;base64,AAA"},
        ])
        assert parts[0]["type"] == "image_url"

    def test_no_images_stays_string(self):
        assert chat_routes._build_user_content("纯文本", []) == "纯文本"

    def test_omit_sentinel_and_copy(self):
        original = [{
            "role": "user",
            "content": [
                {"type": "text", "text": "hi"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,LONGPAYLOAD"}},
            ],
        }]
        out = chat_routes._omit_image_payloads(original)
        url = out[0]["content"][1]["image_url"]["url"]
        assert url == "data:image/png;base64,__omitted__"
        # 引擎原消息未被原地修改
        assert original[0]["content"][1]["image_url"]["url"] == "data:image/png;base64,LONGPAYLOAD"
        assert out[0] is not original[0]


# ---------------------------------------------------------------------------
# 可见序号三分支
# ---------------------------------------------------------------------------


class TestVisibleUserIndexes:
    def test_three_branches(self):
        messages = [
            {"role": "user", "content": "文本"},                       # 0 可见
            {"role": "user", "content": "<system-reminder>x</system-reminder>"},  # 1 跳过
            {"role": "user", "content": [                              # 2 可见（list 提取 text）
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,A"}},
            ]},                                                        # 纯图片也可见
            {"role": "user", "content": 123},                          # 3 跳过（其他形态）
            {"role": "assistant", "content": "回复"},
        ]
        assert chat_routes._visible_user_indexes(messages) == [0, 2]

    def test_list_system_reminder_skipped(self):
        messages = [{"role": "user", "content": [
            {"type": "text", "text": "<system-reminder>注入</system-reminder>"},
        ]}]
        assert chat_routes._visible_user_indexes(messages) == []


# ---------------------------------------------------------------------------
# 引擎与 provider 组装
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_submit_message_accepts_parts(monkeypatch):
    from dataclasses import dataclass

    from query.config import build_query_config
    from query.engine import QueryEngine, build_engine_config
    from query.loop import query_loop
    from query.services.api.llm import StreamEvent

    @dataclass
    class FakeDeps:
        def get_uuid(self):
            return "u"

        async def call_model(self, **kwargs):
            yield StreamEvent(type="content", content="ok")
            yield StreamEvent(type="done", finish_reason="stop")
            yield StreamEvent(type="usage", usage={"total_tokens": 1})

    parts = [
        {"type": "text", "text": "看看这张图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    config = build_engine_config(model="fake", tools=[], deps=FakeDeps())
    engine = QueryEngine(config, initial_messages=[])
    _ = [ev async for ev in engine.submitMessage(parts)]
    user_msgs = [m for m in engine.messages if m.get("role") == "user"]
    assert user_msgs[0]["content"] == parts


@pytest.mark.asyncio
async def test_hook_prompt_stays_text_for_parts(monkeypatch):
    """含图 parts 进引擎时，UserPromptSubmit hook 的 prompt 仍为纯文本（图片占位）。"""
    from dataclasses import dataclass

    from query.engine import QueryEngine, build_engine_config
    from query.services.api.llm import StreamEvent
    import startup.hooks as hooks_module
    import startup.setup as setup_module

    @dataclass
    class FakeDeps:
        def get_uuid(self):
            return "u"

        async def call_model(self, **kwargs):
            yield StreamEvent(type="content", content="ok")
            yield StreamEvent(type="done", finish_reason="stop")
            yield StreamEvent(type="usage", usage={"total_tokens": 1})

    captured: dict = {}

    async def fake_run(snapshot, prompt, session_id, cwd):
        captured["prompt"] = prompt
        return None

    monkeypatch.setattr(setup_module, "get_hooks_snapshot", lambda: object())
    monkeypatch.setattr(hooks_module, "run_user_prompt_submit_hooks", fake_run)

    parts = [
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    config = build_engine_config(model="fake", tools=[], deps=FakeDeps())
    engine = QueryEngine(config, initial_messages=[])
    _ = [ev async for ev in engine.submitMessage(parts)]
    assert captured["prompt"] == "看图\n[image]"


def test_openai_dict_passthrough_unchanged():
    """message_format/llm 零改动断言：dict 消息的 list content 原样送达。"""
    parts = [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAA"}},
    ]
    out = _build_messages([{"role": "user", "content": parts, "_ts": 1.0}])
    assert out == [{"role": "user", "content": parts}]


def test_anthropic_image_block_conversion():
    out = _convert_user_content([
        {"type": "text", "text": "看图"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,QUJD"}},
    ])
    assert out == [
        {"type": "text", "text": "看图"},
        {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": "QUJD"}},
    ]
    # 字符串 content 原样
    assert _convert_user_content("纯文本") == "纯文本"


# ---------------------------------------------------------------------------
# token 估算口径
# ---------------------------------------------------------------------------


class TestTokenEstimation:
    def _msg(self, payload_bytes: int):
        return {"role": "user", "content": [
            {"type": "text", "text": "问题"},
            {"type": "image_url", "image_url": {"url": _data_url(payload_bytes)}},
        ]}

    def test_image_fixed_cost_independent_of_size(self):
        small = [self._msg(1024)]
        large = [self._msg(4 * 1024 * 1024)]
        assert count_context_tokens(small) == count_context_tokens(large)
        # 固定成本恰为 1500/图（文本骨架部分两图相同）
        assert count_context_tokens(small) >= IMAGE_TOKEN_COST
        assert count_context_tokens(small) - IMAGE_TOKEN_COST < 100

    def test_both_entries_add_1500_per_image(self):
        msgs = [self._msg(2048)]
        assert count_image_blocks(msgs) == 1
        # estimate 入口：文本字符÷4 + 1500
        text_only = [{"role": "user", "content": "问题"}]
        base = estimate_tokens_for_messages(text_only)
        # 含图消息的文本部分与原消息一致（图片块被剔除），差值恰为固定成本
        with_image = estimate_tokens_for_messages(msgs)
        # text 块 + 消息骨架略有差异，断言固定成本被计入且图片大小无影响
        assert with_image >= base + IMAGE_TOKEN_COST - 10
        assert estimate_tokens_for_messages([self._msg(4 * 1024 * 1024)]) == with_image

    def test_str_messages_unchanged_baseline(self):
        """纯文本消息计数与改造前口径一致（json.dumps 原字符数÷3）。"""
        msgs = [
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "hi"},
        ]
        expected = sum(len(json.dumps(m, ensure_ascii=False)) for m in msgs) // 3
        assert count_context_tokens(msgs) == expected
