"""SummarizeTeam 工具 — 综合所有 teammate 的结果生成总结。

当所有任务完成后，leader 可调用此工具收集所有 teammate 的最终 assistant 文本，
拼接成 prompt 让 LLM 生成总结，返回总结文本。
"""

from __future__ import annotations

import logging

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------


class SummarizeTeamInput(BaseModel):
    """SummarizeTeam 工具输入。

    Attributes:
        team_name: 团队名
    """

    team_name: str


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------


SUMMARIZE_TEAM_PROMPT = """\
综合团队所有 teammate 的工作结果，生成总结。

使用说明：
- team_name 是要总结的团队名
- 工具会从所有 teammate 的 transcript 中收集最终输出
- 返回一个综合总结，涵盖所有 teammate 的工作成果
- 通常在所有任务完成后调用
"""


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


async def _execute(inp: SummarizeTeamInput, _ctx: ToolUseContext) -> ToolResult:
    """收集所有 teammate 的结果并生成总结。"""
    from tools.team.manager import get_members, team_exists
    from tools.team.task_tools import _list_all_tasks

    if not team_exists(inp.team_name):
        return ToolResult(
            content=f"Team not found: {inp.team_name}",
            is_error=True,
        )

    members = get_members(inp.team_name)
    if not members:
        return ToolResult(content="No teammates in this team.")

    # 收集每个 teammate 的最终输出（从 transcript 读取）
    from tools.subagent.transcript import get_agent_transcript

    summaries: list[str] = []
    for member in members:
        agent_id = member.get("agent_id", "")
        if not agent_id:
            continue

        transcript = get_agent_transcript(agent_id)
        if transcript is None:
            summaries.append(f"### {member['name']}\n(No transcript found)")
            continue

        # 取最后一条 assistant 消息
        final_text = ""
        for msg in reversed(transcript):
            if msg.get("role") == "assistant" and msg.get("content", "").strip():
                final_text = msg["content"]
                break

        summaries.append(f"### {member['name']}\n{final_text or '(No output)'}")

    # 拼接所有结果
    combined = "\n\n---\n\n".join(summaries)

    # 调 LLM 生成总结
    from query.services.api.llm import query_model_with_streaming

    prompt = (
        "You are summarizing the work of multiple teammates. "
        "Here are their individual results:\n\n"
        f"{combined}\n\n"
        "Please provide a concise summary of what was accomplished."
    )

    summary_parts: list[str] = []
    async for event in query_model_with_streaming(
        messages=[
            {"role": "system", "content": "You are a helpful assistant that summarizes team work."},
            {"role": "user", "content": prompt},
        ],
    ):
        if event.type == "content" and event.content:
            summary_parts.append(event.content)
        elif event.type == "error":
            return ToolResult(
                content=f"Failed to generate summary: {event.content or event.error}",
                is_error=True,
            )

    summary = "".join(summary_parts) or "No summary generated."

    return ToolResult(
        content=summary,
        metadata={"team_name": inp.team_name, "members_summarized": len(members)},
    )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_summarize_team_tool() -> Tool:
    """返回 SummarizeTeam 工具实例。"""
    return build_tool(
        name="SummarizeTeam",
        description="Summarize all teammates' results for a team",
        input_schema=SummarizeTeamInput,
        execute=_execute,
        prompt=SUMMARIZE_TEAM_PROMPT,
        is_read_only=True,
    )
