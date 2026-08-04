"""进程级全局状态。

仅保留 server 运行必需的少量字段：工作目录、权限模式、模型名。
成本/token 用量由 AppState 管理，会话由 SessionStore 管理。
"""

from __future__ import annotations

import os
import threading

_lock = threading.Lock()


def _get_initial_state() -> dict[str, object]:
    resolved_cwd = os.path.normpath(os.getcwd())
    return {
        "original_cwd": resolved_cwd,
        "project_root": resolved_cwd,
        "cwd": resolved_cwd,
        "model": None,
        "permission_mode": "default",
    }


_STATE: dict[str, object] = _get_initial_state()


# ---------------------------------------------------------------------------
# CWD / Project Root
# ---------------------------------------------------------------------------

def get_cwd_state() -> str:
    return _STATE["cwd"]


def set_cwd_state(cwd: str) -> None:
    with _lock:
        _STATE["cwd"] = os.path.normpath(cwd)


def set_original_cwd(cwd: str) -> None:
    with _lock:
        _STATE["original_cwd"] = os.path.normpath(cwd)


def set_project_root(cwd: str) -> None:
    with _lock:
        _STATE["project_root"] = os.path.normpath(cwd)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

def get_model() -> str | None:
    return _STATE.get("model")


def set_model(value: str | None) -> None:
    with _lock:
        _STATE["model"] = value


# ---------------------------------------------------------------------------
# Permission mode
# ---------------------------------------------------------------------------

def get_permission_mode() -> str:
    return _STATE.get("permission_mode", "default")


def set_permission_mode(value: str) -> None:
    with _lock:
        _STATE["permission_mode"] = value
