"""写入前碰撞检测 - 检查 Drawer ID 是否已存在。"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def assert_no_collisions(drawer_ids: list[str], storage: Any) -> None:
    """检查 Drawer ID 碰撞。

    Args:
        drawer_ids: 待写入的 Drawer ID 列表
        storage: 存储实例（PalaceStorage 或兼容接口）

    Raises:
        ValueError: 如果发现碰撞
    """
    # 检查内部重复
    seen = set()
    for did in drawer_ids:
        if did in seen:
            raise ValueError(f"Drawer ID 碰撞（内部重复）: {did}")
        seen.add(did)

    # 检查是否已存在
    for did in drawer_ids:
        if storage.get_drawer(did) is not None:
            raise ValueError(f"Drawer ID 碰撞（已存在）: {did}")
