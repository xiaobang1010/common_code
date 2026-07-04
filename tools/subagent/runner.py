"""子代理运行器 — run_agent async generator。

组装子代理的初始消息、工具池、系统提示词，调用 query_loop 运行子代理循环，
yield 每条消息，finally 清理资源。

子代理复用同一 query_loop 引擎，但拥有独立的消息历史、工具池、系统提示词。
普通子代理不继承父对话（从零开始），结果只回传最终 assistant 文本。

增强特性：
- sidechain transcript 增量写入（每条消息追加到 JSONL 文件）
- abort_event 检查（同步共享父的，异步独立）
- max_turns 传入 query_loop
- pending_messages 在 tool 轮次边界 drain
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, AsyncGenerator

from tools.protocol import Tool
from tools.subagent.context import SubagentContext

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# run_agent — 运行子代理循环
# ---------------------------------------------------------------------------


async def run_agent(
    ctx: SubagentContext,
    tools: list[Tool],
    system_prompt: str,
) -> AsyncGenerator[dict, None]:
    """运行子代理循环，yield 每条消息。

    流程：
    1. 写入 meta.json 元数据
    2. 构建 QueryEngineConfig（覆盖 model/tools/system_prompt_sections/max_turns）
    3. 创建 QueryEngine，传入 initial_messages
    4. 写入初始 transcript
    5. 调 query_loop，yield 每条消息
       - 每条消息增量写入 transcript
       - 检查 abort_event，触发时优雅退出
       - 在 tool 轮次边界 drain pending_messages
    6. finally 清理资源

    Args:
        ctx: 子代理执行上下文（含 initial_messages、model、agent_def、abort_event）
        tools: 过滤后的工具池
        system_prompt: 子代理系统提示词

    Yields:
        dict: 子代理产生的消息（assistant/tool 等）
    """
    from dataclasses import replace as dc_replace

    from query.engine import QueryEngine, build_engine_config
    from query.config import build_query_config
    from query.loop import query_loop
    from startup.constants.prompts import SystemPromptSection
    from tools.subagent.transcript import (
        record_sidechain_transcript,
        write_agent_metadata,
    )

    # 1. 写入元数据
    write_agent_metadata(
        agent_id=ctx.agent_id,
        agent_type=ctx.agent_def.agent_type,
        description=ctx.initial_messages[0].get("content", "")[:100] if ctx.initial_messages else "",
        model=ctx.model,
    )

    # 2. 构建 QueryEngineConfig
    engine_config = build_engine_config(
        model=ctx.model,
        tools=tools,
        system_prompt_sections=[
            SystemPromptSection(
                content=system_prompt,
                cache_scope=None,
                name="subagent_prompt",
            ),
        ],
        max_turns=ctx.max_turns,
    )

    # 3. 创建 QueryEngine，传入初始消息
    engine = QueryEngine(
        config=engine_config,
        initial_messages=list(ctx.initial_messages),
    )

    # 4. 写入初始 transcript
    last_uuid = record_sidechain_transcript(
        list(ctx.initial_messages),
        ctx.agent_id,
    )

    # 5. 构建循环级配置快照
    query_config = build_query_config(session_id=ctx.agent_id)

    logger.info(
        "启动子代理 %s (type=%s, model=%s, tools=%d, depth=%d, max_turns=%s)",
        ctx.agent_id,
        ctx.agent_def.agent_type,
        ctx.model,
        len(tools),
        ctx.depth,
        ctx.max_turns,
    )

    # 6. 运行 query_loop，yield 消息
    try:
        async for event in query_loop(engine, query_config):
            # 检查 abort_event
            if ctx.abort_event is not None and ctx.abort_event.is_set():
                logger.info("子代理 %s 被 abort 中断", ctx.agent_id)
                yield {"role": "assistant", "content": "[Subagent aborted]"}
                break

            # 只 yield dict 类型的消息（跳过 StreamEvent）
            if isinstance(event, dict):
                # 增量写入 transcript
                last_uuid = record_sidechain_transcript(
                    [event], ctx.agent_id, last_uuid,
                )
                yield event

                # 在 tool 轮次边界 drain pending_messages
                if event.get("role") == "tool":
                    drained = _drain_pending_messages(ctx)
                    for msg in drained:
                        # 把 pending 消息追加到引擎消息
                        engine.mutable_messages.append(
                            {"role": "user", "content": msg}
                        )
                        # 也写入 transcript
                        last_uuid = record_sidechain_transcript(
                            [{"role": "user", "content": msg}],
                            ctx.agent_id, last_uuid,
                        )
                        yield {"role": "user", "content": msg}

    except Exception as e:
        logger.exception("子代理 %s 执行异常: %s", ctx.agent_id, e)
        yield {
            "role": "assistant",
            "content": f"Subagent error: {e}",
        }
    finally:
        # 7. 清理资源
        ctx.initial_messages.clear()
        logger.info("子代理 %s 执行结束", ctx.agent_id)


# ---------------------------------------------------------------------------
# _drain_pending_messages — 取出并清空 pending 消息队列
# ---------------------------------------------------------------------------


def _drain_pending_messages(ctx: SubagentContext) -> list[str]:
    """原子地取出并清空 pending_messages 队列。

    SendMessage 续接正在运行的子代理时，消息入队 pending_messages，
    在 tool 轮次边界（tool result 之后）取出注入对话。
    """
    if not ctx.pending_messages:
        return []
    drained = list(ctx.pending_messages)
    ctx.pending_messages.clear()
    return drained
