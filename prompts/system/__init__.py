"""系统提示词：段结构、静态内容、组装函数。"""

from prompts.system.sections import SystemPromptSection
from prompts.system.builder import get_system_prompt_sections

__all__ = [
    "SystemPromptSection",
    "get_system_prompt_sections",
]
