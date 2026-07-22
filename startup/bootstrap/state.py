# DO NOT ADD MORE STATE HERE - BE JUDICIOUS WITH GLOBAL STATE

"""模块级单例状态。

使用模块级 _STATE 字典 + get_xxx/set_xxx 函数对访问/修改全局状态，
threading.Lock 保证线程安全。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from typing import Any


_lock = threading.Lock()


def _get_initial_state() -> dict[str, Any]:
    resolved_cwd = os.path.normpath(os.getcwd())
    return {
        "original_cwd": resolved_cwd,
        "project_root": resolved_cwd,
        "total_cost_usd": 0.0,
        "total_api_duration": 0.0,
        "total_api_duration_without_retries": 0.0,
        "total_tool_duration": 0.0,
        "turn_hook_duration_ms": 0.0,
        "turn_tool_duration_ms": 0.0,
        "turn_classifier_duration_ms": 0.0,
        "turn_tool_count": 0,
        "turn_hook_count": 0,
        "turn_classifier_count": 0,
        "start_time": time.time() * 1000,
        "last_interaction_time": time.time() * 1000,
        "total_lines_added": 0,
        "total_lines_removed": 0,
        "cwd": resolved_cwd,
        "model_usage": {},
        "main_loop_model_override": None,
        "initial_main_loop_model": None,
        "model_strings": None,
        "allowed_setting_sources": [
            "userSettings",
            "projectSettings",
            "localSettings",
            "flagSettings",
            "policySettings",
        ],
        "session_id": str(uuid.uuid4()),
        "parent_session_id": None,
        "session_project_dir": None,
        "last_api_completion_timestamp": None,
        "pending_post_compaction": False,
        "system_prompt_section_cache": {},
        "last_emitted_date": None,
        "prompt_id": None,
        "last_main_request_id": None,
    }


# AND ESPECIALLY HERE
_STATE: dict[str, Any] = _get_initial_state()


# ---------------------------------------------------------------------------
# Session ID
# ---------------------------------------------------------------------------

def get_session_id() -> str:
    return _STATE["session_id"]


def regenerate_session_id(set_current_as_parent: bool = False) -> str:
    with _lock:
        if set_current_as_parent:
            _STATE["parent_session_id"] = _STATE["session_id"]
        _STATE["session_id"] = str(uuid.uuid4())
        _STATE["session_project_dir"] = None
        return _STATE["session_id"]


def get_parent_session_id() -> str | None:
    return _STATE["parent_session_id"]


def switch_session(session_id: str, project_dir: str | None = None) -> None:
    with _lock:
        _STATE["session_id"] = session_id
        _STATE["session_project_dir"] = project_dir


def get_session_project_dir() -> str | None:
    return _STATE["session_project_dir"]


# ---------------------------------------------------------------------------
# CWD / Project Root
# ---------------------------------------------------------------------------

def get_original_cwd() -> str:
    return _STATE["original_cwd"]


def get_project_root() -> str:
    return _STATE["project_root"]


def set_original_cwd(cwd: str) -> None:
    with _lock:
        _STATE["original_cwd"] = os.path.normpath(cwd)


def set_project_root(cwd: str) -> None:
    with _lock:
        _STATE["project_root"] = os.path.normpath(cwd)


def get_cwd_state() -> str:
    return _STATE["cwd"]


def set_cwd_state(cwd: str) -> None:
    with _lock:
        _STATE["cwd"] = os.path.normpath(cwd)


# ---------------------------------------------------------------------------
# Cost / Duration
# ---------------------------------------------------------------------------

def get_total_cost_usd() -> float:
    return _STATE["total_cost_usd"]


def add_to_total_cost(cost: float, model_usage: dict, model: str) -> None:
    with _lock:
        _STATE["model_usage"][model] = model_usage
        _STATE["total_cost_usd"] += cost


def get_total_api_duration() -> float:
    return _STATE["total_api_duration"]


def get_total_duration() -> float:
    return time.time() * 1000 - _STATE["start_time"]


def add_to_total_duration(duration: float, duration_without_retries: float) -> None:
    with _lock:
        _STATE["total_api_duration"] += duration
        _STATE["total_api_duration_without_retries"] += duration_without_retries


def get_total_api_duration_without_retries() -> float:
    return _STATE["total_api_duration_without_retries"]


def get_total_tool_duration() -> float:
    return _STATE["total_tool_duration"]


def add_to_tool_duration(duration: float) -> None:
    with _lock:
        _STATE["total_tool_duration"] += duration
        _STATE["turn_tool_duration_ms"] += duration
        _STATE["turn_tool_count"] += 1


def reset_cost_state() -> None:
    with _lock:
        _STATE["total_cost_usd"] = 0.0
        _STATE["total_api_duration"] = 0.0
        _STATE["total_api_duration_without_retries"] = 0.0
        _STATE["total_tool_duration"] = 0.0
        _STATE["start_time"] = time.time() * 1000
        _STATE["total_lines_added"] = 0
        _STATE["total_lines_removed"] = 0
        _STATE["model_usage"] = {}
        _STATE["prompt_id"] = None


# ---------------------------------------------------------------------------
# Turn metrics
# ---------------------------------------------------------------------------

def get_turn_hook_duration_ms() -> float:
    return _STATE["turn_hook_duration_ms"]


def add_to_turn_hook_duration(duration: float) -> None:
    with _lock:
        _STATE["turn_hook_duration_ms"] += duration
        _STATE["turn_hook_count"] += 1


def reset_turn_hook_duration() -> None:
    with _lock:
        _STATE["turn_hook_duration_ms"] = 0.0
        _STATE["turn_hook_count"] = 0


def get_turn_hook_count() -> int:
    return _STATE["turn_hook_count"]


def get_turn_tool_duration_ms() -> float:
    return _STATE["turn_tool_duration_ms"]


def reset_turn_tool_duration() -> None:
    with _lock:
        _STATE["turn_tool_duration_ms"] = 0.0
        _STATE["turn_tool_count"] = 0


def get_turn_tool_count() -> int:
    return _STATE["turn_tool_count"]


def get_turn_classifier_duration_ms() -> float:
    return _STATE["turn_classifier_duration_ms"]


def add_to_turn_classifier_duration(duration: float) -> None:
    with _lock:
        _STATE["turn_classifier_duration_ms"] += duration
        _STATE["turn_classifier_count"] += 1


def reset_turn_classifier_duration() -> None:
    with _lock:
        _STATE["turn_classifier_duration_ms"] = 0.0
        _STATE["turn_classifier_count"] = 0


def get_turn_classifier_count() -> int:
    return _STATE["turn_classifier_count"]


# ---------------------------------------------------------------------------
# Lines changed
# ---------------------------------------------------------------------------

def add_to_total_lines_changed(added: int, removed: int) -> None:
    with _lock:
        _STATE["total_lines_added"] += added
        _STATE["total_lines_removed"] += removed


def get_total_lines_added() -> int:
    return _STATE["total_lines_added"]


def get_total_lines_removed() -> int:
    return _STATE["total_lines_removed"]


# ---------------------------------------------------------------------------
# Model usage
# ---------------------------------------------------------------------------

def get_model_usage() -> dict:
    return _STATE["model_usage"]


def get_usage_for_model(model: str) -> dict | None:
    return _STATE["model_usage"].get(model)


def get_total_input_tokens() -> int:
    return sum(u.get("input_tokens", 0) for u in _STATE["model_usage"].values())


def get_total_output_tokens() -> int:
    return sum(u.get("output_tokens", 0) for u in _STATE["model_usage"].values())


# ---------------------------------------------------------------------------
# Model settings
# ---------------------------------------------------------------------------

def get_main_loop_model_override() -> Any:
    return _STATE["main_loop_model_override"]


def set_main_loop_model_override(model: Any) -> None:
    with _lock:
        _STATE["main_loop_model_override"] = model


def get_initial_main_loop_model() -> Any:
    return _STATE["initial_main_loop_model"]


def set_initial_main_loop_model(model: Any) -> None:
    with _lock:
        _STATE["initial_main_loop_model"] = model


def get_model_strings() -> Any:
    return _STATE["model_strings"]


def set_model_strings(model_strings: Any) -> None:
    with _lock:
        _STATE["model_strings"] = model_strings


# ---------------------------------------------------------------------------
# Last interaction time
# ---------------------------------------------------------------------------

def get_last_interaction_time() -> float:
    return _STATE["last_interaction_time"]


def update_last_interaction_time(immediate: bool = True) -> None:
    with _lock:
        _STATE["last_interaction_time"] = time.time() * 1000


# ---------------------------------------------------------------------------
# Verbose / Debug (convenience for common access)
# ---------------------------------------------------------------------------

def get_verbose() -> bool:
    return _STATE.get("verbose", False)


def set_verbose(value: bool) -> None:
    with _lock:
        _STATE["verbose"] = value


def get_model() -> Any:
    return _STATE.get("model")


def set_model(value: Any) -> None:
    with _lock:
        _STATE["model"] = value


def get_permission_mode() -> str:
    return _STATE.get("permission_mode", "default")


def set_permission_mode(value: str) -> None:
    with _lock:
        _STATE["permission_mode"] = value


# ---------------------------------------------------------------------------
# Reset for tests
# ---------------------------------------------------------------------------

def reset_state_for_tests() -> None:
    global _STATE
    with _lock:
        _STATE = _get_initial_state()
