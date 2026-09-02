"""自定义子代理加载器 - 从 .md frontmatter 文件加载 AgentDefinition。

目录约定：
- 用户级：~/.agent/agents/*.md（可配置 model/tools/permission_mode 等）
- 项目级：<项目根>/.agent/agents/*.md（配置仅限提示词与工具白名单，
  permission_mode 强制剥离--项目文件不可提权，安全设计）

frontmatter 最小字段集：name / description / tools / disallowedTools /
model / max_turns / background / permission-mode（项目级剥离）。
markdown 正文作为系统提示词。

加载失败不静默丢弃，产生带诊断码的记录（agent_missing_frontmatter /
agent_missing_fields / agent_parse_error），供设置页展示。
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from tools.subagent.types import AgentDefinition

logger = logging.getLogger(__name__)

# 布尔真值（与 skills frontmatter 解析约定一致）
_TRUE_VALUES = {"true", "yes", "1", "on"}

# frontmatter key 归一（kebab-case / 驼峰 -> 内部字段名）
_KEY_MAP = {
    "name": "name",
    "description": "description",
    "tools": "tools",
    "disallowedtools": "disallowed_tools",
    "disallowed-tools": "disallowed_tools",
    "model": "model",
    "maxturns": "max_turns",
    "max-turns": "max_turns",
    "tokenbudget": "token_budget",
    "token-budget": "token_budget",
    "injectagentsmd": "inject_agents_md",
    "inject-agents-md": "inject_agents_md",
    "background": "background",
    "permissionmode": "permission_mode",
    "permission-mode": "permission_mode",
}


# ---------------------------------------------------------------------------
# 目录约定
# ---------------------------------------------------------------------------


def get_user_agents_dir() -> Path:
    """用户级自定义代理目录：~/.agent/agents/。"""
    return Path.home() / ".agent" / "agents"


def get_project_agents_dir() -> Path:
    """项目级自定义代理目录：<项目根>/.agent/agents/。

    用 effective_root：后台任务上下文里取任务自己的工作区
    （视图切走后不串工作区），非任务上下文回退全局 project_root()。
    """
    from server.paths import effective_root

    return Path(effective_root()) / ".agent" / "agents"


# ---------------------------------------------------------------------------
# frontmatter 解析（扁平 key: value + 行内/多行列表，够用即可）
# ---------------------------------------------------------------------------


def _extract_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """提取 frontmatter 原始键值对与 markdown 正文。

    Returns:
        (fields, body)。无 frontmatter 时 fields 为空 dict。

    Raises:
        ValueError: 有起始分隔符但无结束分隔符
    """
    lines = text.splitlines()
    start_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == "---":
            start_idx = i
            break
        if stripped:
            return {}, text
    if start_idx == -1:
        return {}, text

    end_idx = -1
    for i in range(start_idx + 1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break
    if end_idx == -1:
        raise ValueError("frontmatter 有起始分隔符 '---' 但未找到结束分隔符")

    fields: dict[str, Any] = {}
    current_list_key: str | None = None
    for raw in lines[start_idx + 1 : end_idx]:
        line = raw.rstrip()
        # 多行列表项（- item）
        if current_list_key is not None:
            item = line.strip()
            if item.startswith("- "):
                fields[current_list_key].append(_strip_quotes(item[2:].strip()))
                continue
            current_list_key = None
        if not line.strip() or line.strip().startswith("#"):
            continue
        m = re.match(r"^([A-Za-z][\w-]*)\s*:\s*(.*)$", line.strip())
        if not m:
            continue
        raw_key, raw_value = m.group(1), m.group(2).strip()
        key = _KEY_MAP.get(raw_key.lower())
        if key is None:
            continue
        if raw_value == "":
            fields[key] = []
            current_list_key = key
        elif raw_value.startswith("[") and raw_value.endswith("]"):
            inner = raw_value[1:-1].strip()
            fields[key] = [
                _strip_quotes(v.strip()) for v in inner.split(",") if v.strip()
            ] if inner else []
        elif raw_value.lower() in _TRUE_VALUES | {"false", "no", "0", "off"}:
            fields[key] = raw_value.lower() in _TRUE_VALUES
        else:
            fields[key] = _strip_quotes(raw_value)
    body = "\n".join(lines[end_idx + 1 :]).lstrip("\n")
    return fields, body


def _strip_quotes(value: str) -> str:
    """去掉成对的单/双引号。"""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


# ---------------------------------------------------------------------------
# 加载与诊断
# ---------------------------------------------------------------------------


def _parse_agent_file(path: Path, source: str) -> tuple[AgentDefinition | None, dict | None]:
    """解析单个 agent .md 文件。

    Returns:
        (definition, diagnostic)。成功时 diagnostic 为 None；
        失败时 definition 为 None，diagnostic 含 file/code/message。
    """
    diagnostic_base = {"file": str(path), "source": source}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        return None, {**diagnostic_base, "code": "agent_read_error", "message": str(e)}

    try:
        fields, body = _extract_frontmatter(text)
    except ValueError as e:
        return None, {**diagnostic_base, "code": "agent_missing_frontmatter_end", "message": str(e)}

    if not fields:
        return None, {
            **diagnostic_base,
            "code": "agent_missing_frontmatter",
            "message": "文件缺少 frontmatter（--- 包裹的元数据块）",
        }

    missing = [k for k in ("name", "description") if not fields.get(k)]
    if missing:
        return None, {
            **diagnostic_base,
            "code": "agent_missing_fields",
            "message": f"缺少必要字段: {', '.join(missing)}",
        }

    # 项目级配置剥离不可信字段：permission_mode 不生效（防提权）
    if source == "project" and "permission_mode" in fields:
        fields.pop("permission_mode")

    # 字段规整
    tools = fields.get("tools")
    if isinstance(tools, str):
        tools = [t.strip() for t in tools.split(",") if t.strip()]
    disallowed = fields.get("disallowed_tools") or []
    if isinstance(disallowed, str):
        disallowed = [t.strip() for t in disallowed.split(",") if t.strip()]
    max_turns = fields.get("max_turns")
    if isinstance(max_turns, str) and max_turns.isdigit():
        max_turns = int(max_turns)
    token_budget = fields.get("token_budget")
    if isinstance(token_budget, str) and token_budget.isdigit():
        token_budget = int(token_budget)

    definition = AgentDefinition(
        agent_type=str(fields["name"]),
        when_to_use=str(fields["description"]),
        tools=tools or None,  # 空/缺省 = 全部工具
        disallowed_tools=[*disallowed, "Agent"],  # 防递归
        model=fields.get("model") or None,
        max_turns=max_turns if isinstance(max_turns, int) else None,
        token_budget=token_budget if isinstance(token_budget, int) else None,
        # 缺省开启注入（与内置 general-purpose 一致），显式 false 关闭
        inject_agents_md=bool(fields.get("inject_agents_md", True)),
        background=bool(fields.get("background", False)),
        permission_mode=fields.get("permission_mode") or None,
        system_prompt=body or str(fields["description"]),
        source=source,
    )
    return definition, None


def load_custom_agents() -> tuple[list[AgentDefinition], list[dict]]:
    """加载全部自定义代理（用户级优先于项目级同名）。

    Returns:
        (agents, diagnostics)。diagnostics 为加载失败的诊断记录列表。
    """
    agents: dict[str, AgentDefinition] = {}
    diagnostics: list[dict] = []

    # 先项目级、后用户级：后加载的同名覆盖先加载的（用户级优先）
    for source, base_dir in (("project", get_project_agents_dir()), ("user", get_user_agents_dir())):
        if not base_dir.is_dir():
            continue
        for md_file in sorted(base_dir.glob("*.md")):
            definition, diagnostic = _parse_agent_file(md_file, source)
            if diagnostic is not None:
                logger.warning("自定义代理加载失败: %s (%s)", diagnostic["file"], diagnostic["message"])
                diagnostics.append(diagnostic)
                continue
            agents[definition.agent_type] = definition

    return list(agents.values()), diagnostics


def find_custom_agent(agent_type: str) -> AgentDefinition | None:
    """按类型查找自定义代理（用户级覆盖项目级）。"""
    agents, _ = load_custom_agents()
    for definition in agents:
        if definition.agent_type == agent_type:
            return definition
    return None
