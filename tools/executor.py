"""工具执行管线核心 — 解析、验证、Hook、权限、执行、结果封装。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from pydantic import BaseModel

from tools.protocol import Tool, ToolResult, ToolUseContext, tool_matches_name
from startup.utils.hooks import HookConfig, HookResult, run_pre_tool_use_hooks, run_post_tool_use_hooks
from tools.utils.validation import validate_tool_input


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
    if hook_config is not None:
        # Hook 需要 JSON 可序列化的 dict，将 Pydantic 实例转为 dict
        hook_input_dict = (
            validated_input.model_dump()
            if isinstance(validated_input, BaseModel)
            else validated_input
        )
        hook_result: HookResult = await run_pre_tool_use_hooks(
            hook_config, tool_name, hook_input_dict,
        )
        if hook_result.decided and hook_result.reason:
            # decided=True + 有 reason 表示 hook 做出了 deny 决策
            return ToolExecutionResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=f"Tool use denied by hook: {hook_result.reason}",
                is_error=True,
            )
        if hook_result.updated_input is not None:
            validated_input = hook_result.updated_input

    # ---- 5. 权限决策 ----
    if permission_check is not None:
        try:
            perm_result = await permission_check(tool, validated_input, context)
            # perm_result: {"decision": "allow"|"deny", "reason": str}
            if isinstance(perm_result, dict) and perm_result.get("decision") == "deny":
                return ToolExecutionResult(
                    tool_call_id=tool_call_id,
                    tool_name=tool_name,
                    content=f"Permission denied: {perm_result.get('reason', 'No reason provided')}",
                    is_error=True,
                )
        except Exception as e:
            return ToolExecutionResult(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                content=f"Permission check error: {e}",
                is_error=True,
            )

    # ---- 6. tool.execute() ----
    try:
        result: ToolResult = await tool.execute(validated_input, context)
    except Exception as e:
        return ToolExecutionResult(
            tool_call_id=tool_call_id,
            tool_name=tool_name,
            content=f"Tool execution error: {e}",
            is_error=True,
        )

    # ---- 7. PostToolUse Hooks ----
    if hook_config is not None:
        await run_post_tool_use_hooks(
            hook_config,
            tool_name,
            hook_input_dict,
            result.content,
        )

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
# 自测
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import asyncio

    from pydantic import BaseModel

    # ---- 辅助：定义 mock 工具 ----

    class EchoInput(BaseModel):
        message: str
        repeat: int = 1

    async def echo_execute(inp: dict, ctx: ToolUseContext) -> ToolResult:
        msg = inp.message if hasattr(inp, 'message') else inp.get("message", "")
        repeat = inp.repeat if hasattr(inp, 'repeat') else inp.get("repeat", 1)
        return ToolResult(content=(msg + "\n") * repeat)

    echo_tool = Tool(
        name="Echo",
        description="Echo tool",
        input_schema=EchoInput,
        execute=echo_execute,
        prompt="Echo prompt",
        aliases=["echo", "e"],
    )

    class StrictInput(BaseModel):
        value: int

    async def strict_execute(inp: dict, ctx: ToolUseContext) -> ToolResult:
        value = inp.value if hasattr(inp, 'value') else inp['value']
        return ToolResult(content=f"value={value}")

    strict_tool = Tool(
        name="Strict",
        description="Strict tool",
        input_schema=StrictInput,
        execute=strict_execute,
        prompt="Strict prompt",
    )

    tools = [echo_tool, strict_tool]

    # ---- 1. 测试 find_tool_by_name（含别名） ----
    assert find_tool_by_name(tools, "Echo") is echo_tool
    assert find_tool_by_name(tools, "echo") is echo_tool
    assert find_tool_by_name(tools, "e") is echo_tool
    assert find_tool_by_name(tools, "Strict") is strict_tool
    assert find_tool_by_name(tools, "nonexistent") is None
    print("[PASS] find_tool_by_name（含别名）")

    # ---- 2. 测试 validate_tool_input（有效/无效输入） ----
    from tools.utils.validation import validate_tool_input as _vti

    valid, err = _vti(echo_tool, {"message": "hello", "repeat": 3})
    assert valid is not None and err is None
    assert valid.message == "hello"
    assert valid.repeat == 3

    invalid, err_msg = _vti(echo_tool, {"repeat": 3})  # 缺少 message
    assert invalid is None
    assert err_msg is not None
    assert "message" in err_msg.lower() or "missing" in err_msg.lower() or "field required" in err_msg.lower()

    invalid2, err_msg2 = _vti(strict_tool, {"value": "not_an_int"})
    assert invalid2 is None
    assert err_msg2 is not None
    print("[PASS] validate_tool_input（有效/无效输入）")

    # ---- 3. 测试 execute_tool_call 完整管线 ----

    async def _test_execute():
        # 3a. 正常执行
        tc = {
            "id": "call_001",
            "function": {"name": "Echo", "arguments": '{"message": "hi", "repeat": 2}'},
        }
        ctx = ToolUseContext()
        result = await execute_tool_call(tc, tools, ctx)
        assert result.tool_call_id == "call_001"
        assert result.tool_name == "Echo"
        assert result.is_error is False
        assert result.content.strip() == "hi\nhi"
        print("[PASS] execute_tool_call 正常执行")

        # 3b. 工具不存在
        tc_missing = {
            "id": "call_002",
            "function": {"name": "Missing", "arguments": "{}"},
        }
        result2 = await execute_tool_call(tc_missing, tools, ctx)
        assert result2.is_error is True
        assert "not found" in result2.content.lower()
        print("[PASS] execute_tool_call 工具不存在")

        # 3c. 无效 JSON
        tc_bad_json = {
            "id": "call_003",
            "function": {"name": "Echo", "arguments": "{invalid json}"},
        }
        result3 = await execute_tool_call(tc_bad_json, tools, ctx)
        assert result3.is_error is True
        assert "invalid json" in result3.content.lower()
        print("[PASS] execute_tool_call 无效 JSON")

        # 3d. 验证失败
        tc_invalid = {
            "id": "call_004",
            "function": {"name": "Strict", "arguments": '{"value": "abc"}'},
        }
        result4 = await execute_tool_call(tc_invalid, tools, ctx)
        assert result4.is_error is True
        assert "validation" in result4.content.lower()
        print("[PASS] execute_tool_call 验证失败")

        # 3e. PreToolUse hook 拒绝（使用已有的 HookConfig + HookEntry + HookDefinition）
        from startup.utils.hooks import HookEntry, HookDefinition

        hook_cfg_deny = HookConfig(
            pre_tool_use=[
                HookEntry(
                    matcher="Echo",
                    hooks=[HookDefinition(type="command", command="exit 1")],
                )
            ]
        )
        result5 = await execute_tool_call(tc, tools, ctx, hook_config=hook_cfg_deny)
        assert result5.is_error is True
        assert "denied by hook" in result5.content.lower()
        print("[PASS] execute_tool_call PreToolUse hook 拒绝")

        # 3f. PreToolUse hook 通过（退出码 0）
        hook_cfg_allow = HookConfig(
            pre_tool_use=[
                HookEntry(
                    matcher="Echo",
                    hooks=[HookDefinition(type="command", command="echo ok")],
                )
            ]
        )
        result6 = await execute_tool_call(tc, tools, ctx, hook_config=hook_cfg_allow)
        assert result6.is_error is False
        assert result6.content.strip() == "hi\nhi"
        print("[PASS] execute_tool_call PreToolUse hook 通过")

        # 3g. 权限拒绝
        async def deny_permission(tool, input_args, context):
            return {"decision": "deny", "reason": "Not allowed"}

        result7 = await execute_tool_call(tc, tools, ctx, permission_check=deny_permission)
        assert result7.is_error is True
        assert "permission denied" in result7.content.lower()
        print("[PASS] execute_tool_call 权限拒绝")

        # 3h. 权限允许
        async def allow_permission(tool, input_args, context):
            return {"decision": "allow"}

        result8 = await execute_tool_call(tc, tools, ctx, permission_check=allow_permission)
        assert result8.is_error is False
        print("[PASS] execute_tool_call 权限允许")

        # 3i. validateInput 拒绝
        def validate_deny(inp, ctx):
            return {"result": False, "message": "Input not allowed", "errorCode": 403}

        validated_tool = Tool(
            name="Validated",
            description="Tool with validate_input",
            input_schema=EchoInput,
            execute=echo_execute,
            prompt="Validated prompt",
            validate_input=validate_deny,
        )
        tc_val = {
            "id": "call_val",
            "function": {"name": "Validated", "arguments": '{"message": "test"}'},
        }
        result9 = await execute_tool_call(tc_val, [validated_tool], ctx)
        assert result9.is_error is True
        assert "not allowed" in result9.content.lower()
        print("[PASS] execute_tool_call validateInput 拒绝")

        # 3j. PostToolUse hook 执行（不修改内容，仅验证不报错）
        hook_cfg_post = HookConfig(
            post_tool_use=[
                HookEntry(
                    matcher="Echo",
                    hooks=[HookDefinition(type="command", command="echo post")],
                )
            ]
        )
        result10 = await execute_tool_call(tc, tools, ctx, hook_config=hook_cfg_post)
        assert result10.is_error is False
        print("[PASS] execute_tool_call PostToolUse hook 执行")

    asyncio.run(_test_execute())

    # ---- 4. 测试 tool_result_to_openai_message 格式 ----
    exec_result = ToolExecutionResult(
        tool_call_id="call_123",
        tool_name="Echo",
        content="hello",
    )
    msg = tool_result_to_openai_message(exec_result)
    assert msg["role"] == "tool"
    assert msg["tool_call_id"] == "call_123"
    assert msg["content"] == "hello"
    print("[PASS] tool_result_to_openai_message 格式")

    # ---- 5. 测试 execute_tool_calls 批量执行 ----

    async def _test_batch():
        calls = [
            {"id": "b1", "function": {"name": "Echo", "arguments": '{"message": "first"}'}},
            {"id": "b2", "function": {"name": "Echo", "arguments": '{"message": "second"}'}},
        ]
        ctx = ToolUseContext()
        results = await execute_tool_calls(calls, tools, ctx)
        assert len(results) == 2
        assert results[0].tool_call_id == "b1"
        assert results[0].content.strip() == "first"
        assert results[1].tool_call_id == "b2"
        assert results[1].content.strip() == "second"
        print("[PASS] execute_tool_calls 批量执行")

    asyncio.run(_test_batch())

    print("\nAll tests passed!")
