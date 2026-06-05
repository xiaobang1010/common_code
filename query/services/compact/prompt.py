"""压缩提示词模块。

参考原始 TypeScript 实现 src/services/compact/prompt.ts。
提供压缩系统提示词和用户提示词模板。
"""

from __future__ import annotations

import re


# ---------------------------------------------------------------------------
# NO TOOLS 前导声明
# ---------------------------------------------------------------------------

NO_TOOLS_PREAMBLE = """CRITICAL: Respond with TEXT ONLY. Do NOT call any tools.

- Do NOT use Read, Bash, Grep, Glob, Edit, Write, or ANY other tool.
- You already have all the context you need in the conversation above.
- Tool calls will be REJECTED and will waste your only turn — you will fail the task.
- Your entire response must be plain text: an <analysis> block followed by a <summary> block.

"""

# ---------------------------------------------------------------------------
# 分析指令
# ---------------------------------------------------------------------------

DETAILED_ANALYSIS_INSTRUCTION_BASE = """Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Chronologically analyze each message and section of the conversation. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""

DETAILED_ANALYSIS_INSTRUCTION_PARTIAL = """Before providing your final summary, wrap your analysis in <analysis> tags to organize your thoughts and ensure you've covered all necessary points. In your analysis process:

1. Analyze the recent messages chronologically. For each section thoroughly identify:
   - The user's explicit requests and intents
   - Your approach to addressing the user's requests
   - Key decisions, technical concepts and code patterns
   - Specific details like:
     - file names
     - full code snippets
     - function signatures
     - file edits
   - Errors that you ran into and how you fixed them
   - Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
2. Double-check for technical accuracy and completeness, addressing each required element thoroughly."""

# ---------------------------------------------------------------------------
# 基础压缩提示词
# ---------------------------------------------------------------------------

BASE_COMPACT_PROMPT = f"""Your task is to create a detailed summary of the conversation so far, paying close attention to the user's explicit requests and your previous actions.
This summary should be thorough in capturing technical details, code patterns, and architectural decisions that would be essential for continuing development work without losing context.

{DETAILED_ANALYSIS_INSTRUCTION_BASE}

Your summary should include the following sections:

1. Primary Request and Intent: Capture all of the user's explicit requests and intents in detail
2. Key Technical Concepts: List all important technical concepts, technologies, and frameworks discussed.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Pay special attention to the most recent messages and include full code snippets where applicable and include a summary of why this file read or edit is important.
4. Errors and fixes: List all errors that you ran into, and how you fixed them. Pay special attention to specific user feedback that you received, especially if the user told you to do something differently.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages that are not tool results. These are critical for understanding the users' feedback and changing intent.
7. Pending Tasks: Outline any pending tasks that you have explicitly been asked to work on.
8. Current Work: Describe in detail precisely what was being worked on immediately before this summary request, paying special attention to the most recent messages from both user and assistant. Include file names and code snippets where applicable.
9. Optional Next Step: List the next step that you will take that is related to the most recent work you were doing. IMPORTANT: ensure that this step is DIRECTLY in line with the user's most recent explicit requests, and the task you were working on immediately before this summary request. If your last task was concluded, then only list next steps if they are explicitly in line with the users request. Do not start on tangential requests or really old requests that were already completed without confirming with the user first.
                       If there is a next step, include direct quotes from the most recent conversation showing exactly what task you were working on and where you left off. This should be verbatim to ensure there's no drift in task interpretation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]
   - [...]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Summary of the changes made to this file, if any]
      - [Important Code Snippet]
   - [File Name 2]
      - [Important Code Snippet]
   - [...]

4. Errors and fixes:
    - [Detailed description of error 1]:
      - [How you fixed the error]
      - [User feedback on the error if any]
    - [...]

5. Problem Solving:
   [Description of solved problems and ongoing troubleshooting]

6. All user messages:
    - [Detailed non tool use user message]
    - [...]

7. Pending Tasks:
    - [Task 1]
    - [Task 2]
    - [...]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the conversation so far, following this structure and ensuring precision and thoroughness in your response.

There may be additional summarization instructions provided in the included context. If so, remember to follow these instructions when creating your summary. Examples of instructions include:
<example>
## Compact Instructions
When summarizing the conversation focus on typescript code changes and also remember the mistakes you made and how you fixed them.
</example>

<example>
# Summary instructions
When you are using compact - please focus on test output and code changes. Include file reads verbatim.
</example>

"""

# ---------------------------------------------------------------------------
# 部分压缩提示词
# ---------------------------------------------------------------------------

PARTIAL_COMPACT_PROMPT = f"""Your task is to create a detailed summary of the RECENT portion of the conversation — the messages that follow earlier retained context. The earlier messages are being kept intact and do NOT need to be summarized. Focus your summary on what was discussed, learned, and accomplished in the recent messages only.

{DETAILED_ANALYSIS_INSTRUCTION_PARTIAL}

Your summary should include the following sections:

1. Primary Request and Intent: Capture the user's explicit requests and intents from the recent messages
2. Key Technical Concepts: List important technical concepts, technologies, and frameworks discussed recently.
3. Files and Code Sections: Enumerate specific files and code sections examined, modified, or created. Include full code snippets where applicable and include a summary of why this file is important.
4. Errors and fixes: List errors encountered and how they were fixed.
5. Problem Solving: Document problems solved and any ongoing troubleshooting efforts.
6. All user messages: List ALL user messages from the recent portion that are not tool results.
7. Pending Tasks: Outline any pending tasks from the recent messages.
8. Current Work: Describe precisely what was being worked on immediately before this summary request.
9. Optional Next Step: List the next step related to the most recent work. Include direct quotes from the most recent conversation.

Here's an example of how your output should be structured:

<example>
<analysis>
[Your thought process, ensuring all points are covered thoroughly and accurately]
</analysis>

<summary>
1. Primary Request and Intent:
   [Detailed description]

2. Key Technical Concepts:
   - [Concept 1]
   - [Concept 2]

3. Files and Code Sections:
   - [File Name 1]
      - [Summary of why this file is important]
      - [Important Code Snippet]

4. Errors and fixes:
    - [Error description]:
      - [How you fixed it]

5. Problem Solving:
   [Description]

6. All user messages:
    - [Detailed non tool use user message]

7. Pending Tasks:
    - [Task 1]

8. Current Work:
   [Precise description of current work]

9. Optional Next Step:
   [Optional Next step to take]

</summary>
</example>

Please provide your summary based on the RECENT messages only (after the retained earlier context), following this structure and ensuring precision and thoroughness in your response.
"""

# ---------------------------------------------------------------------------
# NO TOOLS 尾部声明
# ---------------------------------------------------------------------------

NO_TOOLS_TRAILER = (
    "\n\nREMINDER: Do NOT call any tools. Respond with plain text only — "
    "an <analysis> block followed by a <summary> block. "
    "Tool calls will be rejected and you will fail the task."
)


# ---------------------------------------------------------------------------
# 公共接口
# ---------------------------------------------------------------------------


def get_compact_prompt(custom_instructions: str | None = None) -> str:
    """获取全量压缩提示词。

    Args:
        custom_instructions: 附加的自定义指令

    Returns:
        完整的压缩系统提示词
    """
    prompt = NO_TOOLS_PREAMBLE + BASE_COMPACT_PROMPT

    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"

    prompt += NO_TOOLS_TRAILER

    return prompt


def get_partial_compact_prompt(
    custom_instructions: str | None = None,
) -> str:
    """获取部分压缩提示词。

    Args:
        custom_instructions: 附加的自定义指令

    Returns:
        完整的部分压缩系统提示词
    """
    prompt = NO_TOOLS_PREAMBLE + PARTIAL_COMPACT_PROMPT

    if custom_instructions and custom_instructions.strip():
        prompt += f"\n\nAdditional Instructions:\n{custom_instructions}"

    prompt += NO_TOOLS_TRAILER

    return prompt


def format_compact_summary(summary: str) -> str:
    """格式化压缩摘要。

    去除 <analysis> 草稿区，将 <summary> XML 标签替换为可读的节标题。

    Args:
        summary: 原始摘要字符串，可能包含 <analysis> 和 <summary> XML 标签

    Returns:
        格式化后的摘要
    """
    formatted = summary

    # 去除 analysis 区域 — 它是草稿区，提高摘要质量但无信息价值
    formatted = re.sub(r"<analysis>[\s\S]*?</analysis>", "", formatted)

    # 提取并格式化 summary 区域
    summary_match = re.search(r"<summary>([\s\S]*?)</summary>", formatted)
    if summary_match:
        content = summary_match.group(1) or ""
        formatted = re.sub(
            r"<summary>[\s\S]*?</summary>",
            f"Summary:\n{content.strip()}",
            formatted,
        )

    # 清理节间多余空行
    formatted = re.sub(r"\n\n+", "\n\n", formatted)

    return formatted.strip()


def get_compact_user_summary_message(
    summary: str,
    suppress_follow_up_questions: bool = False,
    recent_messages_preserved: bool = False,
) -> str:
    """构建压缩后的用户摘要消息。

    Args:
        summary: 原始摘要文本
        suppress_follow_up_questions: 是否抑制后续问题
        recent_messages_preserved: 是否保留了近期消息

    Returns:
        格式化的用户摘要消息
    """
    formatted_summary = format_compact_summary(summary)

    base_summary = (
        "This session is being continued from a previous conversation "
        "that ran out of context. The summary below covers the earlier "
        "portion of the conversation.\n\n"
        f"{formatted_summary}"
    )

    if recent_messages_preserved:
        base_summary += "\n\nRecent messages are preserved verbatim."

    if suppress_follow_up_questions:
        continuation = (
            f"{base_summary}\n"
            "Continue the conversation from where it left off without "
            "asking the user any further questions. Resume directly — "
            "do not acknowledge the summary, do not recap what was "
            'happening, do not preface with "I\'ll continue" or similar. '
            "Pick up the last task as if the break never happened."
        )
        return continuation

    return base_summary


def build_compact_prompt(messages_to_compact: list[dict]) -> str:
    """构建压缩请求的提示词。

    将待压缩消息序列化为文本，附加到压缩提示词模板中。

    Args:
        messages_to_compact: 待压缩的消息列表（dict 格式）

    Returns:
        完整的压缩请求提示词
    """
    import json

    # 序列化消息为文本
    serialized_parts: list[str] = []
    for msg in messages_to_compact:
        role = msg.get("role", "unknown")
        content = msg.get("content", "")
        if isinstance(content, str):
            serialized_parts.append(f"[{role}]: {content}")
        else:
            serialized_parts.append(f"[{role}]: {json.dumps(content, ensure_ascii=False)}")

    conversation_text = "\n".join(serialized_parts)

    # 使用全量压缩提示词
    prompt = get_compact_prompt()
    prompt += f"\n\n<conversation>\n{conversation_text}\n</conversation>"

    return prompt


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("压缩提示词测试")
    print("=" * 60)

    # ---- 测试 1: get_compact_prompt ----
    print("\n--- 测试 1: get_compact_prompt ---")
    try:
        prompt = get_compact_prompt()
        assert "CRITICAL: Respond with TEXT ONLY" in prompt
        assert "<analysis>" in prompt
        assert "<summary>" in prompt
        assert "REMINDER: Do NOT call any tools" in prompt
        print(f"  提示词长度: {len(prompt)} 字符")
        print("  [PASS] 基础压缩提示词包含必要元素")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 2: get_compact_prompt 带自定义指令 ----
    print("\n--- 测试 2: get_compact_prompt 带自定义指令 ---")
    try:
        prompt = get_compact_prompt("Focus on Python code changes")
        assert "Additional Instructions:" in prompt
        assert "Focus on Python code changes" in prompt
        print("  [PASS] 自定义指令正确附加")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 3: get_partial_compact_prompt ----
    print("\n--- 测试 3: get_partial_compact_prompt ---")
    try:
        prompt = get_partial_compact_prompt()
        assert "RECENT portion" in prompt
        assert "REMINDER: Do NOT call any tools" in prompt
        print(f"  提示词长度: {len(prompt)} 字符")
        print("  [PASS] 部分压缩提示词包含必要元素")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 4: format_compact_summary ----
    print("\n--- 测试 4: format_compact_summary ---")
    try:
        raw = "<analysis>Some thinking here</analysis>\n\n<summary>\n1. Test point\n2. Another point\n</summary>"
        formatted = format_compact_summary(raw)
        assert "<analysis>" not in formatted
        assert "<summary>" not in formatted
        assert "Summary:" in formatted
        assert "1. Test point" in formatted
        print(f"  格式化结果:\n{formatted[:200]}...")
        print("  [PASS] 摘要格式化正确")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 5: get_compact_user_summary_message ----
    print("\n--- 测试 5: get_compact_user_summary_message ---")
    try:
        msg = get_compact_user_summary_message(
            "<summary>Test summary</summary>",
            suppress_follow_up_questions=True,
        )
        assert "continued from a previous conversation" in msg
        assert "Continue the conversation" in msg
        print("  [PASS] 抑制后续问题的摘要消息")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 6: build_compact_prompt ----
    print("\n--- 测试 6: build_compact_prompt ---")
    try:
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi there!"},
            {"role": "user", "content": "Help me with Python"},
        ]
        prompt = build_compact_prompt(messages)
        assert "<conversation>" in prompt
        assert "[user]: Hello" in prompt
        assert "[assistant]: Hi there!" in prompt
        print("  [PASS] 构建压缩提示词包含对话内容")
    except Exception as e:
        print(f"  [FAIL] {e}")

    # ---- 测试 7: format_compact_summary 无 XML 标签 ----
    print("\n--- 测试 7: format_compact_summary 无 XML 标签 ---")
    try:
        raw = "Plain text summary without XML tags"
        formatted = format_compact_summary(raw)
        assert formatted == raw
        print("  [PASS] 无 XML 标签时原样返回")
    except Exception as e:
        print(f"  [FAIL] {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
