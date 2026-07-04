"""Skill 列表格式化与预算注入。

将可用 skill 的 name + description + when_to_use 格式化为 system-reminder 消息，
带 token 预算控制（不超过上下文窗口的 1%），且只发送增量（避免重复注入）。
"""

from __future__ import annotations

from tools.skills.types import Skill


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

# skill 列表占上下文窗口的比例
SKILL_BUDGET_CONTEXT_PERCENT = 0.01

# 单条 skill 描述的最大字符数
MAX_LISTING_DESC_CHARS = 250

# 粗略 token 估算：字符数 ÷ 4
BYTES_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# format_skills_within_budget — 按预算格式化 skill 列表
# ---------------------------------------------------------------------------


def format_skills_within_budget(
    skills: list[Skill],
    context_window: int,
) -> str:
    """按 token 预算格式化 skill 列表。

    每条格式：- {name}: {description} - {when_to_use}
    超预算时：先截断 description，极端情况只留 name。

    Args:
        skills: 可用 skill 列表
        context_window: 上下文窗口大小（token 数）

    Returns:
        格式化后的 skill 列表文本
    """
    budget_chars = int(context_window * SKILL_BUDGET_CONTEXT_PERCENT * BYTES_PER_TOKEN)
    lines: list[str] = []
    used_chars = 0

    for skill in skills:
        # 构建单条 skill 描述
        desc = skill.description
        when_to_use = skill.when_to_use or ""

        # 组装完整行
        full_line = f"- {skill.name}: {desc}"
        if when_to_use:
            full_line += f" - {when_to_use}"

        # 检查预算
        if used_chars + len(full_line) <= budget_chars:
            lines.append(full_line)
            used_chars += len(full_line)
            continue

        # 超预算 → 截断 description
        truncated_desc = desc[:MAX_LISTING_DESC_CHARS]
        if len(truncated_desc) < len(desc):
            truncated_desc += "..."
        truncated_line = f"- {skill.name}: {truncated_desc}"
        if when_to_use:
            short_when = when_to_use[:100]
            if len(short_when) < len(when_to_use):
                short_when += "..."
            truncated_line += f" - {short_when}"

        if used_chars + len(truncated_line) <= budget_chars:
            lines.append(truncated_line)
            used_chars += len(truncated_line)
            continue

        # 极端情况：只留 name
        name_only = f"- {skill.name}"
        if used_chars + len(name_only) <= budget_chars:
            lines.append(name_only)
            used_chars += len(name_only)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# get_skill_listing_attachment — 获取增量 skill 列表消息
# ---------------------------------------------------------------------------


def get_skill_listing_attachment(
    skills: list[Skill],
    sent_skills: set[str],
    context_window: int = 200_000,
) -> dict | None:
    """获取 skill 列表的 system-reminder 消息（增量注入）。

    只包含尚未发送过的 skill（基于 sent_skills 集合判断）。
    若无新增 skill，返回 None。

    Args:
        skills: 当前所有可用的 skill
        sent_skills: 已发送过的 skill 名称集合（会被更新）
        context_window: 上下文窗口大小

    Returns:
        system-reminder 格式的 user 消息 dict，或 None（无新增）
    """
    # 筛选新增 skill
    new_skills = [s for s in skills if s.name not in sent_skills]
    if not new_skills:
        return None

    # 格式化
    listing_text = format_skills_within_budget(new_skills, context_window)
    if not listing_text:
        return None

    # 更新已发送集合
    for s in new_skills:
        sent_skills.add(s.name)

    # 构造 system-reminder 消息
    return {
        "role": "user",
        "content": (
            "<system-reminder>\n"
            "The following skills are available for use with the Skill tool:\n\n"
            f"{listing_text}\n\n"
            "When a skill matches the user's request, invoke the relevant "
            "Skill tool before generating any other response.\n"
            "</system-reminder>\n"
        ),
    }
