"""内置 skill 注册机制。

提供程序化注册纯 Python 定义的 skill，不依赖文件系统。
启动时注册内置 skill，运行时也可动态注册。

文件型 skill 的加载在 loader.py 中实现（阶段二），
get_all_skills() 合并内置和文件两个来源。
"""

from __future__ import annotations

import logging
from typing import Callable

from tools.skills.types import Skill

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 全局注册表
# ---------------------------------------------------------------------------

_bundled_skills: list[Skill] = []
_registered: bool = False


# ---------------------------------------------------------------------------
# register_bundled_skill — 注册一个内置 skill
# ---------------------------------------------------------------------------


def register_bundled_skill(skill: Skill) -> None:
    """注册一个内置 skill。

    如果同名 skill 已存在，覆盖原有定义。
    """
    # 去重：同名覆盖
    for i, existing in enumerate(_bundled_skills):
        if existing.name == skill.name:
            _bundled_skills[i] = skill
            logger.debug("覆盖内置 skill: %s", skill.name)
            return
    _bundled_skills.append(skill)
    logger.debug("注册内置 skill: %s", skill.name)


# ---------------------------------------------------------------------------
# get_bundled_skills — 获取所有内置 skill
# ---------------------------------------------------------------------------


def get_bundled_skills() -> list[Skill]:
    """获取所有已注册的内置 skill。"""
    return list(_bundled_skills)


# ---------------------------------------------------------------------------
# get_all_skills — 获取所有可用 skill（内置 + 文件加载）
# ---------------------------------------------------------------------------


def get_all_skills() -> list[Skill]:
    """获取所有可用 skill，合并内置和文件来源。

    来源优先级：文件 skill（项目级 > 用户级）> 内置 skill。
    同名 skill 文件优先，内置作为兜底。

    阶段二会在 loader.py 中实现文件加载，此处先返回内置 skill。
    """
    skills: list[Skill] = []

    # 文件型 skill（阶段二实现）
    try:
        from tools.skills.loader import get_file_skills
        file_skills = get_file_skills()
        skills.extend(file_skills)
    except ImportError:
        pass  # loader 尚未实现

    # 内置 skill（去重：文件优先）
    file_names = {s.name for s in skills}
    for skill in _bundled_skills:
        if skill.name not in file_names:
            skills.append(skill)

    # 过滤不可用的 skill
    return [s for s in skills if s.is_enabled()]


# ---------------------------------------------------------------------------
# find_skill_by_name — 按名称查找 skill
# ---------------------------------------------------------------------------


def find_skill_by_name(name: str) -> Skill | None:
    """按名称或别名查找 skill。"""
    for skill in get_all_skills():
        if skill.matches_name(name):
            return skill
    return None


# ---------------------------------------------------------------------------
# get_model_invocable_skills — 获取模型可调用的 skill 列表
# ---------------------------------------------------------------------------


def get_model_invocable_skills() -> list[Skill]:
    """获取模型可以自动调用的 skill 列表。

    过滤掉 disable_model_invocation=True 的 skill。
    """
    return [s for s in get_all_skills() if s.is_model_invocable()]


# ---------------------------------------------------------------------------
# init_bundled_skills — 初始化内置 skill（启动时调用）
# ---------------------------------------------------------------------------


def init_bundled_skills() -> None:
    """初始化内置 skill。

    幂等：已初始化则跳过。
    后续可在此注册默认内置 skill（如 remember 等）。
    """
    global _registered
    if _registered:
        return
    _registered = True
    # 目前没有默认内置 skill，后续按需注册
    # 例如：register_bundled_skill(Skill(name="remember", ...))
    logger.debug("内置 skill 初始化完成（当前 %d 个）", len(_bundled_skills))
