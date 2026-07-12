"""跨平台文件锁 - 防止并发写入冲突。

提供文件级和 Palace 级锁。
"""

from __future__ import annotations

import hashlib
import logging
import os
import sys
import threading
from pathlib import Path
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Per-process holder set for reentrance detection
_palace_lock_holders: set[str] = set()
_palace_lock_holder_pids: dict[str, int] = {}
_lock_holder_lock = threading.Lock()


def _file_hash(text: str) -> str:
    """计算文件路径的 SHA-256 哈希（用于锁文件名）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


@contextmanager
def mine_lock(source_file: str):
    """每文件锁 - 序列化同一文件的并发挖掘。

    Windows: msvcrt.locking
    Unix: fcntl.flock
    """
    lock_name = _file_hash(source_file)
    lock_dir = Path.home() / ".agent" / "memory" / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / f"{lock_name}.lock"

    lock_fd = None
    try:
        lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)

        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(lock_fd, msvcrt.LK_LOCK, 1)
            except (OSError, IOError):
                pass  # Non-blocking best-effort on Windows
        else:
            import fcntl
            fcntl.flock(lock_fd, fcntl.LOCK_EX)

        yield
    finally:
        if lock_fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                    except (OSError, IOError):
                        pass
                else:
                    import fcntl
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            os.close(lock_fd)


@contextmanager
def mine_palace_lock(palace_path: str):
    """每宫殿非阻塞锁 - 防止多个进程同时写入同一 Palace。

    进程内重入检测：同一进程可重入。
    """
    lock_key = _file_hash(str(palace_path))

    with _lock_holder_lock:
        current_pid = os.getpid()
        if lock_key in _palace_lock_holders:
            # Reentrant: same process already holds this lock
            yield
            return

        _palace_lock_holders.add(lock_key)
        _palace_lock_holder_pids[lock_key] = current_pid

    lock_dir = Path.home() / ".agent" / "memory" / ".locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_file = lock_dir / f"palace_{lock_key}.lock"

    lock_fd = None
    try:
        lock_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR)

        # Write holder identity
        os.write(lock_fd, f"{current_pid}:{sys.argv[0]}\n".encode("utf-8"))

        if sys.platform == "win32":
            import msvcrt
            try:
                msvcrt.locking(lock_fd, msvcrt.LK_NBLCK, 1)
            except (OSError, IOError):
                logger.warning("Palace 锁已被其他进程持有")
                yield  # Best-effort
                return
        else:
            import fcntl
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (OSError, IOError):
                logger.warning("Palace 锁已被其他进程持有")
                yield  # Best-effort
                return

        yield
    finally:
        with _lock_holder_lock:
            _palace_lock_holders.discard(lock_key)
            _palace_lock_holder_pids.pop(lock_key, None)

        if lock_fd is not None:
            try:
                if sys.platform == "win32":
                    import msvcrt
                    try:
                        msvcrt.locking(lock_fd, msvcrt.LK_UNLCK, 1)
                    except (OSError, IOError):
                        pass
                else:
                    import fcntl
                    fcntl.flock(lock_fd, fcntl.LOCK_UN)
            except Exception:
                pass
            os.close(lock_fd)
