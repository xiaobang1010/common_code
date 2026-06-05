"""Hook 系统核心模块。

参考原始 TypeScript 实现 src/utils/hooks.ts，提供 Hook 快照与执行功能。

Hook 是用户定义的 shell 命令，可以在工具调用的前后执行，
用于拦截、修改或记录工具行为。

核心概念：
  - HookConfig：hooks 配置快照，从 settings 中捕获
  - HookEntry：一组 hook 定义 + 匹配器
  - HookDefinition：单个 hook 命令定义
  - HookResult：hook 执行结果
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# 默认 hook 执行超时（秒）
DEFAULT_HOOK_TIMEOUT_S = 30


# ---------------------------------------------------------------------------
# Dataclass 定义
# ---------------------------------------------------------------------------


@dataclass
class HookDefinition:
    """单个 hook 命令定义。"""

    type: str  # "command" | "prompt"
    command: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": self.type, "command": self.command}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HookDefinition:
        return cls(
            type=data.get("type", "command"),
            command=data.get("command", ""),
        )


@dataclass
class HookEntry:
    """一组 hook 定义 + 匹配器。

    matcher 用于匹配工具名，支持 glob 风格通配符。
    如果 matcher 为 None，则匹配所有工具。
    """

    matcher: str | None
    hooks: list[HookDefinition] = field(default_factory=list)

    def matches(self, tool_name: str) -> bool:
        """检查工具名是否匹配此 hook entry 的 matcher。"""
        if self.matcher is None:
            return True
        # 将 glob 风格的 * 转换为正则
        pattern = re.escape(self.matcher).replace(r"\*", ".*")
        return bool(re.fullmatch(pattern, tool_name))

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if self.matcher is not None:
            result["matcher"] = self.matcher
        result["hooks"] = [h.to_dict() for h in self.hooks]
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HookEntry:
        hooks = [HookDefinition.from_dict(h) for h in data.get("hooks", [])]
        return cls(
            matcher=data.get("matcher"),
            hooks=hooks,
        )


@dataclass
class HookConfig:
    """Hooks 配置快照。

    对应 settings.json 中的 hooks 字段结构：
    {
      "hooks": {
        "PreToolUse": [...],
        "PostToolUse": [...],
        "Notification": [...],
        "Stop": [...],
        "SessionStart": [...],
        "SessionEnd": [...],
        "UserPromptSubmit": [...],
        "PreCompact": [...]
      }
    }
    """

    pre_tool_use: list[HookEntry] = field(default_factory=list)
    post_tool_use: list[HookEntry] = field(default_factory=list)
    notification: list[HookEntry] = field(default_factory=list)
    stop: list[HookEntry] = field(default_factory=list)
    session_start: list[HookEntry] = field(default_factory=list)
    session_end: list[HookEntry] = field(default_factory=list)
    user_prompt_submit: list[HookEntry] = field(default_factory=list)
    pre_compact: list[HookEntry] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "PreToolUse": [e.to_dict() for e in self.pre_tool_use],
            "PostToolUse": [e.to_dict() for e in self.post_tool_use],
            "Notification": [e.to_dict() for e in self.notification],
            "Stop": [e.to_dict() for e in self.stop],
            "SessionStart": [e.to_dict() for e in self.session_start],
            "SessionEnd": [e.to_dict() for e in self.session_end],
            "UserPromptSubmit": [e.to_dict() for e in self.user_prompt_submit],
            "PreCompact": [e.to_dict() for e in self.pre_compact],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HookConfig:
        def parse_entries(key: str) -> list[HookEntry]:
            raw = data.get(key, [])
            if isinstance(raw, list):
                return [HookEntry.from_dict(e) for e in raw]
            return []

        return cls(
            pre_tool_use=parse_entries("PreToolUse"),
            post_tool_use=parse_entries("PostToolUse"),
            notification=parse_entries("Notification"),
            stop=parse_entries("Stop"),
            session_start=parse_entries("SessionStart"),
            session_end=parse_entries("SessionEnd"),
            user_prompt_submit=parse_entries("UserPromptSubmit"),
            pre_compact=parse_entries("PreCompact"),
        )


@dataclass
class HookResult:
    """Hook 执行结果。"""

    decided: bool = False  # 是否做出了决策（deny/allow）
    reason: str = ""  # 决策原因
    updated_input: dict | None = None  # 修改后的工具输入


# ---------------------------------------------------------------------------
# 快照捕获
# ---------------------------------------------------------------------------


def capture_hooks_config_snapshot() -> HookConfig:
    """从配置中读取 hooks 配置并返回快照。

    读取合并后的 settings 中的 hooks 字段，构建 HookConfig。
    如果配置系统未初始化或无 hooks 配置，返回空的 HookConfig。
    """
    try:
        from startup.utils.config import get_initial_settings

        settings = get_initial_settings()
        hooks_data = settings.hooks
        if not hooks_data:
            return HookConfig()
        return HookConfig.from_dict(hooks_data)
    except Exception as e:
        logger.warning("捕获 hooks 配置快照失败: %s", e)
        return HookConfig()


# ---------------------------------------------------------------------------
# Hook 执行
# ---------------------------------------------------------------------------


async def _execute_command_hook(
    command: str,
    hook_input: dict[str, Any],
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S,
) -> tuple[int, str, str]:
    """执行单个 command hook。

    通过 stdin 传入 JSON 格式的 hook_input。

    返回 (exit_code, stdout, stderr)。
    """
    json_input = json.dumps(hook_input, ensure_ascii=False)

    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=json_input.encode("utf-8")),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            logger.warning("Hook 命令超时 (%ss): %s", timeout_s, command)
            return (-1, "", f"Hook timed out after {timeout_s}s")

        exit_code = proc.returncode if proc.returncode is not None else -1
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        return (exit_code, stdout, stderr)

    except Exception as e:
        logger.error("Hook 命令执行失败: %s — %s", command, e)
        return (-1, "", str(e))


def _parse_hook_output(stdout: str) -> dict[str, Any] | None:
    """解析 hook 的 stdout 输出。

    如果输出以 { 开头，尝试解析为 JSON。
    否则返回 None（纯文本输出）。
    """
    trimmed = stdout.strip()
    if not trimmed.startswith("{"):
        return None
    try:
        return json.loads(trimmed)
    except json.JSONDecodeError:
        return None


async def run_pre_tool_use_hooks(
    hook_config: HookConfig,
    tool_name: str,
    tool_input: dict,
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S,
) -> HookResult:
    """执行 PreToolUse hooks。

    遍历所有匹配的 PreToolUse hook，依次执行。
    - 如果 hook 返回非零退出码，表示 deny
    - 如果 hook 输出 JSON 包含 decision=block 或 permissionDecision=deny，表示 deny
    - 如果 hook 输出 JSON 包含 updatedInput，则修改工具输入

    一旦某个 hook deny，立即返回，不再执行后续 hook。
    """
    result = HookResult()

    for entry in hook_config.pre_tool_use:
        if not entry.matches(tool_name):
            continue

        for hook_def in entry.hooks:
            if hook_def.type != "command":
                continue

            hook_input = {
                "hookEventName": "PreToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
            }

            exit_code, stdout, stderr = await _execute_command_hook(
                hook_def.command, hook_input, timeout_s
            )

            # 非零退出码 = deny
            if exit_code != 0:
                reason = stderr.strip() or stdout.strip() or f"Hook exited with code {exit_code}"
                result.decided = True
                result.reason = reason
                return result

            # 解析 JSON 输出
            parsed = _parse_hook_output(stdout)
            if parsed is not None:
                # 检查 decision 字段
                decision = parsed.get("decision")
                if decision == "block":
                    result.decided = True
                    result.reason = parsed.get("reason", "Blocked by hook")
                    return result

                # 检查 hookSpecificOutput
                specific = parsed.get("hookSpecificOutput", {})
                if specific.get("hookEventName") == "PreToolUse":
                    perm_decision = specific.get("permissionDecision")
                    if perm_decision == "deny":
                        result.decided = True
                        result.reason = specific.get(
                            "permissionDecisionReason",
                            parsed.get("reason", "Blocked by hook"),
                        )
                        return result
                    elif perm_decision == "allow":
                        # 明确允许，跳过后续 hook
                        result.decided = True
                        result.reason = "Allowed by hook"
                        return result

                    # 提取 updatedInput
                    if "updatedInput" in specific:
                        result.updated_input = specific["updatedInput"]

                # 顶层 updatedInput
                if "updatedInput" in parsed and result.updated_input is None:
                    result.updated_input = parsed["updatedInput"]

                # continue=false 表示阻止继续
                if parsed.get("continue") is False:
                    result.decided = True
                    result.reason = parsed.get("stopReason", "Hook requested stop")
                    return result

    return result


async def run_post_tool_use_hooks(
    hook_config: HookConfig,
    tool_name: str,
    tool_input: dict,
    tool_result: str,
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S,
) -> None:
    """执行 PostToolUse hooks。

    PostToolUse hooks 仅记录，不修改结果。
    执行失败不会中断流程，仅记录日志。
    """
    for entry in hook_config.post_tool_use:
        if not entry.matches(tool_name):
            continue

        for hook_def in entry.hooks:
            if hook_def.type != "command":
                continue

            hook_input = {
                "hookEventName": "PostToolUse",
                "tool_name": tool_name,
                "tool_input": tool_input,
                "tool_result": tool_result,
            }

            try:
                exit_code, stdout, stderr = await _execute_command_hook(
                    hook_def.command, hook_input, timeout_s
                )
                if exit_code != 0:
                    logger.warning(
                        "PostToolUse hook 返回非零退出码 %d: %s",
                        exit_code,
                        stderr.strip() or stdout.strip(),
                    )
            except Exception as e:
                logger.error("PostToolUse hook 执行异常: %s", e)


async def run_session_start_hooks(
    hook_config: HookConfig,
    session_id: str,
    cwd: str,
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S,
) -> str:
    """执行 SessionStart hooks。

    遍历所有 SessionStart hook，执行并收集 additionalContext。
    返回合并后的上下文字符串。
    """
    contexts: list[str] = []

    for entry in hook_config.session_start:
        for hook_def in entry.hooks:
            if hook_def.type != "command":
                continue

            hook_input = {
                "hookEventName": "SessionStart",
                "session_id": session_id,
                "cwd": cwd,
                "source": "startup",
            }

            try:
                exit_code, stdout, stderr = await _execute_command_hook(
                    hook_def.command, hook_input, timeout_s
                )
                if exit_code == 0:
                    parsed = _parse_hook_output(stdout)
                    if parsed is not None:
                        specific = parsed.get("hookSpecificOutput", {})
                        ctx = specific.get("additionalContext", "")
                        if ctx:
                            contexts.append(ctx)
                    elif stdout.strip():
                        contexts.append(stdout.strip())
            except Exception as e:
                logger.error("SessionStart hook 执行异常: %s", e)

    return "\n".join(contexts)


async def run_session_end_hooks(
    hook_config: HookConfig,
    session_id: str,
    cwd: str,
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S,
) -> None:
    """执行 SessionEnd hooks。

    SessionEnd hooks 用于清理资源，不修改行为。
    执行失败不中断流程，仅记录日志。
    """
    for entry in hook_config.session_end:
        for hook_def in entry.hooks:
            if hook_def.type != "command":
                continue

            hook_input = {
                "hookEventName": "SessionEnd",
                "session_id": session_id,
                "cwd": cwd,
                "reason": "other",
            }

            try:
                exit_code, stdout, stderr = await _execute_command_hook(
                    hook_def.command, hook_input, timeout_s
                )
                if exit_code != 0:
                    logger.warning(
                        "SessionEnd hook 返回非零退出码 %d: %s",
                        exit_code,
                        stderr.strip() or stdout.strip(),
                    )
            except Exception as e:
                logger.error("SessionEnd hook 执行异常: %s", e)


async def run_user_prompt_submit_hooks(
    hook_config: HookConfig,
    prompt: str,
    session_id: str = "",
    cwd: str = "",
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S,
) -> HookResult:
    """执行 UserPromptSubmit hooks。

    遍历所有 UserPromptSubmit hook，支持拦截用户输入。
    - 如果 hook 返回 continue=false 或 exit code 2，表示拦截
    - 可以通过 additionalContext 注入额外上下文
    """
    result = HookResult()

    for entry in hook_config.user_prompt_submit:
        for hook_def in entry.hooks:
            if hook_def.type != "command":
                continue

            hook_input = {
                "hookEventName": "UserPromptSubmit",
                "session_id": session_id,
                "cwd": cwd,
                "prompt": prompt,
            }

            exit_code, stdout, stderr = await _execute_command_hook(
                hook_def.command, hook_input, timeout_s
            )

            # exit code 2 = block
            if exit_code == 2:
                result.decided = True
                result.reason = stderr.strip() or "Blocked by UserPromptSubmit hook"
                return result

            # exit code != 0 and != 2 = non-blocking error
            if exit_code != 0:
                logger.warning(
                    "UserPromptSubmit hook 返回非零退出码 %d: %s",
                    exit_code,
                    stderr.strip() or stdout.strip(),
                )
                continue

            # 解析 JSON 输出
            parsed = _parse_hook_output(stdout)
            if parsed is not None:
                if parsed.get("continue") is False:
                    result.decided = True
                    result.reason = parsed.get("stopReason", "Blocked by hook")
                    return result

                specific = parsed.get("hookSpecificOutput", {})
                ctx = specific.get("additionalContext", "")
                if ctx:
                    result.reason = ctx  # 复用 reason 字段传递上下文

    return result


async def run_pre_compact_hooks(
    hook_config: HookConfig,
    trigger: str = "auto",
    custom_instructions: str = "",
    session_id: str = "",
    cwd: str = "",
    timeout_s: float = DEFAULT_HOOK_TIMEOUT_S,
) -> str:
    """执行 PreCompact hooks。

    遍历所有 PreCompact hook，收集压缩指导信息。
    返回合并后的压缩指导字符串。
    """
    guidance_parts: list[str] = []

    for entry in hook_config.pre_compact:
        for hook_def in entry.hooks:
            if hook_def.type != "command":
                continue

            hook_input = {
                "hookEventName": "PreCompact",
                "session_id": session_id,
                "cwd": cwd,
                "trigger": trigger,
                "custom_instructions": custom_instructions,
            }

            try:
                exit_code, stdout, stderr = await _execute_command_hook(
                    hook_def.command, hook_input, timeout_s
                )
                if exit_code == 0:
                    parsed = _parse_hook_output(stdout)
                    if parsed is not None:
                        specific = parsed.get("hookSpecificOutput", {})
                        ctx = specific.get("additionalContext", "")
                        if ctx:
                            guidance_parts.append(ctx)
                    elif stdout.strip():
                        guidance_parts.append(stdout.strip())
            except Exception as e:
                logger.error("PreCompact hook 执行异常: %s", e)

    return "\n".join(guidance_parts)


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os
    import sys

    print("=" * 60)
    print("Hook 系统测试")
    print("=" * 60)

    # 测试 1: HookEntry.matches
    print("\n--- 测试 1: HookEntry.matches ---")
    entry_all = HookEntry(matcher=None, hooks=[])
    entry_bash = HookEntry(matcher="Bash", hooks=[])
    entry_glob = HookEntry(matcher="Bash*", hooks=[])
    entry_read = HookEntry(matcher="Read", hooks=[])

    assert entry_all.matches("Bash"), "None matcher 应匹配所有"
    assert entry_all.matches("Read"), "None matcher 应匹配所有"
    assert entry_bash.matches("Bash"), "精确匹配"
    assert not entry_bash.matches("Read"), "不匹配"
    assert entry_glob.matches("Bash"), "通配符匹配 Bash"
    assert entry_glob.matches("BashTool"), "通配符匹配 BashTool"
    assert not entry_glob.matches("Read"), "通配符不匹配 Read"
    assert entry_read.matches("Read"), "精确匹配 Read"
    print("  [PASS] HookEntry.matches")

    # 测试 2: HookConfig 序列化/反序列化
    print("\n--- 测试 2: HookConfig 序列化/反序列化 ---")
    config = HookConfig(
        pre_tool_use=[
            HookEntry(
                matcher="Bash",
                hooks=[HookDefinition(type="command", command="echo test")],
            )
        ],
        post_tool_use=[],
        notification=[],
        stop=[],
    )
    d = config.to_dict()
    assert "PreToolUse" in d
    assert len(d["PreToolUse"]) == 1
    assert d["PreToolUse"][0]["matcher"] == "Bash"

    restored = HookConfig.from_dict(d)
    assert len(restored.pre_tool_use) == 1
    assert restored.pre_tool_use[0].matcher == "Bash"
    assert len(restored.pre_tool_use[0].hooks) == 1
    assert restored.pre_tool_use[0].hooks[0].command == "echo test"
    print("  [PASS] HookConfig 序列化/反序列化")

    # 测试 3: capture_hooks_config_snapshot
    print("\n--- 测试 3: capture_hooks_config_snapshot ---")
    snapshot = capture_hooks_config_snapshot()
    assert isinstance(snapshot, HookConfig)
    print(f"  pre_tool_use: {len(snapshot.pre_tool_use)} entries")
    print(f"  post_tool_use: {len(snapshot.post_tool_use)} entries")
    print("  [PASS] capture_hooks_config_snapshot")

    # 测试 4: run_pre_tool_use_hooks (异步)
    print("\n--- 测试 4: run_pre_tool_use_hooks ---")

    async def test_pre_hook():
        # 空 hook 配置 — 不 deny
        empty_config = HookConfig()
        result = await run_pre_tool_use_hooks(empty_config, "Bash", {"command": "ls"})
        assert not result.decided, "空配置不应 deny"
        print("  空 config: decided=False")

        # 匹配但退出码为 0 的 hook — 不 deny
        config_ok = HookConfig(
            pre_tool_use=[
                HookEntry(
                    matcher="Bash",
                    hooks=[HookDefinition(type="command", command="echo ok")],
                )
            ]
        )
        result = await run_pre_tool_use_hooks(config_ok, "Bash", {"command": "ls"})
        assert not result.decided, "退出码 0 不应 deny"
        print("  exit 0: decided=False")

        # 匹配但退出码非 0 的 hook — deny
        config_deny = HookConfig(
            pre_tool_use=[
                HookEntry(
                    matcher="Bash",
                    hooks=[HookDefinition(type="command", command="exit 1")],
                )
            ]
        )
        result = await run_pre_tool_use_hooks(config_deny, "Bash", {"command": "rm -rf /"})
        assert result.decided, "退出码非 0 应 deny"
        print(f"  exit 1: decided=True, reason={result.reason!r}")

        # 不匹配的 hook — 不 deny
        result = await run_pre_tool_use_hooks(config_deny, "Read", {"path": "/tmp/test"})
        assert not result.decided, "不匹配的 hook 不应 deny"
        print("  不匹配: decided=False")

        # JSON 输出 with decision=block
        config_block = HookConfig(
            pre_tool_use=[
                HookEntry(
                    matcher="Bash",
                    hooks=[
                        HookDefinition(
                            type="command",
                            command='echo \'{"decision":"block","reason":"dangerous"}\'',
                        )
                    ],
                )
            ]
        )
        result = await run_pre_tool_use_hooks(config_block, "Bash", {"command": "rm -rf /"})
        assert result.decided, "decision=block 应 deny"
        assert "dangerous" in result.reason, f"reason 应包含 'dangerous', got: {result.reason}"
        print(f"  decision=block: decided=True, reason={result.reason!r}")

        # JSON 输出 with updatedInput
        config_update = HookConfig(
            pre_tool_use=[
                HookEntry(
                    matcher="Bash",
                    hooks=[
                        HookDefinition(
                            type="command",
                            command='echo \'{"hookSpecificOutput":{"hookEventName":"PreToolUse","updatedInput":{"command":"ls -la"}}}\'',
                        )
                    ],
                )
            ]
        )
        result = await run_pre_tool_use_hooks(config_update, "Bash", {"command": "ls"})
        assert not result.decided, "updatedInput 不应 deny"
        assert result.updated_input is not None, "应有 updated_input"
        assert result.updated_input.get("command") == "ls -la", f"updated_input 不正确: {result.updated_input}"
        print(f"  updatedInput: {result.updated_input}")

    asyncio.run(test_pre_hook())
    print("  [PASS] run_pre_tool_use_hooks")

    # 测试 5: run_post_tool_use_hooks (异步)
    print("\n--- 测试 5: run_post_tool_use_hooks ---")

    async def test_post_hook():
        config = HookConfig(
            post_tool_use=[
                HookEntry(
                    matcher="Bash",
                    hooks=[HookDefinition(type="command", command="echo post")],
                )
            ]
        )
        # 不应抛出异常
        await run_post_tool_use_hooks(config, "Bash", {"command": "ls"}, "file1\nfile2")
        print("  PostToolUse hook 执行成功（无异常）")

    asyncio.run(test_post_hook())
    print("  [PASS] run_post_tool_use_hooks")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
