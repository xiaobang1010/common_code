"""SKILL.md frontmatter 解析器。

解析 SKILL.md 文件格式：
    ---
    name: commit
    description: 提交代码
    when_to_use: 当用户要提交 git 变更时
    allowed-tools: ["Bash"]
    ---

    # Commit Skill

    这里是 markdown 正文...

不依赖 pyyaml，自行解析扁平 YAML frontmatter（key: value + 列表），
足够覆盖 SKILL.md 的元数据需求。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


# ---------------------------------------------------------------------------
# SkillFrontmatter — 解析结果
# ---------------------------------------------------------------------------


@dataclass
class SkillFrontmatter:
    """SKILL.md frontmatter 解析结果。

    字段名用下划线风格（和 SKILL.md 里的 kebab-case 对应），
    由调用方映射到 Skill dataclass。
    """

    name: str | None = None
    description: str | None = None
    when_to_use: str | None = None
    allowed_tools: list[str] = field(default_factory=list)
    disable_model_invocation: bool = False
    user_invocable: bool = True
    paths: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    model: str | None = None
    argument_hint: str | None = None


# ---------------------------------------------------------------------------
# parse_frontmatter — 解析 SKILL.md
# ---------------------------------------------------------------------------

# frontmatter 分隔符
_FM_DELIMITER = "---"

# 字段名映射：SKILL.md 的 kebab-case → SkillFrontmatter 的 snake_case
_FIELD_MAP = {
    "name": "name",
    "description": "description",
    "when-to-use": "when_to_use",
    "whentouse": "when_to_use",
    "allowed-tools": "allowed_tools",
    "allowedtools": "allowed_tools",
    "disable-model-invocation": "disable_model_invocation",
    "disablemodelinvocation": "disable_model_invocation",
    "user-invocable": "user_invocable",
    "userinvocable": "user_invocable",
    "paths": "paths",
    "aliases": "aliases",
    "model": "model",
    "argument-hint": "argument_hint",
    "argumenthint": "argument_hint",
}

# 布尔真值
_TRUE_VALUES = {"true", "yes", "1", "on"}


def parse_frontmatter(text: str) -> tuple[SkillFrontmatter, str]:
    """解析 SKILL.md 文本，返回 (frontmatter, markdown 正文)。

    Args:
        text: SKILL.md 文件的完整文本

    Returns:
        (SkillFrontmatter, content) 元组。若无 frontmatter 分隔符，
        返回空 frontmatter 和原始文本。

    Raises:
        ValueError: frontmatter 格式错误（有起始分隔符但无结束分隔符）
    """
    lines = text.splitlines()

    # 找 frontmatter 起始分隔符（跳过开头的空行）
    start_idx = -1
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped == _FM_DELIMITER:
            start_idx = i
            break
        elif stripped:
            # 第一个非空行不是分隔符 → 无 frontmatter
            return SkillFrontmatter(), text

    if start_idx == -1:
        return SkillFrontmatter(), text

    # 找结束分隔符
    end_idx = -1
    for i in range(start_idx + 1, len(lines)):
        if lines[i].strip() == _FM_DELIMITER:
            end_idx = i
            break

    if end_idx == -1:
        raise ValueError(
            "frontmatter 有起始分隔符 '---' 但未找到结束分隔符"
        )

    # 解析 frontmatter 行
    fm_lines = lines[start_idx + 1 : end_idx]
    fm = _parse_fm_lines(fm_lines)

    # 正文是结束分隔符之后的部分
    content = "\n".join(lines[end_idx + 1 :])
    # 去掉开头空行
    content = content.lstrip("\n")

    return fm, content


# ---------------------------------------------------------------------------
# _parse_fm_lines — 解析 frontmatter 行
# ---------------------------------------------------------------------------


def _parse_fm_lines(lines: list[str]) -> SkillFrontmatter:
    """解析 frontmatter 行列表为 SkillFrontmatter。

    支持的格式：
      - key: value          简单键值对
      - key: "value"        带引号的字符串
      - key: [a, b, c]      行内列表
      - key:                多行列表
        - item1
        - item2
    """
    fm = SkillFrontmatter()
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # 空行跳过
        if not stripped:
            i += 1
            continue

        # 解析 key: value
        match = re.match(r"^(\S+?)\s*:\s*(.*)$", stripped)
        if not match:
            i += 1
            continue

        raw_key, raw_value = match.group(1), match.group(2)
        field_name = _FIELD_MAP.get(raw_key.lower())

        if field_name is None:
            # 未知字段，跳过
            i += 1
            continue

        # YAML 块标量：>- > |- |（折叠/字面多行字符串）
        if raw_value in (">-", ">", "|-", "|"):
            block_text, consumed = _parse_block_scalar(lines, i + 1, line, raw_value)
            _assign_field(fm, field_name, block_text)
            i += 1 + consumed
            continue

        # 值为空 → 可能是多行列表
        if not raw_value:
            values, consumed = _parse_list_block(lines, i + 1)
            _assign_field(fm, field_name, values)
            i += 1 + consumed
            continue

        # 行内列表 [a, b, c]
        if raw_value.startswith("[") and raw_value.endswith("]"):
            values = _parse_inline_list(raw_value)
            _assign_field(fm, field_name, values)
            i += 1
            continue

        # 布尔值
        if raw_value.lower() in _TRUE_VALUES:
            _assign_field(fm, field_name, True)
            i += 1
            continue
        if raw_value.lower() in {"false", "no", "0", "off"}:
            _assign_field(fm, field_name, False)
            i += 1
            continue

        # 普通字符串（去掉引号）
        value = _strip_quotes(raw_value)
        _assign_field(fm, field_name, value)
        i += 1

    return fm


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _parse_list_block(lines: list[str], start: int) -> tuple[list[str], int]:
    """解析多行列表块（- item 形式）。

    Returns:
        (values, consumed_lines) 元组
    """
    values: list[str] = []
    i = start

    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        # 以 - 开头的列表项
        if stripped.startswith("- "):
            item = _strip_quotes(stripped[2:].strip())
            values.append(item)
            i += 1
        else:
            # 不是列表项，结束
            break

    return values, i - start


def _parse_block_scalar(
    lines: list[str], start: int, key_line: str, indicator: str
) -> tuple[str, int]:
    """解析 YAML 块标量（>- > |- |）。

    indicator 含义：
      >-  折叠，换行替换为空格，去掉末尾换行
      >   折叠，换行替换为空格，保留末尾换行
      |-  字面，保留换行，去掉末尾换行
      |   字面，保留换行，保留末尾换行

    缩进规则：块内容必须比 key 行缩进更深。以第一个非空内容行的缩进
    作为块缩进，所有内容行剥离该缩进；空行不参与缩进判定。
    """
    # key 行的缩进（行首空格数）
    key_indent = len(key_line) - len(key_line.lstrip(" "))

    # 先收集属于本块的行（缩进比 key 深的行，或空行），并确定块缩进
    block_indent = -1
    block_raw: list[str] = []
    i = start
    while i < len(lines):
        line = lines[i]
        if not line.strip():
            # 空行属于块内容
            block_raw.append("")
            i += 1
            continue
        cur_indent = len(line) - len(line.lstrip(" "))
        if cur_indent > key_indent:
            block_raw.append(line)
            if block_indent < 0:
                # 第一个非空内容行确定块缩进
                block_indent = cur_indent
            i += 1
        else:
            break

    # 没有内容行
    if block_indent < 0:
        return "", i - start

    # 剥离块缩进
    block_lines = []
    for bl in block_raw:
        if bl == "":
            block_lines.append("")
        elif len(bl) >= block_indent:
            block_lines.append(bl[block_indent:])
        else:
            # 缩进不足的行保留原文（容错）
            block_lines.append(bl.lstrip(" "))

    # 去掉末尾连续空行
    while block_lines and block_lines[-1] == "":
        block_lines.pop()

    consumed = i - start

    if indicator.startswith(">"):
        # 折叠：非空行间换行替换为空格；连续空行保留为一个换行
        result_parts: list[str] = []
        prev_blank = False
        for bl in block_lines:
            if bl == "":
                if result_parts and not prev_blank:
                    result_parts.append("\n")
                prev_blank = True
            else:
                if result_parts and not prev_blank:
                    result_parts.append(" ")
                result_parts.append(bl)
                prev_blank = False
        result = "".join(result_parts)
        if indicator == ">":
            result += "\n"
    else:
        # 字面：保留换行
        result = "\n".join(block_lines)
        if indicator == "|":
            result += "\n"

    return result, consumed


def _parse_inline_list(text: str) -> list[str]:
    """解析行内列表 [a, b, c]。"""
    inner = text[1:-1].strip()
    if not inner:
        return []
    parts = [p.strip() for p in inner.split(",")]
    return [_strip_quotes(p) for p in parts if p]


def _strip_quotes(text: str) -> str:
    """去掉字符串两端的引号（单引号或双引号）。"""
    if len(text) >= 2:
        if (text[0] == '"' and text[-1] == '"') or (
            text[0] == "'" and text[-1] == "'"
        ):
            return text[1:-1]
    return text


def _assign_field(fm: SkillFrontmatter, field_name: str, value: object) -> None:
    """把解析出的值赋到 SkillFrontmatter 对应字段。

    布尔字段接收 bool，列表字段接收 list，字符串字段接收 str。
    """
    if field_name in ("disable_model_invocation", "user_invocable"):
        setattr(fm, field_name, bool(value))
    elif field_name in ("allowed_tools", "paths", "aliases"):
        if isinstance(value, list):
            getattr(fm, field_name).extend(value)
        elif isinstance(value, str):
            getattr(fm, field_name).append(value)
    else:
        setattr(fm, field_name, str(value))
