"""工具执行管线核心 — 解析、验证、Hook、权限、执行、结果封装。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, tool_matches_name
from startup.utils.hooks import (
    HookConfig, HookResult,
    run_pre_tool_use_hooks, run_post_tool_use_hooks,
    run_permission_denied_hooks, run_post_tool_use_failure_hooks,
    resolve_permission_decision,
)
from tools.utils.validation import validate_tool_input

if TYPE_CHECKING:
    # 延迟到类型检查时导入，避免运行时循环依赖
    from query.services.api.llm import StreamEvent


# ---------------------------------------------------------------------------
# ToolExecutionResult — 工具执行管线结果
# ---------------------------------------------------------------------------

@dataclass
class ToolExecutionResult:
    """工具执行管线结果。"""

    tool_call_id: str
    tool_name: str
    content: str
    is_error: bool = False
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 查找工具
# ---------------------------------------------------------------------------

def find_tool_by_name(tools: list[Tool], name: str) -> Tool | None:
    """按名称查找工具（支持别名）。"""
    for tool in tools:
        if tool_matches_name(tool, name):
            return tool
    return None


# ---------------------------------------------------------------------------
# 结果转换
# ---------------------------------------------------------------------------

def tool_result_to_openai_message(result: ToolExecutionResult) -> dict:
    """将工具结果转换为 OpenAI 格式消息。

    返回：
        {"role": "tool", "tool_call_id": ..., "content": ...}
    """
    return {
        "role": "tool",
        "tool_call_id": result.tool_call_id,
        "content": result.content,
    }


# ---------------------------------------------------------------------------
# 单个工具调用执行
# ---------------------------------------------------------------------------

async def execute_tool_call(
    tool_call: dict,
    tools: list[Tool],
    context: ToolUseContext,
    hook_config: HookConfig | None = None,
    permission_check: Callable | None = None,
) -> ToolExecutionResult:
    """完整工具执行管线。

    流程：
    1. 解析工具调用（OpenAI 格式）
    2. Pydantic 验证
    3. validateInput
    4. PreToolUse Hooks
    5. 权限决策
    6. tool.execute()
    7. PostToolUse Hooks
    8. 封装返回
    """
    # ---- 1. 解析工具调用 ----
    tool_call_id = tool_call.get("id", "")
    func_info = tool_call.get("function", {})
    tool_name = func_info.get("name", "")
    raw_arguments = func_info.get("arguments", "{}")

    # 解析 arguments JSON
    try:
        if isinstance(raw_arguments, str):
            parsed_args = json.loads(raw_arguments) if raw_arguments else {}
        else:
            parsed_args = raw_arguments
    except json.JSONDecodeError as e:
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=f"Invalid JSON in tool arguments: {e}",
            is_error=True,
        )

    # ---- 2. 查找工具 + Pydantic 验证 ----
    tool = find_tool_by_name(tools, tool_name)
    if tool is None:
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=f"Tool not found: {tool_name}",
            is_error=True,
        )

    validated_input, validation_error = validate_tool_input(tool, parsed_args)
    if validation_error is not None:
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=f"Input validation error: {validation_error}",
            is_error=True,
        )

    # ---- 3. validateInput ----
    if tool.validate_input is not None:
        try:
            validation_result = tool.validate_input(validated_input, context)
            # validation_result: {result: True} 或 {result: False, message: str, errorCode: int}
            if isinstance(validation_result, dict):
                if not validation_result.get("result", True):
                    return ToolExecutionResult(
                        tool_call_id=tool_call_id,
                        tool_name=tool_name,
                        content=validation_result.get("message", "Validation failed"),
                        is_error=True,
                        metadata={"error_code": validation_result.get("errorCode", 0)},
                    )
            elif validation_result is False:
                return ToolExecutionResult(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    content="Validation failed",
                    is_error=True,
                )
        except Exception as e:
            return ToolExecutionResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=f"validateInput error: {e}",
                is_error=True,
            )

    # ---- 4. PreToolUse Hooks ----
    hook_result = HookResult()
    if hook_config is not None:
        # Hook 需要 JSON 可序列化的 dict，将 Pydantic 实例转为 dict
        hook_input_dict = (
            validated_input.model_dump()
            if isinstance(validated_input, BaseModel)
            else validated_input
        )
        # run_pre_tool_use_hooks 内部保证不抛出异常
        hook_result = await run_pre_tool_use_hooks(
            hook_config, tool_name, hook_input_dict,
        )
        if hook_result.updated_input is not None:
            validated_input = hook_result.updated_input

    # ---- 5. 权限决策 ----
    perm_decision = await resolve_permission_decision(
        hook_result, tool, validated_input, context, permission_check,
    )
    if perm_decision is not None:
        # 权限被拒绝，先跑 PermissionDenied hooks
        if hook_config is not None:
            deny_result = await run_permission_denied_hooks(
                hook_config,
                tool_name,
                hook_input_dict if hook_config is not None else {},
                perm_decision.get("reason", "Permission denied"),
            )
            if deny_result.decided:
                # PermissionDenied hook 表示可以重试
                return ToolExecutionResult(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    content=f"Permission denied: {perm_decision.get('reason', 'No reason provided')}. The PermissionDenied hook indicated this command is now approved. You may retry it if you would like.",
                    is_error=True,
                    metadata={"retry_allowed": True},
                )
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=f"Permission denied: {perm_decision.get('reason', 'No reason provided')}",
            is_error=True,
        )

    # ---- 6. tool.execute() ----
    try:
        result: ToolResult = await tool.execute(validated_input, context)
    except Exception as e:
        error_msg = str(e)
        # 执行失败后跑 PostToolUseFailure hooks
        if hook_config is not None:
            await run_post_tool_use_failure_hooks(
                hook_config,
                tool_name,
                hook_input_dict if hook_config is not None else {},
                error_msg,
            )
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=f"Tool execution error: {error_msg}",
            is_error=True,
        )

    # ---- 7. PostToolUse Hooks ----
    if hook_config is not None:
        try:
            await run_post_tool_use_hooks(
                hook_config,
                tool_name,
                hook_input_dict,
                result.content,
            )
        except Exception:
            pass  # post hook 失败不影响工具执行结果

    # ---- 8. 封装返回 ----
    return ToolExecutionResult(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        content=result.content,
        is_error=result.is_error,
        metadata=result.metadata,
    )


# ---------------------------------------------------------------------------
# 批量工具调用执行
# ---------------------------------------------------------------------------

async def execute_tool_calls(
    tool_calls: list[dict],
    tools: list[Tool],
    context: ToolUseContext,
    hook_config: HookConfig | None = None,
    permission_check: Callable | None = None,
) -> list[ToolExecutionResult]:
    """批量执行工具调用（串行执行，按顺序）。"""
    results: list[ToolExecutionResult] = []
    for tc in tool_calls:
        result = await execute_tool_call(tc, tools, context, hook_config, permission_check)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# StreamingToolExecutor — 流式工具执行器
# ---------------------------------------------------------------------------

class StreamingToolExecutor:
    """流式工具执行器 — 流式输出期间检测完整工具调用并立即异步执行。

    工作方式：
    1. 流式循环每收到 tool_call_delta 事件，调 add_delta(event)
    2. 按 tool_call_id 聚合 delta，累积 arguments 字符串
    3. 当 arguments 能成功 json.loads 时，认为工具调用完整，立即 asyncio.create_task 异步执行
    4. get_completed_results() 返回已完成的工具结果
    5. get_remaining_results() 等待所有待完成 task
    6. cancel() 取消所有正在执行的 task
    """

    def __init__(
        self,
        tools: list[Tool],
        context: ToolUseContext,
        hook_config: HookConfig | None = None,
        permission_check: Callable | None = None,
    ) -> None:
        self._tools = tools
        self._context = context
        self._hook_config = hook_config
        self._permission_check = permission_check
        # 按 tool_call_id 聚合的 delta，每个值是 {"id": str, "name": str, "arguments": str}
        self._delta_map: dict[str, dict] = {}
        # 已启动执行的 tool_call_id 集合
        self._started: set[str] = set()
        # tool_call_id -> asyncio.Task
        self._pending_tasks: dict[str, asyncio.Task] = {}
        # 已完成的工具结果
        self._completed_results: list[ToolExecutionResult] = []

    def add_delta(self, event: StreamEvent) -> None:
        """处理流式事件，累积工具调用 delta，完整后立即异步执行。

        只处理 type == "tool_call_delta" 的事件：按 tool_call_id 聚合 name 与
        arguments 增量；当累积的 arguments 能成功 json.loads 时，认为该工具调用
        完整，立即创建 asyncio.Task 异步执行。
        """
        if event.type != "tool_call_delta":
            return

        call_id = event.tool_call_id
        if call_id is None:
            return

        # 首次见到该 id，创建聚合条目
        if call_id not in self._delta_map:
            self._delta_map[call_id] = {"id": call_id, "name": "", "arguments": ""}

        entry = self._delta_map[call_id]

        # 追加 name 增量（如果有）
        if event.tool_call_name:
            entry["name"] += event.tool_call_name

        # 追加 arguments 增量（如果有）
        if event.tool_call_arguments:
            entry["arguments"] += event.tool_call_arguments

        # 已经启动执行了，不再重复处理
        if call_id in self._started:
            return

        # 尝试解析 arguments；成功则认为工具调用完整，立即异步执行
        try:
            json.loads(entry["arguments"])
        except (json.JSONDecodeError, ValueError):
            # arguments 还不完整，等更多 delta
            return

        # 构建完整 tool_call 并异步执行
        tool_call = {
            "id": call_id,
            "function": {
                "name": entry["name"],
                "arguments": entry["arguments"],
            },
        }
        self._started.add(call_id)
        self._pending_tasks[call_id] = asyncio.create_task(
            self._execute_one(tool_call)
        )

    async def _execute_one(self, tool_call: dict) -> None:
        """执行单个工具调用并把结果存入完成列表。"""
        call_id = tool_call.get("id", "")
        result = await execute_tool_call(
            tool_call,
            self._tools,
            self._context,
            self._hook_config,
            self._permission_check,
        )
        self._completed_results.append(result)
        self._pending_tasks.pop(call_id, None)

    def get_completed_results(self) -> list[ToolExecutionResult]:
        """取出并清空已完成的工具结果。"""
        results = self._completed_results
        self._completed_results = []
        return results

    async def get_remaining_results(self) -> list[ToolExecutionResult]:
        """等待所有待完成 task，返回所有结果并清空状态。"""
        if self._pending_tasks:
            await asyncio.gather(
                *self._pending_tasks.values(),
                return_exceptions=True,
            )
        results = self._completed_results
        self._completed_results = []
        self._pending_tasks = {}
        return results

    def cancel(self) -> None:
        """取消所有正在执行的 task 并清空全部状态。"""
        for task in self._pending_tasks.values():
            task.cancel()
        self._pending_tasks = {}
        self._completed_results = []
        self._delta_map = {}
        self._started = set()
