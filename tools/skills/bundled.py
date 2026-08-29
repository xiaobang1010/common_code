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
    # 惰性接线：首次取用时注册内置 skill（init 幂等）
    init_bundled_skills()
    skills: list[Skill] = []

    # 文件型 skill（阶段二实现）
    try:
        from tools.skills.loader import get_file_skills
        file_skills = get_file_skills()
        skills.extend(file_skills)
    except ImportError:
        pass  # loader 尚未实现

    # 插件提供的 skill（standard kind 插件的 skills/ 子目录）
    try:
        from startup.plugins.standard_loader import get_all_plugin_skills
        plugin_skills = get_all_plugin_skills()
        skills.extend(plugin_skills)
    except ImportError:
        pass

    # 内置 skill（去重：文件和插件优先）
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
    """
    global _registered
    if _registered:
        return
    _registered = True
    register_bundled_skill(_make_spec_skill())
    logger.debug("内置 skill 初始化完成（当前 %d 个）", len(_bundled_skills))


# ---------------------------------------------------------------------------
# spec 技能 — spec 驱动开发工作流（三件套写盘 + 勾选留痕 + 进展面板展示）
# ---------------------------------------------------------------------------

_SPEC_PROMPT = """\
按 spec 驱动开发模式工作：先对齐再执行，文档即进度。

## 路径与三件套
在当前工作区 `.agent/specs/<任务名>/` 下创建三个文件（任务名为 kebab-case 英文短名）：
- `spec.md` 需求大纲：背景与目标、范围（包含/不包含）、技术方案、风险与对策
- `tasks.md` 任务清单：分阶段、有序编号，每项一行勾选项 `- [ ] 任务描述`
- `checklist.md` 验收清单：可判定的验收项，同样用 `- [ ]` 勾选项

## 流程
1. 前置检查：先读相关代码与目录，不猜测；扫描 `.agent/specs/` 已有同名任务则续接
2. 生成三件套后暂停，向用户汇报要点并等待确认
3. 确认后按 tasks.md 顺序逐项执行

## 勾选纪律（核心）
- 每完成一项，立即用编辑工具把 tasks.md 对应行改为 `- [x]`，再开始下一项
- checklist.md 只在验收项真实满足后勾选，不许预先勾选、不许跳过失败项
- 勾选状态会实时显示在进展面板，文档就是唯一的进度事实源
"""


def _make_spec_skill() -> Skill:
    """构建内置 spec 技能。"""
    return Skill(
        name="spec",
        description="复杂任务写 spec 三件套（需求大纲/任务清单/验收清单），进度跟着文档勾选走",
        when_to_use="用户要求先规划、写 spec、要任务清单或验收标准时；接到多步复杂任务、"
        "涉及架构决策或从零搭建模块时，也应主动建议使用",
        content=_SPEC_PROMPT,
        source="bundled",
    )
