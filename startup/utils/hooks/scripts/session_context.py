#!/usr/bin/env python3
"""SessionStart hook: 注入项目上下文信息。"""
import json
import os
import subprocess
import sys

def main():
    cwd = os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CODEBUDDY_PROJECT_DIR") or os.getcwd()

    context_parts = []

    # Git branch
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=5, cwd=cwd
        )
        if result.returncode == 0:
            context_parts.append(f"Git branch: {result.stdout.strip()}")
    except Exception:
        pass

    # Project directory
    context_parts.append(f"Project directory: {cwd}")

    # Detect tech stack
    tech_stack = []
    for indicator, name in [
        ("package.json", "Node.js"),
        ("pyproject.toml", "Python"),
        ("requirements.txt", "Python"),
        ("Cargo.toml", "Rust"),
        ("go.mod", "Go"),
        ("pom.xml", "Java/Maven"),
        ("build.gradle", "Java/Gradle"),
    ]:
        if os.path.exists(os.path.join(cwd, indicator)):
            tech_stack.append(name)
    if tech_stack:
        context_parts.append(f"Tech stack: {', '.join(tech_stack)}")

    additional_context = "\n".join(context_parts)

    print(json.dumps({
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": additional_context
        }
    }, ensure_ascii=False))

if __name__ == "__main__":
    main()
