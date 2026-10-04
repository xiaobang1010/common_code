"""后台子代理启动器 — 薄转发层。

派生与终态处理逻辑已收敛到 tools/subagent/lifecycle.py（统一派生入口：
注册、驱动任务、看门狗、通知投递）。本模块保留旧入口签名兼容既有调用方
与测试；新代码请直接使用 lifecycle.spawn_subagent。
"""

from __future__ import annotations

from tools.subagent.lifecycle import launch_background_subagent  # noqa: F401

# 后台结果截断阈值（沿用生命周期引擎口径，供外部引用兼容）
from tools.subagent.lifecycle import MAX_RESULT_SIZE_CHARS  # noqa: F401
