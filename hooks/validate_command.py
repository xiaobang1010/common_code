#!/usr/bin/env python3
"""PreToolUse hook: 拦截危险命令。"""
import json
import sys

DANGEROUS_PATTERNS = [
    "rm -rf /",
    "rm -rf /*",
    "mkfs",
    "dd if=/dev/zero",
    "dd if=/dev/random",
    ":(){ :|:& };:",
    "> /dev/sda",
    "chmod -R 777 /",
    "chown -R",
]

def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        print(json.dumps({"continue": True}))
        return

    tool_input = data.get("tool_input", {})
    command = tool_input.get("command", "")

    for pattern in DANGEROUS_PATTERNS:
        if pattern in command:
            print(json.dumps({
                "continue": False,
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": f"Dangerous command detected: {pattern}"
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
