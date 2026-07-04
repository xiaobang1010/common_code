"""子代理运行器 — run_agent async generator。

组装子代理的初始消息、工具池、系统提示词，调用 query_loop 运行子代理循环，
yield 每条消息，finally 清理资源。

子代理复用同一 query_loop 引擎，但拥有独立的消息历史、工具池、系统提示词。
普通子代理不继承父对话（从零开始），结果只回传最终 assistant 文本。
"""

from __future__ import annotations

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
    1. 构建 QueryEngineConfig（覆盖 model/tools/system_prompt_sections/max_turns）
    2. 创建 QueryEngine，传入 initial_messages
    3. 调 query_loop，yield 每条消息
    4. finally 清理资源

    子代理用独立的 QueryEngine 实例，消息历史隔离。
    系统提示词用 agent_def 的，覆盖默认的 get_system_prompt_sections()。

    Args:
        ctx: 子代理执行上下文（含 initial_messages、model、agent_def）
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

    # 1. 构建 QueryEngineConfig
    # 用 build_engine_config 获取默认配置，然后覆盖关键字段
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

    # 2. 创建 QueryEngine，传入初始消息
    engine = QueryEngine(
        config=engine_config,
        initial_messages=list(ctx.initial_messages),
    )

    # 3. 构建循环级配置快照
    query_config = build_query_config(session_id=ctx.agent_id)

    logger.info(
        "启动子代理 %s (type=%s, model=%s, tools=%d, depth=%d)",
        ctx.agent_id,
        ctx.agent_def.agent_type,
        ctx.model,
        len(tools),
        ctx.depth,
    )

    # 4. 运行 query_loop，yield 消息
    try:
        async for event in query_loop(engine, query_config):
            # 只 yield dict 类型的消息（跳过 StreamEvent）
            if isinstance(event, dict):
                yield event
    except Exception as e:
        logger.exception("子代理 %s 执行异常: %s", ctx.agent_id, e)
        # 产出一条错误消息
        yield {
            "role": "assistant",
            "content": f"Subagent error: {e}",
        }
    finally:
        # 5. 清理资源
        # 清空初始消息引用（释放内存）
        ctx.initial_messages.clear()
        logger.info("子代理 %s 执行结束", ctx.agent_id)
