"""Agentic 循环引擎核心。

Agentic 循环引擎核心。

核心查询入口和 while(true) 无限循环，
每轮迭代执行：压缩 → 构建请求 → 调用模型 → 流式输出 →
工具调用 → 追加结果 → 错误恢复 → 完成检查 → 状态转换。

三层结构：
  - QueryEngine：会话级状态（消息历史、token 用量、轮次）
  - QueryConfig：循环级快照（session_id、auto_compact_enabled 等）
  - QueryDeps：I/O 依赖（call_model、microcompact、autocompact 等）
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import asdict, dataclass, field, replace
from typing import TYPE_CHECKING, Any, AsyncGenerator

from query.config import QueryConfig, build_query_config
from query.deps import QueryDeps
from query.stop_hooks import run_stop_hooks
from query.services.api.errors import APIError, classify_error, is_recoverable_error
from query.services.api.llm import StreamEvent, collect_tool_calls
from query.services.compact.auto_compact import CompactTracking
from query.utils.messages import get_messages_after_compact_boundary
from tools.executor import (
    StreamingToolExecutor,
    ToolExecutionResult,
    tool_result_to_openai_message,
)
from tools import get_tools
from tools.subagent.tools import is_subagent_context
from query.utils.api import (
    build_api_request,
    inject_context_before_last_user,
    insert_message_before_last_user,
    append_system_context,
)
from query.utils.reasoning import deep_merge_patch, resolve_reasoning_patch

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from query.engine import QueryEngine


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# max_output_tokens 恢复重试上限
MAX_OUTPUT_TOKENS_RECOVERY_LIMIT = 3

# 上下文长度超限时的错误关键词
_CONTEXT_LENGTH_KEYWORDS = ("context_length", "maximum context length", "prompt too long")


def _project_wing_name() -> str:
    """项目 wing 标识：basename:sha1(绝对路径)[:12]。

    用路径哈希保证同名不同路径的项目互不串味，显示名保留 basename。
    注入/摄取/中途召回统一走这个命名，口径一致。
    根目录统一读 server.paths.project_root()（工作区切换后已更新），
    不使用进程 cwd（切换后不更新，会导致记忆归属错误的工作区）。
    """
    from server.paths import effective_root

    root = effective_root()
    name = os.path.basename(root)
    digest = hashlib.sha1(os.path.abspath(root).encode("utf-8")).hexdigest()[:12]
    return f"{name}:{digest}"


# git 分支缓存：workspace 路径 → 分支（切换工作区后按新路径重新读取）
_branch_cache: dict[str, str] = {}


def _current_git_branch(root: str) -> str:
    """读取工作区当前 git 分支，失败返回空串；按工作区缓存避免每轮跑 git。"""
    cached = _branch_cache.get(root)
    if cached is not None:
        return cached
    branch = ""
    try:
        import subprocess

        result = subprocess.run(
            ["git", "-C", root, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if result.returncode == 0:
            branch = result.stdout.strip()
    except Exception:
        branch = ""
    _branch_cache[root] = branch
    return branch


def _build_project_info() -> str:
    """构造注入系统提示词的工作区信息段。

    让 agent 明确知晓当前工作区与访问边界（工作区根、git 分支、
    额外允许目录），不必靠 pwd/试错推断；随会话动态构建。
    以 <user_info> 块承载：可视化规范段引用其中的 IDE Theme 字段决定图形配色，
    主题作为运行时事实注入（当前应用仅提供深色界面，接入浅色主题后此处跟随设置）。
    """
    from server.paths import effective_root

    root = effective_root()
    lines = [f"当前工作区根目录: {root}"]
    branch = _current_git_branch(root)
    if branch:
        lines.append(f"当前 Git 分支: {branch}")
    # 额外允许目录（additional_directories 多源合并结果）
    try:
        from pathlib import Path as _Path
        from startup.config import get_initial_settings

        additional = get_initial_settings(_Path(root)).permissions.additional_directories
        if additional:
            lines.append(f"额外允许目录: {', '.join(additional)}")
    except Exception:
        pass  # 配置读取失败不影响注入
    lines.insert(0, "IDE Theme: dark")
    return "<user_info>\n" + "\n".join(lines) + "\n</user_info>"


# ---------------------------------------------------------------------------
# LoopResult — query_loop 退出结果
# ---------------------------------------------------------------------------


@dataclass
class LoopResult:
    """query_loop 退出结果，标明退出原因。

    Attributes:
        reason: 退出原因
            - "completed": 正常完成（finish_reason=stop 或无工具调用）
            - "prompt_too_long": 上下文超限且恢复失败
            - "model_error": 模型调用异常（不可恢复错误、流异常）
            - "max_output_tokens_exhausted": 输出 token 恢复次数用尽
        error: 异常对象（reason 为错误类时携带）
    """

    reason: str
    error: Exception | None = None


# ---------------------------------------------------------------------------
# State — 循环内临时状态
# ---------------------------------------------------------------------------


@dataclass
class State:
    """循环内临时状态，每轮迭代可能重建。

    会话级状态（messages、total_usage、turn_count）已迁移到 QueryEngine，
    这里只保留循环内临时状态。

    每次循环迭代开始时解构，continue 时整体赋值：
      state = State(**{**asdict(state), **updates})

    Attributes:
        transition: 转换原因（防止死循环）
        error_count: 错误计数
        withheld_messages: 被暂扣的消息（可恢复错误恢复前暂不输出）
        max_output_tokens_recovery_count: max_output_tokens 恢复计数
        has_attempted_reactive_compact: 是否已尝试响应式压缩
        auto_compact_tracking: 自动压缩追踪状态
    """

    transition: str | None = None
    error_count: int = 0
    withheld_messages: list[dict] = field(default_factory=list)
    max_output_tokens_recovery_count: int = 0
    has_attempted_reactive_compact: bool = False
    auto_compact_tracking: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# _is_context_length_error — 判断是否为上下文长度错误
# ---------------------------------------------------------------------------


def _is_context_length_error(error: Exception | APIError) -> bool:
    """判断错误是否为上下文长度超限。"""
    msg = str(error).lower()
    return any(kw in msg for kw in _CONTEXT_LENGTH_KEYWORDS)


# ---------------------------------------------------------------------------
# _is_max_output_tokens_event — 判断是否为 max_output_tokens 事件
# ---------------------------------------------------------------------------


def _is_max_output_tokens_event(event: StreamEvent) -> bool:
    """判断流式事件是否表示输出 token 超限。"""
    return (
        event.type == "done"
        and event.finish_reason == "length"
    )


# ---------------------------------------------------------------------------
# _build_tool_result_messages — 将工具执行结果转为消息
# ---------------------------------------------------------------------------


def _build_tool_result_messages(
    results: list[ToolExecutionResult],
) -> list[dict]:
    """将工具执行结果列表转换为 OpenAI 格式消息。"""
    messages: list[dict] = []
    for result in results:
        msg = tool_result_to_openai_message(result)
        messages.append(msg)
    return messages


def _present_files_event(result: ToolExecutionResult) -> dict[str, Any] | None:
    """present_files 交付结果 → 前端结构化事件；其他工具返回 None。

    该事件只随 SSE 发给前端（打开面板标签），不进入对话历史、不落库。
    """
    if result.tool_name != "present_files" or result.is_error:
        return None
    meta = result.metadata or {}
    if meta.get("type") != "present_files":
        return None
    return {
        "role": "present_files",
        "files": meta.get("files", []),
        "explanation": meta.get("explanation", ""),
    }


# ---------------------------------------------------------------------------
# _build_assistant_message — 从流式事件构建 assistant 消息
# ---------------------------------------------------------------------------


def _build_assistant_message(
    content_parts: list[str],
    tool_calls: list[dict],
    reasoning_parts: list[str] | None = None,
    reasoning_first_ts: float | None = None,
    reasoning_last_ts: float | None = None,
    context_usage: int | None = None,
) -> dict:
    """从流式事件中收集的内容和工具调用构建 assistant 消息。"""
    content = "".join(content_parts) if content_parts else ""
    msg: dict[str, Any] = {"role": "assistant", "content": content, "_ts": time.time() * 1000}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    # 上下文计数基线：本次请求的真实总输入（含缓存读写），
    # 供 count_context_tokens 反向扫描取基线；发给模型前随下划线字段一并剥离
    if context_usage:
        msg["_context_usage"] = int(context_usage)
    # 思维链：有思考输出时写入下划线内部字段（与 _ts 同约定，发给模型前会被剥离，
    # 随会话整表 JSON 落库），供前端历史恢复重建「思考 · X秒」行
    reasoning = "".join(reasoning_parts) if reasoning_parts else ""
    if reasoning and reasoning_first_ts is not None and reasoning_last_ts is not None:
        msg["_reasoning"] = reasoning
        msg["_reasoning_ms"] = int(reasoning_last_ts - reasoning_first_ts)
    return msg


# ---------------------------------------------------------------------------
# _mine_conversation_to_palace - 会话结束自动摄取入 Palace
# ---------------------------------------------------------------------------


async def _mine_conversation_to_palace(engine: QueryEngine) -> None:
    """会话结束自动摄取入 Palace。"""
    try:
        from query.services.memory.registry import get_active_memory
        memory = get_active_memory()
        if memory is not None and hasattr(memory, 'mine_conversation'):
            project_name = _project_wing_name()
            # Convert messages to the format ConversationMiner expects
            convo_messages = []
            for msg in engine.mutable_messages:
                role = msg.get("role", "")
                content = msg.get("content", "")
                if role in ("user", "assistant") and content:
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    convo_messages.append({"role": role, "content": content})
            if convo_messages:
                await memory.mine_conversation(convo_messages, wing=project_name, session_id="loop_session")
    except Exception:
        pass  # 摄取失败不中断


# ---------------------------------------------------------------------------
# _run_inline_compression — 内联两级压缩管线
# ---------------------------------------------------------------------------


def _compact_transcript_path(engine: Any, tool_use_context: Any) -> str:
    """解析压缩续写消息引用的转录逃生门路径。

    子代理回合复用其既有 sidechain 转录（~/.agent/subagents/<agent_id>/transcript.jsonl，
    只引用不新建）；主会话指向回合收尾导出的会话转录
    （~/.agent/transcripts/<session_id>.jsonl）。
    """
    from pathlib import Path

    home = Path(os.path.expanduser("~"))
    if tool_use_context is not None and getattr(tool_use_context, "tool_use_id", ""):
        return str(
            home / ".agent" / "subagents" / tool_use_context.tool_use_id / "transcript.jsonl"
        )
    return str(home / ".agent" / "transcripts" / f"{engine.session_id}.jsonl")


async def _run_inline_compression(
    messages: list[dict],
    model: str,
    tracking: CompactTracking,
    deps: Any,
    transcript_path: str | None = None,
) -> Any:
    """内联两级压缩管线（microcompact → autocompact），异步生成器。

    yield 两种对象：StreamEvent（压缩进行中/完成/失败事件，供上层转发前端）
    与最终消息列表（最后一个产出）。snip 与 context_collapse 实验层已下线；
    autocompact 内部自判阈值，管线层面不做提前返回。

    Args:
        messages: 消息列表（全量历史）
        model: 模型名称
        tracking: 压缩追踪状态（会话级）
        deps: I/O 依赖（取 microcompact、autocompact）
        transcript_path: 压缩续写消息引用的转录逃生门路径
    """
    from query.services.api.llm import StreamEvent as _SE
    from query.services.compact.auto_compact import (
        STATUS_COMPACTED,
        STATUS_FAILED,
        STATUS_BREAKER,
        STATUS_SKIPPED_RAPID_REFILL,
        should_auto_compact,
    )
    from query.services.compact.micro_compact import should_micro_compact

    # 1b. Microcompact（闲置/水位触发）
    if should_micro_compact(messages, model):
        messages = deps.microcompact(messages=messages, model=model)

    # 1d. Autocompact：先经判定门，放行时先行进行中标记再执行（LLM 调用耗时可见）
    if should_auto_compact(messages, model, tracking):
        yield _SE(type="compact_started", content="auto")
    messages, result = await deps.autocompact(
        messages=messages,
        model=model,
        tracking=tracking,
        transcript_path=transcript_path,
    )
    if result is not None:
        info = {
            "status": result.status,
            "tokens_before": result.tokens_before,
            "tokens_after": result.tokens_after,
            "reason": result.reason,
        }
        if result.status == STATUS_COMPACTED:
            yield _SE(
                type="compact_completed",
                content=f"{result.tokens_before} -> {result.tokens_after}",
                compact_info=info,
            )
        elif result.status in (
            STATUS_FAILED, STATUS_BREAKER, STATUS_SKIPPED_RAPID_REFILL
        ):
            yield _SE(
                type="compact_failed",
                content=result.reason or result.status,
                compact_info=info,
            )
    yield messages


# ---------------------------------------------------------------------------
# query — 便捷入口
# ---------------------------------------------------------------------------


async def query(
    params: dict[str, Any] | None = None,
) -> AsyncGenerator[StreamEvent | dict, None]:
    """便捷入口，内部创建一次性 QueryEngine。

    query() 是一次性调用，不传 prompt（messages 已包含历史），
    直接调 query_loop。如需跨轮持久化，请使用 QueryEngine.submitMessage。

    Args:
        params: 查询参数，支持以下字段：
            - messages: 初始消息列表
            - config_overrides: QueryEngineConfig 覆盖字段（含 deps）
            - user_context: 用户上下文字典，由调用方构建（dict[str, str] | None）
            - system_context: 系统上下文字典，由调用方构建（dict[str, str] | None）

    Yields:
        StreamEvent | dict: 流式事件或结果消息
    """
    # 延迟 import 避免循环依赖
    from query.engine import build_engine_config, QueryEngine

    if params is None:
        params = {}

    messages = params.get("messages", [])
    config_overrides = params.get("config_overrides", {})
    user_context = params.get("user_context")
    system_context = params.get("system_context")

    engine_config = build_engine_config(**config_overrides)
    engine = QueryEngine(engine_config, initial_messages=messages)

    # query() 是一次性调用，不需要 prompt（messages 已包含历史）
    # 直接调 query_loop
    query_config = build_query_config(session_id=engine.deps.get_uuid())
    async for event in query_loop(engine, query_config, user_context, system_context):
        yield event


# ---------------------------------------------------------------------------
# query_loop — while(true) 无限循环
# ---------------------------------------------------------------------------


async def query_loop(
    engine: QueryEngine,
    config: QueryConfig,
    user_context: dict[str, str] | None = None,
    system_context: dict[str, str] | None = None,
    tool_use_context: "ToolUseContext | None" = None,
) -> AsyncGenerator[StreamEvent | dict, None]:
    """Agentic 循环核心。

    while(true) 无限循环，每轮迭代：
    1. 压缩管线
    2. 注入用户/系统上下文（由调用方传入）
    3. 构建 API 请求
    4. 调用模型（流式）
    5. 流式输出
    6. 检测工具调用
    7. 错误恢复
    8. 完成检查
    9. 执行工具
    10. 追加结果到消息列表
    11. 状态转换

    会话级状态（messages、total_usage）从 engine 读写，
    循环级临时状态从 state 读写，I/O 依赖从 engine.deps 获取。

    Args:
        engine: 查询引擎，持有会话状态和会话级配置
        config: 循环级配置快照（auto_compact_enabled 等）
        user_context: 用户上下文字典，由调用方构建
        system_context: 系统上下文字典，由调用方构建
        tool_use_context: 调用方的工具执行上下文（子代理场景传入，
            使每轮工具执行的 tool_use_id 继承子代理标识，供上下文判定与工具裁剪）

    Yields:
        StreamEvent | dict: 流式事件或结果消息
    """
    deps = engine.deps
    engine_config = engine.config

    # 压缩追踪：会话级状态（挂 engine），跨回合保留冷却基线与计数快照
    tracking: CompactTracking = engine.compact_tracking

    # 压缩逃生门转录路径（主会话 transcripts / 子代理 sidechain）
    transcript_path = _compact_transcript_path(engine, tool_use_context)

    # 循环内临时状态
    state = State()

    # skill 列表增量注入追踪（跨轮保持，避免重复注入）
    sent_skills: set[str] = set()

    # 循环内轮次计数（max_turns 检查用；不依赖 submitMessage 收尾才递增的引擎计数）
    loop_turns = 0

    # 首轮记忆注入：若有启用的记忆插件，注入 L0+L1 上下文或历史记忆。
    # 首轮判定用「历史仅含当前这条用户消息」：submitMessage 在进循环前已把用户消息
    # 追加进历史，原先的「历史为空」在 UI 路径上永远不成立，导致注入从未执行
    if len(engine.mutable_messages) == 1 and user_context is None:
        try:
            from query.services.memory.registry import get_active_memory
            memory = get_active_memory()
            if memory is not None:
                # 优先使用 MemoryPalaceProvider 的 wake_up（L0+L1 上下文）。
                # wake_up 是同步调用，丢到线程池避免阻塞事件循环（心跳、权限桥都在上面）
                if hasattr(memory, 'wake_up'):
                    project_name = _project_wing_name()
                    wake_up_text = await asyncio.to_thread(
                        memory.wake_up, wing=project_name,
                    )
                    if wake_up_text and wake_up_text.strip():
                        user_context = {"记忆上下文": wake_up_text}
                else:
                    # 降级：使用通用 search 接口
                    results = await memory.search("", limit=3)
                    if results:
                        mem_text = "\n".join(
                            f"- {r.get('content', '')[:200]}" for r in results if r.get("content")
                        )
                        if mem_text:
                            user_context = {"历史记忆": mem_text}
                # 记忆准备完成：通知前端展示「已加载上下文」阶段
                yield StreamEvent(type="phase", content="memory_ready")
        except Exception:
            pass  # 记忆检索失败不中断循环

    # eslint-disable-next-line no-constant-condition
    while True:
        # 从引擎读取当前消息
        messages = engine.mutable_messages
        transition = state.transition

        # ---- 0. maxTurns 检查（循环内计数，子代理直接调 query_loop 也能生效） ----
        if engine_config.max_turns is not None:
            if loop_turns >= engine_config.max_turns:
                # 以 assistant 消息说明停止原因，让子代理结果可读
                yield {
                    "role": "assistant",
                    "content": (
                        f"[已达到轮次上限（max_turns={engine_config.max_turns}），"
                        f"本轮任务提前停止。]"
                    ),
                }
                yield StreamEvent(type="done", finish_reason="stop")
                await _mine_conversation_to_palace(engine)
                yield LoopResult(reason="completed")
                return
        loop_turns += 1

        # ---- 0b. token 预算检查（与轮次上限同款优雅停止模式） ----
        if engine_config.token_budget:
            if engine.total_usage >= engine_config.token_budget:
                yield {
                    "role": "assistant",
                    "content": (
                        f"[已达到 token 预算上限（token_budget="
                        f"{engine_config.token_budget}），本轮任务提前停止。]"
                    ),
                }
                yield StreamEvent(type="done", finish_reason="stop")
                await _mine_conversation_to_palace(engine)
                yield LoopResult(reason="completed")
                return

        # ---- 1. 压缩管线（内联两级）----
        if config.auto_compact_enabled and messages:
            try:
                compacted: list[dict] | None = None
                async for item in _run_inline_compression(
                    messages=messages,
                    model=engine_config.model,
                    tracking=tracking,
                    deps=deps,
                    transcript_path=transcript_path,
                ):
                    if isinstance(item, StreamEvent):
                        yield item
                    else:
                        compacted = item
                if compacted is not None and compacted != messages:
                    # 全量回写引擎；插入语义下新边界+摘要只在压缩发生时增量下发，
                    # 微压缩的原位替换不产消息（避免把旧边界/摘要重复推给订阅者）
                    was_inserted = len(compacted) > len(messages)
                    engine.mutable_messages = compacted
                    messages = compacted
                    if was_inserted:
                        for msg in get_messages_after_compact_boundary(compacted)[:2]:
                            yield msg
            except Exception:
                # 压缩失败不中断循环
                pass

        # ---- 2. 用户/系统上下文（由调用方传入，无需此处获取） ----

        # ---- 2.5 会话中途轻量召回 ----
        # 首轮 wake_up 已注入记忆，后续轮按最近用户输入做一次轻量检索，
        # 分数超阈值才注入少量记忆片段（默认开启，可用 memory.auto_recall 关闭；
        # 阈值按 recall 分数量纲 0.4*bm25+boost 校准，默认 0.3）
        mid_context = user_context
        try:
            if mid_context is None:
                from query.services.memory.registry import get_active_memory
                memory = get_active_memory()
                if memory is not None and hasattr(memory, 'recall'):
                    from startup.config import get_global_config
                    memory_cfg = get_global_config().memory or {}
                    if memory_cfg.get("auto_recall", True):
                        threshold = float(memory_cfg.get("auto_recall_threshold", 0.3))
                        # 取最近一条用户消息作为检索 query
                        user_query = ""
                        for m in reversed(messages):
                            if m.get("role") == "user":
                                user_query = m.get("content", "")
                                break
                        if isinstance(user_query, list):
                            user_query = " ".join(
                                c.get("text", "") for c in user_query if isinstance(c, dict)
                            )
                        if user_query:
                            # recall 是同步查询，丢到线程池避免阻塞事件循环
                            results = await asyncio.to_thread(
                                memory.recall, user_query,
                                wing=_project_wing_name(), n_results=3,
                            )
                            hits = [
                                r for r in results
                                if float(r.get("score", 0.0)) >= threshold
                            ]
                            if hits:
                                snippet = "\n".join(
                                    f"- {r.get('content', '')[:200]}" for r in hits
                                )
                                mid_context = {"相关记忆": snippet}
        except Exception:
            pass  # 中途召回失败不中断循环

        # ---- 3. 构建 API 请求 ----
        from prompts import build_system_messages, get_system_prompt_sections

        if engine_config.system_prompt_sections:
            sections = engine_config.system_prompt_sections
        else:
            # 工作区信息构建含 subprocess（git 分支探测）与配置读取，
            # 丢线程池避免阻塞事件循环（心跳、权限桥都在上面）
            sections = get_system_prompt_sections(
                project_info=await asyncio.to_thread(_build_project_info)
            )
        system_messages = build_system_messages(sections)

        if system_context:
            system_messages = append_system_context(system_messages, system_context)

        # 用户上下文仅临时拼入 api_messages，不污染 messages（messages 会被写回引擎）。
        # 落点用稳定规则（最后一条 user 之前 / 工具续写轮末尾），不再头部插入，
        # 保证自动前缀缓存供应商的历史前缀不被每轮变化的内容击穿
        # 发给模型的只有活跃窗口（最后一个压缩边界起）：插入语义下 messages
        # 是全量历史（含边界前的旧消息与旧摘要），旧消息不应进入请求；
        # 全量列表留在引擎侧供落库与界面回放
        api_messages = get_messages_after_compact_boundary(messages)
        recall_text: str | None = None
        if mid_context:
            api_messages = inject_context_before_last_user(api_messages, mid_context)
            # 分类估算用：与 inject_context_before_last_user 内同样的分段拼接口径
            recall_text = "\n".join(f"# {k}\n{v}" for k, v in mid_context.items())

        # skill 列表增量注入（临时，不写回引擎）
        skill_listing_text: str | None = None
        try:
            from tools.skills.bundled import get_model_invocable_skills
            from tools.skills.listing import get_skill_listing_attachment
            from startup.model.config import get_effective_context_window

            invocable_skills = get_model_invocable_skills()
            if invocable_skills:
                context_window = get_effective_context_window(engine_config.model)
                skill_listing = get_skill_listing_attachment(
                    invocable_skills, sent_skills, context_window,
                )
                if skill_listing is not None:
                    # 与记忆召回同一落点规则：易变清单不进头部，保住历史前缀
                    api_messages = insert_message_before_last_user(
                        api_messages, skill_listing,
                    )
                    content = skill_listing.get("content")
                    if isinstance(content, str):
                        skill_listing_text = content
        except ImportError:
            pass

        request = build_api_request(
            messages=api_messages,
            system_prompt=system_messages,
            tools=engine_config.tools,
            model=engine_config.model,
            max_tokens=engine_config.max_tokens,
            temperature=engine_config.temperature,
        )

        # ---- 3.5 上下文容量分类估算 ----
        # 供前端「上下文容量」面板展示分类占比；估算失败不中断对话
        try:
            from query.services.context_metrics import build_context_breakdown
            yield StreamEvent(
                type="context_breakdown",
                breakdown=build_context_breakdown(
                    sections=sections,
                    tools=engine_config.tools,
                    # 与请求同口径：只统计活跃窗口（最后一个压缩边界起）
                    history_messages=get_messages_after_compact_boundary(messages),
                    skill_listing_text=skill_listing_text,
                    recall_text=recall_text,
                    # 面板"已用/窗口/压缩水位"与触发判定同源（含窗口来源）
                    model=engine_config.model,
                ),
            )
        except Exception:
            logger.debug("context breakdown 估算失败，跳过本次上报", exc_info=True)

        # 创建流式工具执行器
        from tools.protocol import ToolUseContext
        from startup.setup import get_hooks_snapshot
        tool_executor = StreamingToolExecutor(
            tools=engine_config.tools,
            context=ToolUseContext(
                question_callback=engine_config.question_prompt,
                # 子代理场景继承其 tool_use_id（agent_ 前缀），供 is_subagent_context 判定
                tool_use_id=(
                    tool_use_context.tool_use_id
                    if tool_use_context is not None and tool_use_context.tool_use_id
                    else ""
                ),
                # 会话级中断事件（/api/abort 置位），Agent 工具传给前台子代理优雅退出
                abort_controller=engine_config.abort_event,
                # 引擎会话标识：子代理注册表按父会话关联与通知投递
                session_id=getattr(engine, "session_id", ""),
                # 父会话标识必须同样穿过每轮的执行器上下文重建：
                # RespondToCoordinator 靠它定位投递目标（runner 挂在子代理上下文上，
                # 工具实际消费的是这里重建出的执行上下文）
                parent_session_id=(
                    tool_use_context.parent_session_id
                    if tool_use_context is not None
                    else ""
                ),
            ),
            permission_check=engine_config.permission_check,
            permission_prompt=engine_config.permission_prompt,
            always_allowed=engine.always_allowed,
            hook_config=get_hooks_snapshot(),
        )
        tool_result_messages: list[dict] = []

        # ---- 4. 调用模型（流式） ----
        # 阶段事件：请求已构建、即将发起模型调用。与空 content 的流开始信号并列保留：
        # 后者维持流启动语义，本事件承载前端「正在调用模型」文案（每轮循环都会发）
        yield StreamEvent(type="phase", content="model_requested")
        yield StreamEvent(type="content", content="")  # stream_request_start 信号

        content_parts: list[str] = []
        # 思维链累积与计时：起止取事件到达时刻（粗粒度耗时），供 assistant 消息
        # 的 _reasoning/_reasoning_ms 字段与前端「思考 · X秒」显示
        reasoning_parts: list[str] = []
        reasoning_first_ts: float | None = None
        reasoning_last_ts: float | None = None
        stream_events: list[StreamEvent] = []
        finish_reason: str | None = None
        usage_info: dict | None = None
        error_occurred: Exception | None = None
        # 扣留的上下文超限错误事件，恢复完才决定要不要暴露给调用方
        withheld_error: StreamEvent | None = None

        # 实发参数在此收口：推理等级映射求值结果按 API 格式决定注入位置
        # （OpenAI 兼容并入 extra_body，Anthropic 并入顶层 kwargs），
        # 未选等级/无映射时不注入任何推理参数，行为与改造前一致
        call_kwargs: dict[str, Any] = {
            "messages": request["messages"],
            "tools": engine_config.tools,
            "model": engine_config.model,
            "max_tokens": engine_config.max_tokens,
            "temperature": engine_config.temperature,
        }
        try:
            reasoning_patch = resolve_reasoning_patch(
                engine_config.model, engine_config.reasoning_level
            )
            if reasoning_patch:
                from query.services.api.client import get_active_api_format
                if get_active_api_format() == "anthropic":
                    deep_merge_patch(call_kwargs, reasoning_patch)
                else:
                    deep_merge_patch(
                        call_kwargs.setdefault("extra_body", {}), reasoning_patch
                    )
        except Exception:
            logger.warning("推理参数注入失败，按无推理参数请求继续", exc_info=True)

        try:
            async for event in deps.call_model(**call_kwargs):
                # ---- 5. 流式输出 ----
                # 上下文超限错误先扣下，等恢复流程走完再决定是否暴露
                if (
                    event.type == "error"
                    and event.error
                    and _is_context_length_error(event.error)
                ):
                    withheld_error = event
                    stream_events.append(event)
                    error_occurred = event.error
                    continue

                # 非上下文超限的事件正常 yield 和处理
                yield event
                stream_events.append(event)

                if event.type == "tool_call_delta":
                    tool_executor.add_delta(event)

                if event.type == "content" and event.content:
                    content_parts.append(event.content)
                elif event.type == "reasoning" and event.content:
                    # 思维链增量：累积全文，首个记 start、每个刷新 last（耗时 = last - first）
                    reasoning_parts.append(event.content)
                    now_ms = time.time() * 1000
                    if reasoning_first_ts is None:
                        reasoning_first_ts = now_ms
                    reasoning_last_ts = now_ms
                elif event.type == "done" and event.finish_reason:
                    finish_reason = event.finish_reason
                elif event.type == "usage" and event.usage:
                    usage_info = event.usage
                elif event.type == "error" and event.error:
                    error_occurred = event.error

                # 流式期间 yield 已完成的工具结果
                for completed in tool_executor.get_completed_results():
                    tr_msg = tool_result_to_openai_message(completed)
                    yield tr_msg
                    tool_result_messages.append(tr_msg)
                    # Skill/Agent 工具可能返回 new_messages（如 skill 正文），注入对话
                    if completed.new_messages:
                        for nm in completed.new_messages:
                            yield nm
                            tool_result_messages.append(nm)
                    # context_modifier 中的 allowed_tools 注入会话级权限
                    if completed.context_modifier and completed.context_modifier.get("allowed_tools"):
                        for t in completed.context_modifier["allowed_tools"]:
                            engine.always_allowed.add(t)
                    # present_files 交付事件：只发前端，不入对话
                    pf_event = _present_files_event(completed)
                    if pf_event:
                        yield pf_event

        except Exception as e:
            # 模型调用异常
            yield StreamEvent(
                type="error",
                error=e,
                content=str(e),
            )
            # 回滚引擎消息历史
            engine.mutable_messages = messages
            yield LoopResult(reason="model_error", error=e)
            return

        # 流式结束后收尾等待剩余工具
        remaining_results = await tool_executor.get_remaining_results()
        for result in remaining_results:
            tr_msg = tool_result_to_openai_message(result)
            yield tr_msg
            tool_result_messages.append(tr_msg)
            # Skill/Agent 工具可能返回 new_messages，注入对话
            if result.new_messages:
                for nm in result.new_messages:
                    yield nm
                    tool_result_messages.append(nm)
            # context_modifier 中的 allowed_tools 注入会话级权限
            if result.context_modifier and result.context_modifier.get("allowed_tools"):
                for t in result.context_modifier["allowed_tools"]:
                    engine.always_allowed.add(t)
            # present_files 交付事件：只发前端，不入对话
            pf_event = _present_files_event(result)
            if pf_event:
                yield pf_event

        # 更新 token 使用量（写回引擎）
        if usage_info:
            engine.total_usage += usage_info.get("total_tokens", 0)

        # ---- 6. 检测工具调用 ----
        tool_calls = collect_tool_calls(stream_events)

        # 构建 assistant 消息
        assistant_msg = _build_assistant_message(
            content_parts, tool_calls, reasoning_parts, reasoning_first_ts, reasoning_last_ts,
            context_usage=(
                usage_info.get("total_input_tokens") or usage_info.get("prompt_tokens")
            ) if usage_info else None,
        )

        # ---- 7. 错误恢复 ----

        # 7a. 上下文长度超限 → 尝试压缩恢复
        if error_occurred and _is_context_length_error(error_occurred):
            api_error = classify_error(error_occurred)
            if is_recoverable_error(api_error):
                # 尝试压缩恢复
                if not state.has_attempted_reactive_compact and config.auto_compact_enabled:
                    try:
                        compacted: list[dict] | None = None
                        async for item in _run_inline_compression(
                            messages=messages,
                            model=engine_config.model,
                            tracking=tracking,
                            deps=deps,
                            transcript_path=transcript_path,
                        ):
                            if isinstance(item, StreamEvent):
                                yield item
                            else:
                                compacted = item
                        if compacted is not None and compacted != messages:
                            was_inserted = len(compacted) > len(messages)
                            engine.mutable_messages = compacted
                            messages = compacted
                            if was_inserted:
                                # 插入的新边界+摘要增量下发（与主压缩路径同规则）
                                for msg in get_messages_after_compact_boundary(compacted)[:2]:
                                    yield msg
                            # 取消流式工具执行器（LLM 没产出有效响应，工具结果不应保留）
                            tool_executor.cancel()
                            updates = {
                                "has_attempted_reactive_compact": True,
                                "transition": "reactive_compact_retry",
                            }
                            state = State(**{**asdict(state), **updates})
                            continue
                    except Exception:
                        pass

                # 压缩恢复失败 → yield 扣留的错误事件 → return
                # 取消流式工具执行器（LLM 没产出有效响应，工具结果不应保留）
                tool_executor.cancel()
                if withheld_error is not None:
                    yield withheld_error
                yield LoopResult(reason="prompt_too_long", error=error_occurred)
                return

        # 7b. finish_reason=length → 恢复消息 → continue（最多 3 次）
        if finish_reason == "length":
            if state.max_output_tokens_recovery_count < MAX_OUTPUT_TOKENS_RECOVERY_LIMIT:
                recovery_msg = {
                    "role": "user",
                    "content": (
                        "Output token limit hit. Resume directly — no apology, "
                        "no recap of what you were doing. Pick up mid-thought "
                        "if that is where the cut happened. Break remaining "
                        "work into smaller pieces."
                    ),
                }
                next_messages = [*messages, assistant_msg, recovery_msg]
                engine.mutable_messages = next_messages
                messages = next_messages
                updates = {
                    "max_output_tokens_recovery_count": state.max_output_tokens_recovery_count + 1,
                    "transition": "max_output_tokens_recovery",
                }
                state = State(**{**asdict(state), **updates})
                continue

            # 恢复次数用尽
            yield StreamEvent(
                type="error",
                error=RuntimeError("Max output tokens recovery limit exceeded"),
                content="Output token limit recovery exhausted after 3 attempts",
            )
            yield LoopResult(reason="max_output_tokens_exhausted")
            return

        # 7c. 其他错误 → yield error → return
        if error_occurred:
            # 上下文超限但 is_recoverable_error 返回 False 时，
            # 7a 没进 if，错误事件已在流式循环里被扣下，这里 yield 出来
            if withheld_error is not None:
                yield withheld_error
            yield StreamEvent(
                type="error",
                error=error_occurred,
                content=str(error_occurred),
            )
            # 回滚引擎消息历史：错误时不保留这一轮的不完整 assistant 消息，
            # 避免坏历史导致下次请求又报错
            engine.mutable_messages = messages
            yield LoopResult(reason="model_error", error=error_occurred)
            return

        # 把 assistant 消息持久化到引擎，并更新局部 messages
        engine.mutable_messages = [*messages, assistant_msg]
        messages = [*messages, assistant_msg]

        # yield assistant 消息，让 REPL append 到自己的历史。
        # 放在错误恢复之后、完成检查之前——错误恢复走 continue/return 时
        # 不 yield（消息可能不完整或要重试），只有正常流程才 yield。
        yield assistant_msg

        # ---- 8. 完成检查 ----

        # 8a. finish_reason=stop → yield done → return
        if finish_reason == "stop":
            yield StreamEvent(type="done", finish_reason="stop")
            await _mine_conversation_to_palace(engine)
            yield LoopResult(reason="completed")
            return

        # 8b. 无工具调用 → yield done → return
        if not tool_calls:
            # 运行停止钩子
            stop_result = await run_stop_hooks(messages)
            if stop_result.should_stop:
                yield StreamEvent(type="done", finish_reason="stop")
                await _mine_conversation_to_palace(engine)
                yield LoopResult(reason="completed")
                return

            yield StreamEvent(type="done", finish_reason="stop")
            await _mine_conversation_to_palace(engine)
            yield LoopResult(reason="completed")
            return

        # ---- 9. 工具结果已在流式期间收集 ----
        # tool_result_messages 已在流式循环和收尾阶段填充

        # ---- 10. 工具结果已在流式期间 yield，此处仅用于状态转换 ----

        # ---- 11. 状态转换 ----
        next_messages = [*messages, *tool_result_messages]

        # 后台子代理完成通知（活跃通道）：父会话运行中时在本轮 drain 注入对话，
        # 非活跃期间的通知留队列，下次运行时在此取走，不丢失
        try:
            from tools.subagent.notify import drain_notifications

            for notice in drain_notifications(getattr(engine, "session_id", "")):
                yield notice
                next_messages.append(notice)
        except ImportError:
            pass

        engine.mutable_messages = next_messages

        # 刷新工具列表（为未来 MCP 接入预留，当前刷新结果和初始一样）。
        # 子代理全程锁定派生时解析的工具池：跳过整体重置，
        # 否则被星形拓扑排除的横向工具会经每轮刷新重新回到子代理池
        if not is_subagent_context(tool_use_context):
            engine_config = replace(engine_config, tools=get_tools())

        updates = {
            "max_output_tokens_recovery_count": 0,
            "transition": "next_turn",
        }
        state = State(**{**asdict(state), **updates})
