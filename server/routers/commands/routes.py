"""斜杠命令端点 — 前端 /name 输入的解析与技能触发。

响应结构迁就前端 useChatStore 的既有消费协议（按 data.is_skill 分支）：
技能命中 {is_skill, skill_name, skill_prompt}，命令/未命中 {output}。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter

from query.utils.messages import sanitize_dangling_tool_calls
from server import state as server_state
from server.paths import project_root
from tools.commands.commands import find_command, try_resolve_skill
from tools.commands.commands_context import CommandContext

logger = logging.getLogger(__name__)

router = APIRouter()


def _resolve_view_engine_messages() -> list:
    """当前查看会话的引擎消息列表（真实引用）。

    与 /api/state 同口径：查看会话有运行中的后台任务时优先取任务引擎，
    否则取全局查看视图引擎。返回的是引擎内部列表引用，
    命令对其原地修改（clear/extend）会直接反映到会话。
    """
    engine = server_state.engine
    view_session = server_state.engine_session_id
    run = server_state.running_runs.get(view_session) if view_session else None
    if run is not None and not run.finished.is_set():
        engine = run.engine
    return engine.mutable_messages if engine is not None else []


def _build_context(args: str, *, for_compact: bool = False) -> CommandContext:
    """为命令 handler 构造上下文。

    默认最小可用上下文（HTTP 侧无 REPL/消息历史），保持各命令既有行为。
    仅 compact 命令注入真实消息引用与压缩函数，让 /compact 在 HTTP 链路
    上真实执行（cmd_compact 对 messages 原地 clear/extend，直接作用于引擎）；
    其余命令（含 /clear）不注入，避免连带改变其行为。
    """
    messages: list = []
    compact_fn = None
    if for_compact:
        messages = _resolve_view_engine_messages()
        from query.services.compact.auto_compact import compact_conversation

        compact_fn = compact_conversation

    return CommandContext(
        messages=messages,
        app_state=getattr(server_state, "app_state", None),
        config=None,
        compact_fn=compact_fn,
        repl=None,
        project_root=project_root(),
        args=args,
    )


def _persist_compacted_messages() -> None:
    """compact 成功后把压缩结果写回会话存储。

    引擎消息被原地压缩后若不落库，下一轮 /api/chat 的历史前缀取自 DB，
    会把未压缩历史整体回灌，压缩白做。运行中任务的引擎由其收尾统一落库
    （收尾保存的就是被原地压缩后的列表），此处只处理空闲查看引擎的场景。
    """
    view_session = server_state.engine_session_id
    run = server_state.running_runs.get(view_session) if view_session else None
    if run is not None and not run.finished.is_set():
        return
    store = getattr(server_state, "session_store", None)
    engine = server_state.engine
    if store is None or engine is None or not view_session:
        return
    try:
        # 与 chat 链路收尾同口径：入库前清洗悬空 tool_calls
        store.save_messages(
            view_session, sanitize_dangling_tool_calls(engine.mutable_messages)
        )
    except Exception:
        logger.warning("compact 结果落库失败（内存视图已压缩，重启后回退 DB 旧历史）", exc_info=True)


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
        # 仅 compact 注入引擎真实消息与压缩函数，其余命令维持最小上下文
        for_compact = name == "compact"
        try:
            output = await cmd.handler(_build_context(args, for_compact=for_compact))
        except Exception as exc:  # handler 异常不炸端点，降级为文本输出
            output = f"命令执行失败：{exc}"
        # 压缩成功即落库，防止下一轮从 DB 回灌未压缩历史
        if for_compact and output.startswith("Conversation compacted"):
            _persist_compacted_messages()
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

        # 重写提示形状被两侧消费：前端 skillParse.ts 解析历史、
        # useChatStore.editAndResend 编辑重发时按此形状重组，改形状需三处同步
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
