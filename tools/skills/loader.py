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


# ---------------------------------------------------------------------------
# classify_skill_source — 判断技能来源标签（工作区/个人/插件/内置）
# ---------------------------------------------------------------------------


def classify_skill_source(skill) -> str:
    """根据 skill 的 source 和 skill_root 路径推断来源标签。

    返回值用于前端展示：
    - "workspace"：项目级 .agent/skills（工作区技能）
    - "personal"：用户级 ~/.agent/skills（个人技能）
    - "plugin"：插件提供的技能
    - "bundled"：内置程序化技能
    """
    if skill.source == "plugin":
        return "plugin"
    if skill.source == "bundled":
        return "bundled"
    # source == "file"，按 skill_root 路径区分用户级/项目级
    if skill.skill_root:
        import os
        home = os.path.expanduser("~")
        # 在 home 下的 .agent/skills 是用户级（个人）
        if skill.skill_root.startswith(os.path.join(home, ".agent", "skills")):
            return "personal"
        # 其余 .agent/skills 是项目级（工作区）
        if ".agent" in skill.skill_root and "skills" in skill.skill_root:
            return "workspace"
    return "personal"


# ---------------------------------------------------------------------------
# 用户级技能目录 — 获取 ~/.agent/skills 路径
# ---------------------------------------------------------------------------


def _get_user_skills_dir() -> Path:
    """获取用户级技能目录 ~/.agent/skills/。"""
    home = Path(os.path.expanduser("~"))
    return home / AGENT_DIR_NAME / SKILLS_DIR_NAME


# ---------------------------------------------------------------------------
# create_skill_file — 在用户级创建技能文件
# ---------------------------------------------------------------------------


def create_skill_file(
    name: str,
    description: str,
    when_to_use: str = "",
    allowed_tools: list[str] | None = None,
) -> Path:
    """在用户级 ~/.agent/skills/<name>/SKILL.md 创建技能。

    Args:
        name: 技能名（也是目录名）
        description: 一句话描述（必填）
        when_to_use: 使用场景说明
        allowed_tools: 工具白名单

    Returns:
        创建的 SKILL.md 路径

    Raises:
        ValueError: 名称非法或技能已存在
    """
    # 名称校验：只允许字母数字连字符下划线
    import re
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(f"技能名只能含字母、数字、连字符、下划线：{name}")

    user_dir = _get_user_skills_dir()
    skill_dir = user_dir / name
    if skill_dir.exists():
        raise ValueError(f"技能已存在：{name}")

    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / SKILL_FILE_NAME

    # 组装 frontmatter
    fm_lines = ["---", f"name: {name}", f"description: {description}"]
    if when_to_use:
        fm_lines.append(f"when-to-use: {when_to_use}")
    if allowed_tools:
        fm_lines.append("allowed-tools:")
        for tool in allowed_tools:
            fm_lines.append(f"  - {tool}")
    fm_lines.append("disable-model-invocation: false")
    fm_lines.append("---")
    fm_lines.append("")
    fm_lines.append(f"# {name}")
    fm_lines.append("")
    fm_lines.append("<!-- 在这里编写技能正文 -->")

    skill_file.write_text("\n".join(fm_lines), encoding="utf-8")
    clear_cache()
    return skill_file


# ---------------------------------------------------------------------------
# import_skill_file — 从文本导入技能到用户级
# ---------------------------------------------------------------------------


def import_skill_file(name: str, content: str) -> Path:
    """把一段 SKILL.md 文本写入用户级 ~/.agent/skills/<name>/SKILL.md。

    Args:
        name: 技能名
        content: SKILL.md 完整文本（含 frontmatter）

    Returns:
        写入的 SKILL.md 路径

    Raises:
        ValueError: 名称非法
    """
    import re
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(f"技能名只能含字母、数字、连字符、下划线：{name}")

    user_dir = _get_user_skills_dir()
    skill_dir = user_dir / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    skill_file = skill_dir / SKILL_FILE_NAME
    skill_file.write_text(content, encoding="utf-8")
    clear_cache()
    return skill_file


# ---------------------------------------------------------------------------
# delete_skill_file — 删除用户级技能
# ---------------------------------------------------------------------------


def delete_skill_file(name: str) -> None:
    """删除用户级技能目录 ~/.agent/skills/<name>/。

    Args:
        name: 技能名

    Raises:
        ValueError: 技能不存在、或不在用户级目录（拒绝删除项目级/插件技能）
    """
    import re
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(f"技能名非法：{name}")

    user_dir = _get_user_skills_dir()
    skill_dir = user_dir / name

    # 安全检查：确保要删的目录在用户级 skills 目录下（防止路径穿越）
    try:
        skill_dir.resolve().relative_to(user_dir.resolve())
    except ValueError:
        raise ValueError(f"技能不在用户级目录，拒绝删除：{name}")

    if not skill_dir.exists():
        raise ValueError(f"技能不存在：{name}")

    import shutil
    shutil.rmtree(skill_dir)
    clear_cache()
