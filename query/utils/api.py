"""API 工具函数。"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from query.utils.messages import sanitize_dangling_tool_calls
from tools.utils.schema import tool_to_openai_schema

if TYPE_CHECKING:
    from tools.protocol import Tool


# ---------------------------------------------------------------------------
# 易变上下文注入（保前缀缓存的稳定落点）
# ---------------------------------------------------------------------------

def insert_message_before_last_user(messages: list[dict], message: dict) -> list[dict]:
    """把一条易变注入消息放到「最新消息区」的稳定落点，保护历史前缀缓存。

    落点规则（三分支）：
    1. 最后一条 user 消息之后不存在 tool 结果或带 tool_calls 的 assistant
       消息（本轮尚未进入工具续写）：插入到该 user 消息之前——提醒紧贴
       用户问题，且不拆散已缓存的历史前缀；
    2. 处于工具续写轮（末尾是本轮刚产生的工具流量）：追加到列表末尾——
       若插进历史中段的 user 消息前，会把本轮工具流量排除在稳定前缀外、
       每轮重算；
    3. 无 user 消息：兜底追加到末尾。

    自动前缀缓存的服务端（OpenAI 兼容协议）按「从头逐 token 匹配」命中，
    每轮都变的注入内容放在头部会击穿全部历史，移到上述落点后历史前缀稳定。
    """
    last_user_idx: int | None = None
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            last_user_idx = i
            break

    if last_user_idx is None:
        # 分支 3：无 user 消息，兜底追加末尾
        return [*messages, message]

    # 分支 2：最后一条 user 之后已有本轮工具流量，追加末尾
    for m in messages[last_user_idx + 1:]:
        if m.get("role") == "tool" or (
            m.get("role") == "assistant" and m.get("tool_calls")
        ):
            return [*messages, message]

    # 分支 1：插到最后一条 user 消息之前
    return [*messages[:last_user_idx], message, *messages[last_user_idx:]]


def inject_context_before_last_user(
    messages: list[dict], context: dict[str, str] | None
) -> list[dict]:
    """注入 userContext（记忆召回等易变内容）到稳定落点。

    接受字典形式的上下文，每个键值对转换为 `# {key}\\n{value}` 分段，
    多段之间用换行连接，再包装为 system-reminder 格式的 user 消息，
    按 `insert_message_before_last_user` 的落点规则插入。
    """
    # 空字典或 None 时直接返回原消息列表，不做处理
    if not context:
        return messages

    # 把字典转成 # key\nvalue 分段文本，多段用换行连接
    context_text = "\n".join(f"# {key}\n{value}" for key, value in context.items())

    context_message = {
        "role": "user",
        "content": (
            "<system-reminder>\n"
            "As you answer the user's questions, you can use the following context:\n"
            f"{context_text}\n"
            "\n"
            "IMPORTANT: this context may or may not be relevant to your tasks. "
            "You should not respond to this context unless it is highly relevant to your task.\n"
            "</system-reminder>\n"
        ),
    }
    return insert_message_before_last_user(messages, context_message)


# ---------------------------------------------------------------------------
# append_system_context
# ---------------------------------------------------------------------------

def append_system_context(messages: list[dict], context: dict[str, str] | None) -> list[dict]:
    """在消息列表后追加系统上下文。

    接受字典形式的上下文，每个键值对转换为 `{key}: {value}` 形式，
    多段之间用换行连接，作为 system 消息追加到消息列表末尾。
    """
    # 空字典或 None 时直接返回原消息列表，不做处理
    if not context:
        return messages

    # 把字典转成 key: value 形式文本，多段用换行连接
    context_text = "\n".join(f"{key}: {value}" for key, value in context.items())
    return [*messages, {"role": "system", "content": context_text}]


# ---------------------------------------------------------------------------
# tool_to_api_schema
# ---------------------------------------------------------------------------

def tool_to_api_schema(tool: Tool) -> dict:
    """将 Tool 转换为 OpenAI function calling schema。

    委托给 utils/schema.py 的 tool_to_openai_schema。
    """
    return tool_to_openai_schema(tool)


# ---------------------------------------------------------------------------
# build_api_request
# ---------------------------------------------------------------------------

def build_api_request(
    messages: list[dict],
    system_prompt: list[dict],
    tools: list[Tool],
    model: str,
    *,
    stream: bool = True,
    max_tokens: int = 8192,
    temperature: float = 1.0,
    **kwargs,
) -> dict:
    """构建完整的 OpenAI API 请求体。

    Args:
        messages: user/assistant/tool 消息列表
        system_prompt: system 消息列表（来自 build_system_messages）
        tools: Tool 对象列表
        model: 模型名
        stream: 是否流式（默认 True）
        max_tokens: 最大输出 token
        temperature: 温度
        **kwargs: 额外参数直接传入请求体
    """
    # 悬空 tool_calls 清洗：主对话与子代理的每轮请求都经此函数，
    # 在收口处保证「assistant(tool_calls) → tool 结果」序列合法，
    # 中断/输出超限恢复留下的残缺历史不会原样发给模型
    # （压缩等内部直调 query_model_with_streaming 的路径不经过这里，
    # 其失败由 query_loop 的 try/except 兜底）
    sanitized = sanitize_dangling_tool_calls(messages)

    # system 消息放在最前，然后是 user/assistant/tool 消息
    all_messages = [*system_prompt, *sanitized]

    # 转换工具 schema
    tool_schemas = [tool_to_api_schema(t) for t in tools] if tools else []

    request: dict = {
        "model": model,
        "messages": all_messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": stream,
    }

    if tool_schemas:
        request["tools"] = tool_schemas

    request.update(kwargs)
    return request
