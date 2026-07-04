"""Skill 数据结构定义。

Skill 是可热插拔的 prompt 能力包——一段带 frontmatter 元数据的 markdown，
被调用时把内容展开注入对话。Skill 本身不是 Tool，而是通过统一的 SkillTool
包装暴露给 LLM。

设计参考 Claude Code 的 PromptCommand：每个 skill 有 name/description/when_to_use
等元数据，以及一个 get_prompt 函数返回实际正文（调用时才执行，懒加载）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable


# ---------------------------------------------------------------------------
# Skill — 能力包数据结构
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    """可热插拔的 prompt 能力包。

    Attributes:
        name: skill 名称，也是调用名（如 "commit"）
        description: 一句话描述，进 skill_listing 供 LLM 判断
        when_to_use: "何时使用"的详细场景说明，是自动触发的关键依据
        content: markdown 正文（文件型 skill 的 SKILL.md 正文）
        get_prompt: 获取正文的函数，接收 args 字符串返回 prompt 文本。
            为 None 时默认返回 content。内置 skill 可动态生成内容。
        allowed_tools: 该 skill 激活时额外授予的工具权限（如 ["Bash"]）
        disable_model_invocation: 是否禁止模型自动调用（True 则只允许用户显式触发）
        user_invocable: 用户能否输入 /name 触发
        paths: gitignore 风格的路径匹配模式，条件式 skill——只有模型触碰到
            匹配文件时才激活。None 表示始终可用
        is_enabled_fn: 运行时开关函数，返回 False 则该 skill 不可用
        skill_root: skill 资源根目录（用于变量替换和附属文件定位）
        source: 来源标记（"file" / "bundled" / "mcp" / "plugin"）
        aliases: 别名列表，支持按别名匹配
    """

    name: str
    description: str
    when_to_use: str | None = None
    content: str = ""
    get_prompt: Callable[[str], str] | None = None
    allowed_tools: list[str] | None = None
    disable_model_invocation: bool = False
    user_invocable: bool = True
    paths: list[str] | None = None
    is_enabled_fn: Callable[[], bool] | None = None
    skill_root: str | None = None
    source: str = "unknown"
    aliases: list[str] = field(default_factory=list)

    # ------------------------------------------------------------------
    # 便捷方法
    # ------------------------------------------------------------------

    def is_enabled(self) -> bool:
        """检查该 skill 当前是否可用。"""
        if self.is_enabled_fn is not None:
            return self.is_enabled_fn()
        return True

    def resolve_prompt(self, args: str = "") -> str:
        """获取 skill 正文。

        优先用 get_prompt 函数（可能做变量替换、动态生成），
        否则返回静态 content。
        """
        if self.get_prompt is not None:
            return self.get_prompt(args)
        return self.content

    def matches_name(self, name: str) -> bool:
        """检查 skill 名或别名是否匹配给定名称。"""
        return self.name == name or name in self.aliases

    def is_model_invocable(self) -> bool:
        """模型是否可以自动调用该 skill。"""
        return not self.disable_model_invocation and self.is_enabled()

    def is_user_invocable(self) -> bool:
        """用户是否可以通过斜杠命令调用。"""
        return self.user_invocable and self.is_enabled()
