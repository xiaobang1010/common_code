"""斜杠命令端点 — 前端 /name 输入的解析与技能触发。

响应结构迁就前端 useChatStore 的既有消费协议（按 data.is_skill 分支）：
技能命中 {is_skill, skill_name, skill_prompt}，命令/未命中 {output}。
"""

from __future__ import annotations

from fastapi import APIRouter

from server import state as server_state
from server.paths import project_root
from tools.commands.commands import find_command, try_resolve_skill
from tools.commands.commands_context import CommandContext

router = APIRouter()


def _build_context(args: str) -> CommandContext:
    """为命令 handler 构造最小可用上下文：HTTP 侧无 REPL/消息历史。"""
    return CommandContext(
        messages=[],
        app_state=getattr(server_state, "app_state", None),
        config=None,
        compact_fn=None,
        repl=None,
        project_root=project_root(),
        args=args,
    )


@router.post("/api/command")
async def run_command(body: dict) -> dict:
    """解析前端斜杠输入，命令优先、技能兜底。

    请求体兼容前端实际传参 {command: "<完整输入串>"}（含前导 / 与参数）。
    """
    raw = (body.get("command") or "").strip()
    if not raw:
        return {"output": "空命令"}
    text = raw[1:] if raw.startswith("/") else raw
    name, _, args = text.partition(" ")
    name = name.strip()
    args = args.strip()

    cmd = find_command(name)
    if cmd is not None:
        try:
            output = await cmd.handler(_build_context(args))
        except Exception as exc:  # handler 异常不炸端点，降级为文本输出
            output = f"命令执行失败：{exc}"
        return {"output": output}

    skill_msg = try_resolve_skill(name, args)
    if skill_msg is not None:
        # 渐进披露：不内联技能正文（user 消息通道对模型约束力弱），返回重写
        # 提示让模型主动调用 Skill 工具取正文——tool 结果通道的指令权威性高
        from tools.skills.bundled import find_skill_by_name

        skill = find_skill_by_name(name)
        if skill is not None and not skill.is_model_invocable():
            # disable-model-invocation 技能：模型不能调 Skill 工具，回退内联
            # 正文（恒含 ## 用户任务 段，供前端历史解析识别）
            return {
                "is_skill": True,
                "skill_name": name,
                "skill_prompt": skill_msg["content"].replace(
                    "</system-reminder>",
                    f"## 用户任务\n{args}\n\n</system-reminder>",
                    1,
                ),
            }

        lines = [
            f"Use the skill named `{name}` for this turn.",
            f'First call the `Skill` tool with skill="{name}" before doing the task.',
            "After the skill content is loaded, follow its instructions and continue.",
            "",
            f"User request: {args}",
        ]
        if not args:
            lines.insert(
                4,
                "User request is empty: ask the user what they want to do first; do NOT invent a task.",
            )
        return {
            "is_skill": True,
            "skill_name": name,
            "skill_prompt": "\n".join(lines),
        }

    return {"output": f"未知命令：/{name}，输入 /help 查看可用命令"}
