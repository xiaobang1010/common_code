"""提示词消息转换工具。"""

from __future__ import annotations

from prompts.system.sections import SystemPromptSection


def build_system_messages(sections: list[SystemPromptSection]) -> list[dict]:
    """将段列表转换为 OpenAI system 消息列表。

    - 静态段（cache_scope 为 "static" 或 "global"）合并为一条 system 消息放在最前
    - 动态段（cache_scope 为 None）作为单独 system 消息追加
    """
    static_parts: list[str] = []
    dynamic_messages: list[dict] = []

    for section in sections:
        if section.cache_scope in ("static", "global"):
            static_parts.append(section.content)
        else:
            dynamic_messages.append({"role": "system", "content": section.content})

    messages: list[dict] = []
    if static_parts:
        messages.append({"role": "system", "content": "\n\n".join(static_parts)})

    messages.extend(dynamic_messages)
    return messages
