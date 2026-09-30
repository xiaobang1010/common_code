"""模型特性查询模块。

支持查询模型的上下文窗口大小、最大输出 token 数等特性，
内置已知模型配置，未知模型按供应商定义或通用默认值回退并显式告警。
"""

from startup.model.config import (
    ModelConfig,
    get_model_config,
    get_model_window_info,
    get_effective_context_window,
)

__all__ = [
    "ModelConfig",
    "get_model_config",
    "get_model_window_info",
    "get_effective_context_window",
]
