"""权限决策核心模块。

实现完整的权限决策管线，按 permission_mode 分流：
- default（自动编辑）：只读放行、文件编辑放行、安全命令放行；
  删文件、读敏感文件、危险命令才确认
- full_access（完全访问）：全部放行，除非模型主动调 AskUserQuestion

按优先级依次检查：
1. deny 规则（所有模式生效）
2. full_access 模式：直接 ALLOW
3. 敏感文件读取检查（default 模式）
4. 安全路径检查（default 模式）：.git/.claude 写入 → ASK
5. default 模式分流：只读放行、编辑放行、Bash 按风险分级
6. allow 规则
7. 默认：default ASK / full_access ALLOW
"""

from __future__ import annotations

import fnmatch
import os
import re
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
# 权限模式
# ---------------------------------------------------------------------------

# 默认模式（自动编辑）：只读放行、文件编辑放行、安全命令放行，
# 删文件、读敏感文件、危险命令才确认
MODE_DEFAULT = "default"

# 完全访问模式：全部放行，除非模型主动调 AskUserQuestion
MODE_FULL_ACCESS = "full_access"

# 所有合法模式
VALID_MODES = {MODE_DEFAULT, MODE_FULL_ACCESS}


# ---------------------------------------------------------------------------
# 工具分类
# ---------------------------------------------------------------------------

# 只读工具：直接放行（default 模式）
READONLY_TOOLS: set[str] = {
    "Read",
    "Glob",
    "Grep",
    "Skill",
    "LS",
    "TodoRead",
    "TodoWrite",
}

# 文件编辑工具：自动放行（default 模式）
FILE_EDIT_TOOLS: set[str] = {
    "Write",
    "Edit",
    "MultiEdit",
}

# 需要权限的工具（旧逻辑保留，用于兼容）
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
# 敏感文件模式（读取这些文件需要确认）
# ---------------------------------------------------------------------------

# 敏感文件名/扩展名模式
_SENSITIVE_FILE_PATTERNS: list[str] = [
    ".env",
    ".env.",
    ".key",
    ".pem",
    ".secret",
    "credentials",
    "id_rsa",
    "id_ed25519",
    ".ssh/",
    ".gitconfig",
    ".npmrc",
    ".pypirc",
]

# 敏感文件完整路径正则（编译一次）
_SENSITIVE_PATH_REGEX = re.compile(
    r"(?:^|/)(?:"
    r"\.env(?:\.[^/]*)?"        # .env / .env.local / .env.production
    r"|[^/]*\.key"              # *.key
    r"|[^/]*\.pem"              # *.pem
    r"|[^/]*\.secret"           # *.secret
    r"|credentials[^/]*"        # credentials / credentials.json
    r"|id_rsa"                  # id_rsa
    r"|id_ed25519"              # id_ed25519
    r"|\.ssh/[^/]*"             # .ssh/*
    r"|\.gitconfig"             # .gitconfig
    r"|\.npmrc"                 # .npmrc
    r"|\.pypirc"                # .pypirc
    r")(?:$|/)",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Bash 命令风险分级
# ---------------------------------------------------------------------------

# 安全命令前缀（只读操作，default 模式直接放行）
_SAFE_COMMAND_PATTERNS: list[str] = [
    r"^ls\b",
    r"^cat\b",
    r"^head\b",
    r"^tail\b",
    r"^less\b",
    r"^more\b",
    r"^echo\b",
    r"^pwd\b",
    r"^whoami\b",
    r"^date\b",
    r"^which\b",
    r"^where\b",
    r"^find\b.*(?:-name|-type|-size|-mtime)",  # find 只读模式
    r"^grep\b",
    r"^rg\b",
    r"^git\s+(?:status|log|diff|branch|show|blame|remote|config\s+-l)\b",
    r"^node\s+--version\b",
    r"^npm\s+--version\b",
    r"^python\s+--version\b",
    r"^pip\s+--version\b",
    r"^uv\s+--version\b",
    r"^type\b",
    r"^file\b",
    r"^wc\b",
    r"^sort\b",
    r"^uniq\b",
    r"^diff\b",
    r"^tree\b",
]

# 危险命令模式（default 模式需要确认）
_DANGEROUS_COMMAND_PATTERNS: list[str] = [
    r"\brm\b",
    r"\bmkfs\b",
    r"\bdd\b",
    r"\bchmod\s+-R\b",
    r"\bchown\s+-R\b",
    r":\(\)\s*\{",
    r">\s*/dev/sd",
    r"\bformat\b",
    r"\bdel\s+/[fs]\b",
    r"\brmdir\b",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bkillall\b",
    r"\bpkill\b",
]

# 编译正则
_SAFE_COMMAND_REGEX = [re.compile(p, re.IGNORECASE) for p in _SAFE_COMMAND_PATTERNS]
_DANGEROUS_COMMAND_REGEX = [re.compile(p, re.IGNORECASE) for p in _DANGEROUS_COMMAND_PATTERNS]


def _is_safe_command(command: str) -> bool:
    """判断 Bash 命令是否安全（只读，无副作用）。"""
    command = command.strip()
    for pattern in _SAFE_COMMAND_REGEX:
        if pattern.search(command):
            return True
    return False


def _is_dangerous_command(command: str) -> bool:
    """判断 Bash 命令是否危险（有破坏性）。"""
    command = command.strip()
    for pattern in _DANGEROUS_COMMAND_REGEX:
        if pattern.search(command):
            return True
    return False


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


def is_sensitive_file(path: str) -> bool:
    """检查路径是否是敏感文件（.env / *.key / *.pem / credentials 等）。

    Args:
        path: 待检查的文件路径。

    Returns:
        True 表示是敏感文件。
    """
    normalized = path.replace("\\", "/")
    return bool(_SENSITIVE_PATH_REGEX.search(normalized))


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
    """检查工具输入是否涉及不安全路径（.git/ .claude/）。

    Returns:
        True 表示安全，False 表示涉及受保护目录。
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


def _check_sensitive_file_read(tool_name: str, tool_input: dict) -> bool:
    """检查是否在读取敏感文件（.env / *.key / credentials 等）。

    Returns:
        True 表示涉及敏感文件读取。
    """
    if tool_name not in ("Read",):
        return False

    for value in tool_input.values():
        if isinstance(value, str) and is_sensitive_file(value):
            return True
        if isinstance(value, (list, tuple)):
            for item in value:
                if isinstance(item, str) and is_sensitive_file(item):
                    return True
        if isinstance(value, dict):
            for v in value.values():
                if isinstance(v, str) and is_sensitive_file(v):
                    return True
    return False


def _extract_bash_command(tool_input: dict) -> str:
    """从 Bash 工具输入中提取命令字符串。"""
    for key in ("command", "cmd", "script"):
        if key in tool_input and isinstance(tool_input[key], str):
            return tool_input[key]
    # 兜底：拼接所有字符串值
    return " ".join(v for v in tool_input.values() if isinstance(v, str))


# ---------------------------------------------------------------------------
# 完整权限决策管线
# ---------------------------------------------------------------------------


def has_permissions_to_use_tool(
    tool_name: str,
    tool_input: dict,
    context: dict | None = None,
) -> PermissionResult:
    """完整权限决策管线，按 permission_mode 分流。

    Args:
        tool_name: 工具名。
        tool_input: 工具输入字典。
        context: 上下文字典，可包含：
            - "permission_mode": str — 权限模式（default / full_access）
            - "deny_rules": list[PermissionRule] — deny 规则列表
            - "ask_rules": list[PermissionRule] — ask 规则列表
            - "allow_rules": list[PermissionRule] — allow 规则列表
            - "bypass_permissions": bool — 是否绕过权限（兼容旧逻辑）

    Returns:
        PermissionResult 决策结果。
    """
    ctx = context or {}
    permission_mode: str = ctx.get("permission_mode", MODE_DEFAULT)
    deny_rules: list[PermissionRule] = ctx.get("deny_rules", [])
    ask_rules: list[PermissionRule] = ctx.get("ask_rules", [])
    allow_rules: list[PermissionRule] = ctx.get("allow_rules", [])
    # bypass_permissions 兼容旧逻辑，等价于 full_access
    bypass_permissions: bool = ctx.get("bypass_permissions", False)

    # 1. deny 规则优先（所有模式生效）
    deny_result = check_rule_based_permissions(tool_name, tool_input, deny_rules)
    if deny_result is not None:
        return deny_result

    # 2. ask 规则
    ask_result = check_rule_based_permissions(tool_name, tool_input, ask_rules)
    if ask_result is not None:
        return ask_result

    # 3. full_access 模式：直接放行（deny 已在上面检查）
    if permission_mode == MODE_FULL_ACCESS or bypass_permissions:
        return PermissionResult(
            decision=PermissionDecision.ALLOW,
            reason="Full access mode is active.",
        )

    # --- 以下为 default 模式的细分判定 ---

    # 4. 敏感文件读取检查：读敏感文件 → ASK
    if _check_sensitive_file_read(tool_name, tool_input):
        return PermissionResult(
            decision=PermissionDecision.ASK,
            reason="Reading sensitive file requires confirmation.",
        )

    # 5. 安全路径检查：.git/.claude 写入 → ASK
    if not _check_input_safety(tool_input):
        return PermissionResult(
            decision=PermissionDecision.ASK,
            reason="Input involves protected directory (.git/ or .claude/).",
        )

    # 6. 只读工具直接放行
    if tool_name in READONLY_TOOLS:
        return PermissionResult(
            decision=PermissionDecision.ALLOW,
            reason=f"Read-only tool {tool_name} is allowed.",
        )

    # 7. 文件编辑工具自动放行（对齐 ZCode edit 模式）
    if tool_name in FILE_EDIT_TOOLS:
        return PermissionResult(
            decision=PermissionDecision.ALLOW,
            reason=f"File edit tool {tool_name} is allowed in default mode.",
        )

    # 8. Bash 命令风险分级
    if tool_name in ("Bash", "PowerShell"):
        command = _extract_bash_command(tool_input)
        if _is_dangerous_command(command):
            return PermissionResult(
                decision=PermissionDecision.ASK,
                reason=f"Dangerous command requires confirmation: {command[:80]}",
            )
        if _is_safe_command(command):
            return PermissionResult(
                decision=PermissionDecision.ALLOW,
                reason=f"Safe command is allowed: {command[:80]}",
            )
        # 其他命令保守策略：ASK
        return PermissionResult(
            decision=PermissionDecision.ASK,
            reason=f"Command requires confirmation: {command[:80]}",
        )

    # 9. allow 规则
    allow_result = check_rule_based_permissions(tool_name, tool_input, allow_rules)
    if allow_result is not None:
        return allow_result

    # 10. 默认 ASK（default 模式保守兜底）
    return PermissionResult(
        decision=PermissionDecision.ASK,
        reason=f"No rule matched for {tool_name}, defaulting to ask.",
    )
