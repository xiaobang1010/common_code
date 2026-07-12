"""L0 身份层 - 始终加载，约 100 tokens。

从 ~/.agent/identity.txt 读取纯文本身份信息。
不依赖任何存储层。
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


class Layer0:
    """L0 身份层 - 始终加载，约 100 tokens。

    从 ~/.agent/identity.txt 读取纯文本身份信息。
    内容示例：AI 助手名称、个性特征、关键人物、当前项目。
    """

    def __init__(self, identity_path: Path | None = None):
        if identity_path is None:
            identity_path = Path.home() / ".agent" / "identity.txt"
        self.identity_path = identity_path

    def render(self) -> str:
        """读取身份文件并返回文本。

        文件不存在时创建默认模板并返回。
        """
        if not self.identity_path.exists():
            # 创建默认身份模板
            default_content = """# AI Assistant Identity

## 基本信息
- 名称：AI 编程助手
- 项目：{project}

## 关键人物
（暂无记录）

## 当前项目
（暂无记录）
"""
            try:
                self.identity_path.parent.mkdir(parents=True, exist_ok=True)
                self.identity_path.write_text(default_content, encoding="utf-8")
                logger.info("创建默认身份模板: %s", self.identity_path)
            except Exception:
                pass
            return default_content

        try:
            return self.identity_path.read_text(encoding="utf-8")
        except Exception:
            return ""
