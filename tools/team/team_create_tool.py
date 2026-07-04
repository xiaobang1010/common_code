"""TeamCreate 工具 — 创建代理团队。

团队配置持久化到 ~/.agent/teams/{team}/config.json，
团队与共享任务列表 1:1 对应。
"""

from __future__ import annotations

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, build_tool


# ---------------------------------------------------------------------------
# 输入模型
# ---------------------------------------------------------------------------


class TeamCreateInput(BaseModel):
    """TeamCreate 工具输入。

    Attributes:
        team_name: 团队名
        mode: 部署模式（当前仅 "in-process"）
    """

    team_name: str
    mode: str = "in-process"


# ---------------------------------------------------------------------------
# 工具描述
# ---------------------------------------------------------------------------


TEAM_CREATE_PROMPT = """\
创建一个代理团队，用于多代理协作。

使用说明：
- team_name 是团队名称
- 创建后会建立团队配置目录和共享任务列表
- 然后可以用 Agent 工具带 team_name + name 参数派生 teammate
- 用 TaskCreate 创建任务并分配给 teammate
- 用 SendMessage 在代理间通信
- 一个 leader 只能管理一个团队，解散后才能创建新的
"""


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


async def _execute(inp: TeamCreateInput, _context: ToolUseContext) -> ToolResult:
    """执行团队创建。"""
    from tools.team.manager import create_team

    try:
        result = create_team(inp.team_name, inp.mode)
        return ToolResult(
            content=(
                f"Team '{inp.team_name}' created successfully. "
                f"Use Agent tool with team_name + name to spawn teammates. "
                f"Use TaskCreate to create and assign tasks."
            ),
            metadata=result,
        )
    except ValueError as e:
        return ToolResult(
            content=str(e),
            is_error=True,
        )


# ---------------------------------------------------------------------------
# 工厂函数
# ---------------------------------------------------------------------------


def get_team_create_tool() -> Tool:
    """返回 TeamCreateTool 实例。"""
    return build_tool(
        name="TeamCreate",
        description="Create a team for multi-agent collaboration",
        input_schema=TeamCreateInput,
        execute=_execute,
        prompt=TEAM_CREATE_PROMPT,
        is_read_only=True,
    )
