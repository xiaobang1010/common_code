"""模型特性查询模块。

参考原始 TypeScript 实现 src/utils/model/，提供模型配置查询功能。

支持查询模型的上下文窗口大小、最大输出 token 数等特性。
内置已知模型配置，未知模型使用通用默认值。
"""

from startup.model.config import (
    ModelConfig,
    get_model_config,
    get_effective_context_window,
)

__all__ = [
    "ModelConfig",
    "get_model_config",
    "get_effective_context_window",
]
