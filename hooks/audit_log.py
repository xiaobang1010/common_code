#!/usr/bin/env python3
"""PostToolUse hook: 文件变更审计日志。"""
import json
import os
import sys
from datetime import datetime, timezone

AUDIT_LOG_DIR = ".agent"
AUDIT_LOG_FILE = "audit-log.jsonl"

def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        return

    tool_name = data.get("tool_name", "unknown")
    tool_input = data.get("tool_input", {})
    tool_response = data.get("tool_response", {})

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "tool": tool_name,
        "input_summary": _summarize_input(tool_name, tool_input),
        "status": "success" if tool_response.get("exitCode", 0) == 0 else "failed",
    }

    project_dir = os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CODEBUDDY_PROJECT_DIR") or data.get("cwd", ".")
    log_dir = os.path.join(project_dir, AUDIT_LOG_DIR)
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, AUDIT_LOG_FILE)

    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(json.dumps({"continue": True}))

def _summarize_input(tool_name: str, tool_input: dict) -> str:
    if tool_name in ("Bash", "PowerShell"):
        return tool_input.get("command", "")[:200]
    elif tool_name in ("Write", "Edit", "MultiEdit"):
        return tool_input.get("file_path", "") or tool_input.get("path", "")
    return json.dumps(tool_input, ensure_ascii=False)[:200]

if __name__ == "__main__":
    main()
