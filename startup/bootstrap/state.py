# DO NOT ADD MORE STATE HERE - BE JUDICIOUS WITH GLOBAL STATE

"""模块级单例状态，参考原始 bootstrap/state.ts 的设计。

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
        "has_unknown_model_cost": False,
        "cwd": resolved_cwd,
        "model_usage": {},
        "main_loop_model_override": None,
        "initial_main_loop_model": None,
        "model_strings": None,
        "is_interactive": False,
        "kairos_active": False,
        "strict_tool_result_pairing": False,
        "sdk_agent_progress_summaries_enabled": False,
        "user_msg_opt_in": False,
        "client_type": "cli",
        "session_source": None,
        "question_preview_format": None,
        "flag_settings_path": None,
        "flag_settings_inline": None,
        "allowed_setting_sources": [
            "userSettings",
            "projectSettings",
            "localSettings",
            "flagSettings",
            "policySettings",
        ],
        "session_ingress_token": None,
        "oauth_token_from_fd": None,
        "api_key_from_fd": None,
        # Telemetry state
        "meter": None,
        "session_counter": None,
        "loc_counter": None,
        "pr_counter": None,
        "commit_counter": None,
        "cost_counter": None,
        "token_counter": None,
        "code_edit_tool_decision_counter": None,
        "active_time_counter": None,
        "stats_store": None,
        "session_id": str(uuid.uuid4()),
        "parent_session_id": None,
        # Logger state
        "logger_provider": None,
        "event_logger": None,
        # Meter / Tracer provider state
        "meter_provider": None,
        "tracer_provider": None,
        # Agent color state
        "agent_color_map": {},
        "agent_color_index": 0,
        # Last API request for bug reports
        "last_api_request": None,
        "last_api_request_messages": None,
        "last_classifier_requests": None,
        "cached_agent_md_content": None,
        # In-memory error log
        "in_memory_error_log": [],
        # Session-only plugins
        "inline_plugins": [],
        "chrome_flag_override": None,
        "use_cowork_plugins": False,
        "session_bypass_permissions_mode": False,
        "scheduled_tasks_enabled": False,
        "session_cron_tasks": [],
        "session_created_teams": set(),
        "session_trust_accepted": False,
        "session_persistence_disabled": False,
        "has_exited_plan_mode": False,
        "needs_plan_mode_exit_attachment": False,
        "needs_auto_mode_exit_attachment": False,
        "lsp_recommendation_shown_this_session": False,
        "init_json_schema": None,
        "registered_hooks": None,
        "plan_slug_cache": {},
        "teleported_session_info": None,
        "invoked_skills": {},
        "slow_operations": [],
        "sdk_betas": None,
        "main_thread_agent_type": None,
        "is_remote_mode": False,
        "direct_connect_server_url": None,
        "system_prompt_section_cache": {},
        "last_emitted_date": None,
        "additional_directories_for_agent_md": [],
        "allowed_channels": [],
        "has_dev_channels": False,
        "session_project_dir": None,
        "prompt_cache_1h_allowlist": None,
        "prompt_cache_1h_eligible": None,
        "afk_mode_header_latched": None,
        "fast_mode_header_latched": None,
        "cache_editing_header_latched": None,
        "thinking_clear_latched": None,
        "prompt_id": None,
        "last_main_request_id": None,
        "last_api_completion_timestamp": None,
        "pending_post_compaction": False,
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
        _STATE["plan_slug_cache"].pop(_STATE["session_id"], None)
        _STATE["session_id"] = str(uuid.uuid4())
        _STATE["session_project_dir"] = None
        return _STATE["session_id"]


def get_parent_session_id() -> str | None:
    return _STATE["parent_session_id"]


def switch_session(session_id: str, project_dir: str | None = None) -> None:
    with _lock:
        _STATE["plan_slug_cache"].pop(_STATE["session_id"], None)
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
        _STATE["has_unknown_model_cost"] = False
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
# Interactive / Client
# ---------------------------------------------------------------------------

def get_is_interactive() -> bool:
    return _STATE["is_interactive"]


def set_is_interactive(value: bool) -> None:
    with _lock:
        _STATE["is_interactive"] = value


def get_client_type() -> str:
    return _STATE["client_type"]


def set_client_type(client_type: str) -> None:
    with _lock:
        _STATE["client_type"] = client_type


# ---------------------------------------------------------------------------
# Misc boolean flags
# ---------------------------------------------------------------------------

def get_kairos_active() -> bool:
    return _STATE["kairos_active"]


def set_kairos_active(value: bool) -> None:
    with _lock:
        _STATE["kairos_active"] = value


def get_strict_tool_result_pairing() -> bool:
    return _STATE["strict_tool_result_pairing"]


def set_strict_tool_result_pairing(value: bool) -> None:
    with _lock:
        _STATE["strict_tool_result_pairing"] = value


def get_user_msg_opt_in() -> bool:
    return _STATE["user_msg_opt_in"]


def set_user_msg_opt_in(value: bool) -> None:
    with _lock:
        _STATE["user_msg_opt_in"] = value


def has_unknown_model_cost() -> bool:
    return _STATE["has_unknown_model_cost"]


def set_has_unknown_model_cost() -> None:
    with _lock:
        _STATE["has_unknown_model_cost"] = True


# ---------------------------------------------------------------------------
# Session source / format
# ---------------------------------------------------------------------------

def get_session_source() -> str | None:
    return _STATE["session_source"]


def set_session_source(source: str) -> None:
    with _lock:
        _STATE["session_source"] = source


def get_question_preview_format() -> str | None:
    return _STATE["question_preview_format"]


def set_question_preview_format(fmt: str) -> None:
    with _lock:
        _STATE["question_preview_format"] = fmt


# ---------------------------------------------------------------------------
# Direct connect / Remote
# ---------------------------------------------------------------------------

def get_direct_connect_server_url() -> str | None:
    return _STATE["direct_connect_server_url"]


def set_direct_connect_server_url(url: str) -> None:
    with _lock:
        _STATE["direct_connect_server_url"] = url


def get_is_remote_mode() -> bool:
    return _STATE["is_remote_mode"]


def set_is_remote_mode(value: bool) -> None:
    with _lock:
        _STATE["is_remote_mode"] = value


# ---------------------------------------------------------------------------
# Last interaction time
# ---------------------------------------------------------------------------

def get_last_interaction_time() -> float:
    return _STATE["last_interaction_time"]


def update_last_interaction_time(immediate: bool = True) -> None:
    with _lock:
        _STATE["last_interaction_time"] = time.time() * 1000


# ---------------------------------------------------------------------------
# Stats store
# ---------------------------------------------------------------------------

def get_stats_store() -> Any:
    return _STATE["stats_store"]


def set_stats_store(store: Any) -> None:
    with _lock:
        _STATE["stats_store"] = store


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


# ---------------------------------------------------------------------------
# Test block
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # 测试 get/set 函数对
    print("=== 测试 bootstrap/state.py ===")

    sid = get_session_id()
    assert isinstance(sid, str) and len(sid) > 0, f"session_id 应为非空字符串, got: {sid}"
    print(f"get_session_id() = {sid}")

    set_verbose(True)
    assert get_verbose() is True
    print(f"get_verbose() = {get_verbose()}")

    set_verbose(False)
    assert get_verbose() is False

    set_model("claude-3.5-sonnet")
    assert get_model() == "claude-3.5-sonnet"
    print(f"get_model() = {get_model()}")

    set_permission_mode("plan")
    assert get_permission_mode() == "plan"
    print(f"get_permission_mode() = {get_permission_mode()}")

    set_cwd_state("/tmp/test")
    assert get_cwd_state() == os.path.normpath("/tmp/test")
    print(f"get_cwd_state() = {get_cwd_state()}")

    add_to_total_cost(0.05, {"input_tokens": 100, "output_tokens": 50}, "claude-3.5-sonnet")
    assert get_total_cost_usd() == 0.05
    print(f"get_total_cost_usd() = {get_total_cost_usd()}")

    # 测试 Store 的 subscribe 机制
    print("\n=== 测试 Store ===")
    from startup.state.store import create_store

    store = create_store({"count": 0})
    assert store.get_state() == {"count": 0}
    print(f"初始状态: {store.get_state()}")

    notifications: list[dict] = []
    def listener():
        notifications.append(store.get_state())

    unsub = store.subscribe(listener)

    store.set_state(lambda s: {**s, "count": s["count"] + 1})
    assert store.get_state() == {"count": 1}
    assert len(notifications) == 1
    print(f"更新后状态: {store.get_state()}, 通知次数: {len(notifications)}")

    # 相同引用不触发通知
    store.set_state(lambda s: s)
    assert len(notifications) == 1, "相同引用不应触发通知"
    print("same ref skip OK")

    unsub()
    store.set_state(lambda s: {**s, "count": s["count"] + 1})
    assert len(notifications) == 1, "取消订阅后不应收到通知"
    print("unsubscribe OK")

    # 测试 AppState
    print("\n=== 测试 AppState ===")
    from startup.state.app_state import AppState, AppStateProvider

    app_state = AppState()
    assert app_state.verbose is False
    assert app_state.session_id is not None
    print(f"AppState.session_id = {app_state.session_id}")
    print(f"AppState.verbose = {app_state.verbose}")

    provider = AppStateProvider(app_state)
    selected = provider.use_state(lambda s: s.verbose)
    assert selected is False
    print(f"use_state(lambda s: s.verbose) = {selected}")

    selected_model = provider.use_state(lambda s: s.model)
    assert selected_model is None
    print(f"use_state(lambda s: s.model) = {selected_model}")

    print("\nAll tests passed!")
