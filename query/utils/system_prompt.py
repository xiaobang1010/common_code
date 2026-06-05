"""动态段注册表管理 — 参考原始 src/utils/systemPrompt.ts。"""

from __future__ import annotations

from collections import OrderedDict

from startup.constants.prompts import SystemPromptSection


# ---------------------------------------------------------------------------
# SystemPromptRegistry — 动态提示词段注册表
# ---------------------------------------------------------------------------

class SystemPromptRegistry:
    """管理动态提示词段的注册表。

    使用 OrderedDict 保持注册顺序，确保 get_sections() 返回的段列表
    与注册顺序一致。
    """

    def __init__(self) -> None:
        self._sections: OrderedDict[str, SystemPromptSection] = OrderedDict()

    def register(
        self,
        name: str,
        content: str,
        cache_scope: str | None = None,
    ) -> None:
        """注册一个动态提示词段。

        如果 name 已存在，则覆盖原有内容。
        """
        self._sections[name] = SystemPromptSection(
            content=content,
            cache_scope=cache_scope,
            name=name,
        )

    def unregister(self, name: str) -> None:
        """取消注册指定名称的段。

        如果 name 不存在，静默忽略。
        """
        self._sections.pop(name, None)

    def get_sections(self) -> list[SystemPromptSection]:
        """获取所有已注册的段（按注册顺序）。"""
        return list(self._sections.values())


# ---------------------------------------------------------------------------
# 全局单例 + 便捷函数
# ---------------------------------------------------------------------------

registry = SystemPromptRegistry()


def register_dynamic_section(name: str, content: str) -> None:
    """便捷注册函数 — 使用全局 registry，cache_scope 默认 None。"""
    registry.register(name, content, cache_scope=None)


def unregister_dynamic_section(name: str) -> None:
    """便捷取消函数 — 使用全局 registry。"""
    registry.unregister(name)
