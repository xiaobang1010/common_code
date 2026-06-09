#!/usr/bin/env python3
"""PreCompact hook: 压缩前保存关键信息。"""
import json
import sys

def main():
    try:
        data = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        data = {}

    trigger = data.get("trigger", "auto")
    custom_instructions = data.get("custom_instructions", "")

    guidance_parts = [
        "Important: When compacting context, please preserve:",
        "- All code architecture decisions and their rationale",
        "- Current task progress and what remains",
        "- Any error states being actively debugged",
        "- File paths that have been modified in this session",
    ]

    if custom_instructions:
        guidance_parts.append(f"- User-specified: {custom_instructions}")

    print(json.dumps({
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PreCompact",
            "additionalContext": "\n".join(guidance_parts)
        }
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
