"""权限决策核心模块。

实现完整的权限决策管线（策略链模式），按优先级依次检查：
1. deny 规则
2. ask 规则
3. 工具自身权限
4. 安全检查（.git/ .claude/ 目录）
5. bypass 模式
6. allow 规则
7. 默认 ASK
"""

from __future__ import annotations

import fnmatch
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from startup.utils.settings.types import PermissionRule


class PermissionDecision(Enum):
    """权限决策结果。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class PermissionResult:
    """权限决策结果。"""

    decision: PermissionDecision
    reason: str = ""
    rule: PermissionRule | None = None


# ---------------------------------------------------------------------------
# 需要权限的工具列表
# ---------------------------------------------------------------------------

_TOOLS_REQUIRING_PERMISSION: set[str] = {
    "Bash",
    "PowerShell",
    "Write",
    "Edit",
    "MultiEdit",
}

# 安全路径前缀：涉及这些目录的操作始终需要确认
_UNSAFE_PATH_PREFIXES: tuple[str, ...] = (
    ".git" + os.sep,
    ".claude" + os.sep,
)


# ---------------------------------------------------------------------------
# 模式匹配辅助
# ---------------------------------------------------------------------------


def matches_tool_pattern(pattern: str, tool_name: str) -> bool:
    """工具名模式匹配，支持 fnmatch 通配符。

    Args:
        pattern: 模式字符串，如 "Bash"、"Write"、"Mcp__*" 等。
        tool_name: 实际工具名。

    Returns:
        是否匹配。
    """
    return fnmatch.fnmatch(tool_name, pattern)


def matches_input_pattern(pattern: str, tool_input: dict) -> bool:
    """输入模式匹配，检查输入值中是否包含 pattern。

    遍历 tool_input 的所有值，如果 pattern 为空则视为匹配所有输入。

    Args:
        pattern: 要匹配的字符串模式。
        tool_input: 工具输入字典。

    Returns:
        是否匹配。
    """
    if not pattern:
        return True

    for value in tool_input.values():
        if isinstance(value, str) and pattern in value:
            return True
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and pattern in item:
                    return True
        if isinstance(value, dict):
            if matches_input_pattern(pattern, value):
                return True

    return False


# ---------------------------------------------------------------------------
# 安全路径检查
# ---------------------------------------------------------------------------


def is_safe_path(path: str) -> bool:
    """检查路径是否安全（不涉及 .git/ 或 .claude/ 目录）。

    Args:
        path: 待检查的文件路径。

    Returns:
        True 表示安全，False 表示涉及受保护目录。
    """
    # 统一分隔符
    normalized = path.replace("\\", "/")

    # 检查路径中是否包含受保护目录段
    parts = normalized.split("/")
    for part in parts:
        if part in (".git", ".claude"):
            return False

    return True


# ---------------------------------------------------------------------------
# 规则层权限检查
# ---------------------------------------------------------------------------


def check_rule_based_permissions(
    tool_name: str,
    tool_input: dict,
    rules: list[PermissionRule],
) -> PermissionResult | None:
    """仅检查规则层权限。

    遍历规则列表，匹配 tool_pattern 和 input_pattern。
    tool_pattern 支持通配符（fnmatch），input_pattern 支持路径匹配。

    Args:
        tool_name: 工具名。
        tool_input: 工具输入。
        rules: 权限规则列表。

    Returns:
        匹配到的 PermissionResult，或 None 表示无规则匹配。
    """
    for rule in rules:
        if not matches_tool_pattern(rule.tool_pattern, tool_name):
            continue
        if not matches_input_pattern(rule.input_pattern, tool_input):
            continue

        # 匹配成功，根据规则类型返回结果
        if rule.rule_type == "deny":
            return PermissionResult(
                decision=PermissionDecision.DENY,
                reason=f"Permission to use {tool_name} has been denied by rule.",
                rule=rule,
            )
        elif rule.rule_type == "ask":
            return PermissionResult(
                decision=PermissionDecision.ASK,
                reason=f"Rule requires approval for {tool_name}.",
                rule=rule,
            )
        elif rule.rule_type == "allow":
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                reason=f"Tool {tool_name} is allowed by rule.",
                rule=rule,
            )

    return None


# ---------------------------------------------------------------------------
# 输入安全检查
# ---------------------------------------------------------------------------


def _check_input_safety(tool_input: dict) -> bool:
    """检查工具输入是否涉及不安全路径。

    Returns:
        True 表示安全，False 表示涉及 .git/ 或 .claude/ 目录。
    """
    for value in tool_input.values():
        if isinstance(value, str) and not is_safe_path(value):
            return False
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and not is_safe_path(item):
                    return False
        if isinstance(value, dict):
            if not _check_input_safety(value):
                return False
    return True


# ---------------------------------------------------------------------------
# 完整权限决策管线
# ---------------------------------------------------------------------------


def has_permissions_to_use_tool(
    tool_name: str,
    tool_input: dict,
    context: dict | None = None,
) -> PermissionResult:
    """完整权限决策管线（策略链模式）。

    按优先级依次检查：
    1. deny 规则：遍历 deny 规则列表，匹配 tool_name + input 模式 → DENY
    2. ask 规则：遍历 ask 规则列表，匹配 → ASK
    3. 安全检查：检查输入是否涉及 .git/ 或 .claude/ 目录 → ASK（bypass 模式仍强制）
    4. bypass 模式：如果 bypass_permissions 开启 → ALLOW
    5. 工具自身权限：检查工具是否需要权限 → ASK
    6. allow 规则：遍历 allow 规则列表，匹配 → ALLOW
    7. 默认：ASK

    Args:
        tool_name: 工具名。
        tool_input: 工具输入字典。
        context: 上下文字典，可包含：
            - "deny_rules": list[PermissionRule] — deny 规则列表
            - "ask_rules": list[PermissionRule] — ask 规则列表
            - "allow_rules": list[PermissionRule] — allow 规则列表
            - "bypass_permissions": bool — 是否绕过权限
            - "tools_requiring_permission": set[str] — 需要权限的工具集合

    Returns:
        PermissionResult 决策结果。
    """
    ctx = context or {}
    deny_rules: list[PermissionRule] = ctx.get("deny_rules", [])
    ask_rules: list[PermissionRule] = ctx.get("ask_rules", [])
    allow_rules: list[PermissionRule] = ctx.get("allow_rules", [])
    bypass_permissions: bool = ctx.get("bypass_permissions", False)
    tools_requiring_permission: set[str] = ctx.get(
        "tools_requiring_permission", _TOOLS_REQUIRING_PERMISSION
    )

    # 1. deny 规则优先
    deny_result = check_rule_based_permissions(tool_name, tool_input, deny_rules)
    if deny_result is not None:
        return deny_result

    # 2. ask 规则
    ask_result = check_rule_based_permissions(tool_name, tool_input, ask_rules)
    if ask_result is not None:
        return ask_result

    # 3. 安全检查（.git/ .claude/ 目录）—— bypass 模式仍强制
    if not _check_input_safety(tool_input):
        return PermissionResult(
            decision=PermissionDecision.ASK,
            reason="Input involves protected directory (.git/ or .claude/).",
        )

    # 4. bypass 模式
    if bypass_permissions:
        return PermissionResult(
            decision=PermissionDecision.ALLOW,
            reason="Bypass permissions mode is active.",
        )

    # 5. 工具自身权限检查
    if tool_name in tools_requiring_permission:
        return PermissionResult(
            decision=PermissionDecision.ASK,
            reason=f"Tool {tool_name} requires permission.",
        )

    # 6. allow 规则
    allow_result = check_rule_based_permissions(tool_name, tool_input, allow_rules)
    if allow_result is not None:
        return allow_result

    # 7. 默认 ASK
    return PermissionResult(
        decision=PermissionDecision.ASK,
        reason=f"No rule matched for {tool_name}, defaulting to ask.",
    )


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from startup.utils.settings.types import PermissionRule

    print("=" * 60)
    print("权限决策系统测试")
    print("=" * 60)

    passed = 0
    failed = 0

    # 测试 1: deny 规则优先
    print("\n--- 测试 1: deny 规则优先 ---")
    try:
        deny_rules = [PermissionRule(rule_type="deny", tool_pattern="Bash")]
        allow_rules = [PermissionRule(rule_type="allow", tool_pattern="Bash")]
        result = has_permissions_to_use_tool(
            "Bash",
            {"command": "ls"},
            {"deny_rules": deny_rules, "allow_rules": allow_rules},
        )
        assert result.decision == PermissionDecision.DENY, f"期望 DENY，实际 {result.decision}"
        assert result.rule is not None, "应匹配到规则"
        print(f"  决策: {result.decision.value}, 原因: {result.reason}")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    # 测试 2: allow 规则匹配
    print("\n--- 测试 2: allow 规则匹配 ---")
    try:
        allow_rules = [PermissionRule(rule_type="allow", tool_pattern="Read")]
        result = has_permissions_to_use_tool(
            "Read",
            {"file_path": "/tmp/test.txt"},
            {"allow_rules": allow_rules},
        )
        assert result.decision == PermissionDecision.ALLOW, f"期望 ALLOW，实际 {result.decision}"
        print(f"  决策: {result.decision.value}, 原因: {result.reason}")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    # 测试 3: 安全检查（.git/ 路径）
    print("\n--- 测试 3: 安全检查（.git/ 路径）---")
    try:
        result = has_permissions_to_use_tool(
            "Read",
            {"file_path": ".git/config"},
            {"bypass_permissions": True},
        )
        assert result.decision == PermissionDecision.ASK, f"期望 ASK，实际 {result.decision}"
        assert "protected directory" in result.reason, f"原因应提及受保护目录: {result.reason}"
        print(f"  决策: {result.decision.value}, 原因: {result.reason}")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    # 测试 4: bypass 模式
    print("\n--- 测试 4: bypass 模式 ---")
    try:
        result = has_permissions_to_use_tool(
            "Bash",
            {"command": "rm -rf /tmp/test"},
            {"bypass_permissions": True},
        )
        assert result.decision == PermissionDecision.ALLOW, f"期望 ALLOW，实际 {result.decision}"
        assert "Bypass" in result.reason, f"原因应提及 Bypass: {result.reason}"
        print(f"  决策: {result.decision.value}, 原因: {result.reason}")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    # 测试 5: 默认 ASK
    print("\n--- 测试 5: 默认 ASK ---")
    try:
        # 使用不在 tools_requiring_permission 中的工具名来测试默认 ASK
        result = has_permissions_to_use_tool("UnknownTool", {"data": "test"})
        assert result.decision == PermissionDecision.ASK, f"期望 ASK，实际 {result.decision}"
        assert "default" in result.reason.lower(), f"原因应提及 default: {result.reason}"
        print(f"  决策: {result.decision.value}, 原因: {result.reason}")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    # 测试 6: matches_tool_pattern 通配符
    print("\n--- 测试 6: matches_tool_pattern 通配符 ---")
    try:
        assert matches_tool_pattern("Bash", "Bash"), "精确匹配应成功"
        assert matches_tool_pattern("Mcp__*", "Mcp__server1__tool1"), "通配符匹配应成功"
        assert not matches_tool_pattern("Bash", "Read"), "不匹配应返回 False"
        assert matches_tool_pattern("*", "AnyTool"), "* 通配符应匹配所有"
        assert matches_tool_pattern("Mcp__server1__*", "Mcp__server1__tool1"), "前缀通配符应匹配"
        print("  精确匹配: OK")
        print("  通配符匹配: OK")
        print("  不匹配: OK")
        print("  * 通配符: OK")
        print("  前缀通配符: OK")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    # 测试 7: ask 规则
    print("\n--- 测试 7: ask 规则 ---")
    try:
        ask_rules = [PermissionRule(rule_type="ask", tool_pattern="Write")]
        result = has_permissions_to_use_tool(
            "Write",
            {"file_path": "/tmp/test.txt", "content": "hello"},
            {"ask_rules": ask_rules},
        )
        assert result.decision == PermissionDecision.ASK, f"期望 ASK，实际 {result.decision}"
        assert result.rule is not None, "应匹配到规则"
        print(f"  决策: {result.decision.value}, 原因: {result.reason}")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    # 测试 8: input_pattern 匹配
    print("\n--- 测试 8: input_pattern 匹配 ---")
    try:
        deny_rules = [
            PermissionRule(rule_type="deny", tool_pattern="Bash", input_pattern="rm -rf")
        ]
        result = has_permissions_to_use_tool(
            "Bash",
            {"command": "rm -rf /"},
            {"deny_rules": deny_rules},
        )
        assert result.decision == PermissionDecision.DENY, f"期望 DENY，实际 {result.decision}"

        # input_pattern 不匹配时不应触发
        result2 = has_permissions_to_use_tool(
            "Bash",
            {"command": "ls -la"},
            {"deny_rules": deny_rules},
        )
        assert result2.decision != PermissionDecision.DENY, "input_pattern 不匹配时不应 DENY"
        print(f"  匹配: {result.decision.value}")
        print(f"  不匹配: {result2.decision.value}")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    # 测试 9: is_safe_path
    print("\n--- 测试 9: is_safe_path ---")
    try:
        assert not is_safe_path(".git/config"), ".git/ 路径应不安全"
        assert not is_safe_path("src/.git/HEAD"), "包含 .git/ 应不安全"
        assert not is_safe_path(".claude/settings.json"), ".claude/ 路径应不安全"
        assert is_safe_path("src/main.py"), "普通路径应安全"
        assert is_safe_path("/tmp/test.txt"), "绝对路径应安全"
        assert is_safe_path("README.md"), "根目录文件应安全"
        print("  .git/config: unsafe")
        print("  src/.git/HEAD: unsafe")
        print("  .claude/settings.json: unsafe")
        print("  src/main.py: safe")
        print("  /tmp/test.txt: safe")
        print("  README.md: safe")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    # 测试 10: check_rule_based_permissions 独立调用
    print("\n--- 测试 10: check_rule_based_permissions 独立调用 ---")
    try:
        rules = [
            PermissionRule(rule_type="allow", tool_pattern="Read"),
            PermissionRule(rule_type="deny", tool_pattern="Write"),
        ]
        result = check_rule_based_permissions("Read", {"file_path": "/a.txt"}, rules)
        assert result is not None, "应匹配到规则"
        assert result.decision == PermissionDecision.ALLOW, f"期望 ALLOW，实际 {result.decision}"

        result2 = check_rule_based_permissions("Write", {"file_path": "/b.txt"}, rules)
        assert result2 is not None, "应匹配到规则"
        assert result2.decision == PermissionDecision.DENY, f"期望 DENY，实际 {result2.decision}"

        result3 = check_rule_based_permissions("Bash", {"command": "ls"}, rules)
        assert result3 is None, "无匹配规则应返回 None"
        print("  allow 匹配: OK")
        print("  deny 匹配: OK")
        print("  无匹配: OK")
        print("  [PASS]")
        passed += 1
    except AssertionError as e:
        print(f"  [FAIL] {e}")
        failed += 1

    print("\n" + "=" * 60)
    print(f"测试完成: {passed} 通过, {failed} 失败")
    print("=" * 60)
