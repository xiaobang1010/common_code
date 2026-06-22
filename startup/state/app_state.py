"""AppState 类型定义和订阅机制，参考原始 state/AppState.tsx / AppStateStore.ts。"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Generic, TypeVar

from startup.state.store import Store, create_store

T = TypeVar("T")


@dataclass
class ToolPermissionContext:
    """工具权限上下文。"""
    mode: str = "default"
    is_bypass_permissions_mode_available: bool = False
    allowed_tools: list[str] = field(default_factory=list)
    denied_tools: list[str] = field(default_factory=list)


@dataclass
class MCPState:
    """MCP 连接状态。"""
    clients: list[Any] = field(default_factory=list)
    tools: list[Any] = field(default_factory=list)
    commands: list[Any] = field(default_factory=list)
    resources: dict[str, list[Any]] = field(default_factory=dict)
    plugin_reconnect_key: int = 0


@dataclass
class PluginState:
    """插件状态。"""
    enabled: list[Any] = field(default_factory=list)
    disabled: list[Any] = field(default_factory=list)
    commands: list[Any] = field(default_factory=list)
    errors: list[Any] = field(default_factory=list)
    installation_status: dict[str, list[Any]] = field(default_factory=lambda: {
        "marketplaces": [],
        "plugins": [],
    })
    needs_refresh: bool = False


@dataclass
class NotificationState:
    """通知状态。"""
    current: Any = None
    queue: list[Any] = field(default_factory=list)


@dataclass
class PromptSuggestionState:
    """提示建议状态。"""
    text: str | None = None
    prompt_id: str | None = None
    shown_at: float = 0.0
    accepted_at: float = 0.0
    generation_request_id: str | None = None


@dataclass
class FileHistoryState:
    """文件历史状态。"""
    snapshots: list[Any] = field(default_factory=list)
    tracked_files: set[str] = field(default_factory=set)
    snapshot_sequence: int = 0


@dataclass
class InboxState:
    """收件箱状态。"""
    messages: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class WorkerSandboxPermissions:
    """Worker 沙箱权限请求状态。"""
    queue: list[dict[str, Any]] = field(default_factory=list)
    selected_index: int = 0


@dataclass
class SkillImprovementState:
    """技能改进状态。"""
    suggestion: dict[str, Any] | None = None


@dataclass
class TokenUsage:
    """Token 用量统计。"""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    # 最近一次请求的 prompt_tokens，反映当前上下文大小（覆盖，不累加）
    last_prompt_tokens: int = 0
    # 最近一次请求的 cache_creation_input_tokens，反映已缓存大小（覆盖，不累加）
    last_cache_creation: int = 0


@dataclass
class AutoCompactConfig:
    """自动压缩配置。"""
    enabled: bool = True
    threshold: int = 100000
    strategy: str = "default"


@dataclass
class AppState:
    """全局应用状态，参考原始 TypeScript 的 AppState 类型定义。

    字段分组：
    - 会话状态：session_id, messages, tool_use_context, is_loading, abort_controller
    - 模型状态：model, model_config, thinking_budget, effort_level
    - UI 状态：screen, verbose, debug, theme, output_style
    - 权限状态：permission_mode, bypass_permissions
    - 工具状态：mcp_tools, mcp_clients, tool_permission_context
    - 压缩状态：auto_compact_config, context_collapse_enabled
    - 成本状态：total_cost_usd, token_usage
    """

    # --- 会话状态 ---
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    messages: list[Any] = field(default_factory=list)
    tool_use_context: Any = None
    is_loading: bool = False
    abort_controller: Any = None

    # --- 模型状态 ---
    model: str | None = None
    model_config: dict[str, Any] = field(default_factory=dict)
    thinking_budget: int | None = None
    effort_level: str | None = None
    main_loop_model: str | None = None
    main_loop_model_for_session: str | None = None
    thinking_enabled: bool | None = None

    # --- UI 状态 ---
    verbose: bool = False
    debug: bool = False
    theme: str = "dark"
    output_style: str = "default"
    screen: str = "main"
    status_line_text: str | None = None
    expanded_view: str = "none"
    is_brief_only: bool = False
    footer_selection: str | None = None
    spinner_tip: str | None = None

    # --- 权限状态 ---
    permission_mode: str = "default"
    bypass_permissions: bool = False
    tool_permission_context: ToolPermissionContext = field(default_factory=ToolPermissionContext)

    # --- 工具状态 ---
    mcp: MCPState = field(default_factory=MCPState)
    mcp_tools: list[Any] = field(default_factory=list)
    mcp_clients: list[Any] = field(default_factory=list)
    plugins: PluginState = field(default_factory=PluginState)

    # --- 压缩状态 ---
    auto_compact_config: AutoCompactConfig = field(default_factory=AutoCompactConfig)
    context_collapse_enabled: bool = False

    # --- 成本状态 ---
    total_cost_usd: float = 0.0
    token_usage: TokenUsage = field(default_factory=TokenUsage)

    # --- 任务 / Agent ---
    tasks: dict[str, Any] = field(default_factory=dict)
    agent_name_registry: dict[str, str] = field(default_factory=dict)
    foregrounded_task_id: str | None = None
    viewing_agent_task_id: str | None = None
    agent: str | None = None
    agent_definitions: dict[str, Any] = field(default_factory=lambda: {
        "active_agents": [],
        "all_agents": [],
    })

    # --- 文件历史 ---
    file_history: FileHistoryState = field(default_factory=FileHistoryState)

    # --- 通知 ---
    notifications: NotificationState = field(default_factory=NotificationState)
    elicitation: dict[str, list[Any]] = field(default_factory=lambda: {"queue": []})

    # --- 提示建议 ---
    prompt_suggestion_enabled: bool = False
    prompt_suggestion: PromptSuggestionState = field(default_factory=PromptSuggestionState)

    # --- 技能改进 ---
    skill_improvement: SkillImprovementState = field(default_factory=SkillImprovementState)

    # --- 收件箱 ---
    inbox: InboxState = field(default_factory=InboxState)

    # --- Worker 沙箱权限 ---
    worker_sandbox_permissions: WorkerSandboxPermissions = field(default_factory=WorkerSandboxPermissions)
    pending_worker_request: dict[str, Any] | None = None
    pending_sandbox_request: dict[str, Any] | None = None

    # --- 远程 / Bridge ---
    remote_session_url: str | None = None
    remote_connection_status: str = "connecting"
    remote_background_task_count: int = 0
    repl_bridge_enabled: bool = False
    repl_bridge_connected: bool = False
    repl_bridge_session_active: bool = False
    repl_bridge_reconnecting: bool = False
    repl_bridge_connect_url: str | None = None
    repl_bridge_session_url: str | None = None
    repl_bridge_error: str | None = None
    show_remote_callout: bool = False

    # --- 设置 ---
    settings: dict[str, Any] = field(default_factory=dict)

    # --- Session hooks ---
    session_hooks: dict[str, Any] = field(default_factory=dict)

    # --- 认证 ---
    auth_version: int = 0

    # --- 初始消息 ---
    initial_message: dict[str, Any] | None = None

    # --- 活动覆盖层 ---
    active_overlays: set[str] = field(default_factory=set)

    # --- 快速模式 ---
    fast_mode: bool = False

    # --- Effort value ---
    effort_value: str | None = None

    # --- Denial tracking ---
    denial_tracking: dict[str, Any] | None = None

    # --- Speculation ---
    speculation: dict[str, Any] = field(default_factory=lambda: {"status": "idle"})
    speculation_session_time_saved_ms: float = 0.0

    # --- Todos ---
    todos: dict[str, Any] = field(default_factory=dict)

    # --- 远程 Agent 任务建议 ---
    remote_agent_task_suggestions: list[dict[str, str]] = field(default_factory=list)

    # --- 归属 ---
    attribution: dict[str, Any] = field(default_factory=dict)


class AppStateProvider:
    """持有 Store[AppState] 实例，提供 use_state(selector) 方法。

    参考 React 的 AppStateProvider + useAppState hook 设计，
    在 Python 中用 selector 函数实现细粒度订阅。
    """

    def __init__(self, initial_state: AppState | None = None) -> None:
        self._store: Store[AppState] = create_store(
            initial_state or AppState()
        )

    @property
    def store(self) -> Store[AppState]:
        return self._store

    def get_state(self) -> AppState:
        return self._store.get_state()

    def set_state(self, updater: Callable[[AppState], AppState]) -> None:
        self._store.set_state(updater)

    def subscribe(self, listener: Callable[[], None]) -> Callable[[], None]:
        return self._store.subscribe(listener)

    def use_state(self, selector: Callable[[AppState], T]) -> T:
        """返回 selector(get_state())，支持细粒度订阅。

        用法：
            provider = AppStateProvider()
            verbose = provider.use_state(lambda s: s.verbose)
            model = provider.use_state(lambda s: s.model)
        """
        return selector(self._store.get_state())
