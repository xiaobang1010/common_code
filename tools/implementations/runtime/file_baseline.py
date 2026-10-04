"""文件读写基线登记表。

记录本进程内最近一次 Read / Write / Edit 成功后的文件基线（mtime/size），
Write 覆盖已存在文件而模型未回传 base_mtime/base_size 时自动采用，
把「记基线、抄参数」的负担从模型挪到系统侧——模型只管先读后写；
磁盘实际状态仍逐一比对，读取之后的外部改动依然会被 file_modified 拦截。

作用域是进程级而非会话级：子代理的 session_id 与父会话不同，按会话隔离
会让子代理拿不到父会话读过的基线，重新掉回「先读后写」的循环里。
跨会话误用的风险由基线与磁盘的实时比对兜底。
"""

from __future__ import annotations

import threading

# 绝对路径 → (mtime 整数秒, size 字节)
_BASELINES: dict[str, tuple[int, int]] = {}
_LOCK = threading.Lock()


def record_baseline(path: str, mtime: int, size: int) -> None:
    """登记文件基线（Read 成功后、Write/Edit 写盘成功后调用）。"""
    with _LOCK:
        _BASELINES[path] = (int(mtime), int(size))


def get_baseline(path: str) -> tuple[int, int] | None:
    """取文件基线；从未登记过返回 None。"""
    with _LOCK:
        return _BASELINES.get(path)
