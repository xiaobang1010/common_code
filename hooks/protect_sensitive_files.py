#!/usr/bin/env python3
"""PreToolUse hook: 保护敏感文件不被写入。"""
import json
import os
import re
import sys

SENSITIVE_PATTERNS = [
    r"\.env$",
    r"\.env\.",
    r"credentials",
    r"\.key$",
    r"\.pem$",
    r"\.secret$",
    r"\.ssh[/\\]",
    r"id_rsa",
    r"id_ed25519",
    r"\.gitconfig$",
    r"\.npmrc$",
    r"\.pypirc$",
]

def is_sensitive_path(file_path: str) -> str | None:
    if not file_path:
        return None
    basename = os.path.basename(file_path)
    for pattern in SENSITIVE_PATTERNS:
        if re.search(pattern, file_path) or re.search(pattern, basename):
            return pattern
    return None

def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        print(json.dumps({"continue": True}))
        return

    tool_input = data.get("tool_input", {})
    file_path = tool_input.get("file_path", "") or tool_input.get("path", "")

    matched = is_sensitive_path(file_path)
    if matched:
        print(json.dumps({
            "continue": False,
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": f"Sensitive file protected: {file_path} (matched pattern: {matched})"
            }
        }, ensure_ascii=False))
        return

    print(json.dumps({
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow"
        }
    }))

if __name__ == "__main__":
    main()
