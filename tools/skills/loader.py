"""文件型 skill 加载器。

从多个来源加载 SKILL.md 格式的 skill：
- 用户级：~/.agent/skills/<name>/SKILL.md
- 项目级：从 cwd 向上到 home 的每一级 .agent/skills/<name>/SKILL.md

同名 skill 项目级覆盖用户级（深路径优先于浅路径）。
结果 memoize，配置变更后调 clear_cache() 清缓存。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from functools import lru_cache

from tools.skills.frontmatter import parse_frontmatter, SkillFrontmatter
from tools.skills.types import Skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------

SKILL_FILE_NAME = "SKILL.md"
SKILLS_DIR_NAME = "skills"
AGENT_DIR_NAME = ".agent"


# ---------------------------------------------------------------------------
# get_file_skills — 获取所有文件型 skill
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_file_skills() -> tuple[Skill, ...]:
    """获取所有文件型 skill（用户级 + 项目级），去重。

    返回 tuple（不可变，配合 lru_cache）。
    同名 skill 项目级覆盖用户级，深路径优先。

    失败的 skill（解析错误、缺少必填字段）跳过并记录 warning。
    """
    skills: list[Skill] = []
    seen_names: set[str] = set()

    # 按优先级从高到低加载：项目级（深路径→浅路径）→ 用户级
    for skill_dir in _get_skill_dirs():
        if not skill_dir.is_dir():
            continue

        # 遍历目录下的子目录（每个子目录是一个 skill）
        for entry in sorted(skill_dir.iterdir()):
            if not entry.is_dir():
                continue

            skill_file = entry / SKILL_FILE_NAME
            if not skill_file.is_file():
                continue

            skill_name = entry.name
            if skill_name in seen_names:
                # 高优先级来源已加载同名 skill，跳过
                continue

            skill = _load_skill_file(skill_file, skill_name)
            if skill is not None:
                skills.append(skill)
                seen_names.add(skill_name)

    return tuple(skills)


# ---------------------------------------------------------------------------
# get_skill_dirs — 获取所有 skill 目录路径（按优先级排序）
# ---------------------------------------------------------------------------


def _get_skill_dirs() -> list[Path]:
    """获取所有 skill 目录路径，按优先级从高到低排序。

    优先级：
    1. 项目级：从 cwd 向上到 home 的每一级 .agent/skills（深路径优先）
    2. 用户级：~/.agent/skills
    """
    dirs: list[Path] = []

    # 项目级：从 cwd 向上到 home
    cwd = Path(os.getcwd()).resolve()
    home = Path(os.path.expanduser("~")).resolve()

    current = cwd
    while current is not None:
        skill_dir = current / AGENT_DIR_NAME / SKILLS_DIR_NAME
        dirs.append(skill_dir)

        # 到 home 就停
        if current == home or current == current.parent:
            break
        current = current.parent

    # 用户级
    user_dir = home / AGENT_DIR_NAME / SKILLS_DIR_NAME
    dirs.append(user_dir)

    return dirs


# ---------------------------------------------------------------------------
# _load_skill_file — 加载单个 SKILL.md
# ---------------------------------------------------------------------------


def _load_skill_file(file_path: Path, skill_name: str) -> Skill | None:
    """加载单个 SKILL.md 文件，构造 Skill 对象。

    解析失败时返回 None 并记录 warning。
    """
    try:
        text = file_path.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("读取 skill 文件失败 %s: %s", file_path, e)
        return None

    # 解析 frontmatter
    try:
        fm, content = parse_frontmatter(text)
    except ValueError as e:
        logger.warning("skill frontmatter 解析失败 %s: %s", file_path, e)
        return None

    # 校验必填字段
    name = fm.name or skill_name
    description = fm.description or ""
    if not description:
        logger.warning("skill '%s' 缺少 description 字段，跳过: %s", name, file_path)
        return None

    # 构造 get_prompt 函数：做变量替换
    skill_root = str(file_path.parent)
    resolved_content = _substitute_variables(content, skill_root)

    def get_prompt(args: str = "") -> str:
        """获取 skill 正文，做参数替换。"""
        result = resolved_content
        if args:
            result = _substitute_args(result, args)
        return result

    # 构造 Skill
    skill = Skill(
        name=name,
        description=description,
        when_to_use=fm.when_to_use,
        content=content,
        get_prompt=get_prompt,
        allowed_tools=fm.allowed_tools if fm.allowed_tools else None,
        disable_model_invocation=fm.disable_model_invocation,
        user_invocable=fm.user_invocable,
        paths=fm.paths if fm.paths else None,
        skill_root=skill_root,
        source="file",
        aliases=fm.aliases if fm.aliases else [],
    )

    logger.debug("加载 skill: %s (from %s)", name, file_path)
    return skill


# ---------------------------------------------------------------------------
# 变量替换
# ---------------------------------------------------------------------------


def _substitute_variables(text: str, skill_root: str) -> str:
    """替换 skill 正文中的变量。

    支持的变量：
    - ${CLAUDE_SKILL_DIR} → skill 根目录路径
    - ${CLAUDE_SESSION_ID} → 占位（当前无 session 上下文，保留原样）
    """
    text = text.replace("${CLAUDE_SKILL_DIR}", skill_root)
    # CLAUDE_SESSION_ID 在运行时由调用方替换，此处不做
    return text


def _substitute_args(text: str, args: str) -> str:
    """替换 skill 正文中的参数占位符。

    将 $arg 替换为 args 值，$1/$2/... 替换为按空格分割的参数。
    """
    if not args:
        return text

    # $arg → 完整 args
    text = text.replace("$arg", args)

    # $1, $2, ... → 按空格分割的参数
    parts = args.split()
    for i, part in enumerate(parts, start=1):
        text = text.replace(f"${i}", part)

    return text


# ---------------------------------------------------------------------------
# clear_cache — 清缓存
# ---------------------------------------------------------------------------


def clear_cache() -> None:
    """清除 skill 加载缓存。

    配置变更或目录变更后调用，下次 get_file_skills() 会重新扫描。
    """
    get_file_skills.cache_clear()
