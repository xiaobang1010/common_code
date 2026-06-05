"""配置系统核心模块。

提供全局配置和项目配置的读取、保存、合并功能。
线程安全，使用 threading.Lock 保护共享状态。

配置文件路径：
  - 全局配置：~/.agent.json
  - 项目配置：.agent/settings.json
  - 本地项目配置：.agent/settings.local.json

LLM 配置项：
  - llm_base_url：默认 https://api.openai.com/v1
  - llm_api_key：API Key
  - llm_model：默认模型名
  这些配置从配置文件或环境变量 OPENAI_API_KEY, OPENAI_BASE_URL, CC_MODEL 读取
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from startup.utils.config_constants import (
    DEFAULT_LLM_BASE_URL,
    DEFAULT_LLM_MODEL,
    ENV_LLM_API_KEY,
    ENV_LLM_BASE_URL,
    ENV_LLM_MODEL,
    GLOBAL_CONFIG_FILENAME,
    LOCAL_SETTINGS_FILENAME,
    MANAGED_SETTINGS_FILENAME,
    PROJECT_CONFIG_DIR,
    PROJECT_SETTINGS_FILENAME,
)
from startup.utils.settings.types import Permissions, PermissionRule, Settings


# ---------------------------------------------------------------------------
# Dataclass 定义
# ---------------------------------------------------------------------------


@dataclass
class ProjectConfig:
    """项目级配置，存储在全局配置文件的 projects 字段中。"""

    allowed_tools: list[str] = field(default_factory=list)
    mcp_context_uris: list[str] = field(default_factory=list)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    has_trust_dialog_accepted: bool = False
    project_onboarding_seen_count: int = 0
    has_agent_md_external_includes_approved: bool = False
    has_agent_md_external_includes_warning_shown: bool = False
    enabled_mcpjson_servers: list[str] = field(default_factory=list)
    disabled_mcpjson_servers: list[str] = field(default_factory=list)
    disabled_mcp_servers: list[str] = field(default_factory=list)
    enabled_mcp_servers: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProjectConfig:
        return cls(
            allowed_tools=data.get("allowedTools", []),
            mcp_context_uris=data.get("mcpContextUris", []),
            mcp_servers=data.get("mcpServers", {}),
            has_trust_dialog_accepted=data.get("hasTrustDialogAccepted", False),
            project_onboarding_seen_count=data.get("projectOnboardingSeenCount", 0),
            has_agent_md_external_includes_approved=data.get(
                "hasAgentMdExternalIncludesApproved", False
            ),
            has_agent_md_external_includes_warning_shown=data.get(
                "hasAgentMdExternalIncludesWarningShown", False
            ),
            enabled_mcpjson_servers=data.get("enabledMcpjsonServers", []),
            disabled_mcpjson_servers=data.get("disabledMcpjsonServers", []),
            disabled_mcp_servers=data.get("disabledMcpServers", []),
            enabled_mcp_servers=data.get("enabledMcpServers", []),
        )


@dataclass
class GlobalConfig:
    """全局配置，存储在 ~/.agent.json。"""

    num_startups: int = 0
    theme: str = "dark"
    preferred_notif_channel: str = "auto"
    verbose: bool = False
    auto_compact_enabled: bool = True
    show_turn_duration: bool = True
    todo_feature_enabled: bool = True
    show_expanded_todos: bool = False
    message_idle_notif_threshold_ms: int = 60000
    file_checkpointing_enabled: bool = True
    terminal_progress_bar_enabled: bool = True
    respect_gitignore: bool = True
    copy_full_response: bool = False
    env: dict[str, str] = field(default_factory=dict)
    projects: dict[str, dict[str, Any]] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    has_completed_onboarding: bool = False
    # LLM 配置
    llm_base_url: str | None = None
    llm_api_key: str | None = None
    llm_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 友好的字典，使用 camelCase 键名。"""
        d = asdict(self)
        # 转换键名为 camelCase 以与 TS 版本兼容
        result: dict[str, Any] = {}
        key_map = {
            "num_startups": "numStartups",
            "preferred_notif_channel": "preferredNotifChannel",
            "auto_compact_enabled": "autoCompactEnabled",
            "show_turn_duration": "showTurnDuration",
            "todo_feature_enabled": "todoFeatureEnabled",
            "show_expanded_todos": "showExpandedTodos",
            "message_idle_notif_threshold_ms": "messageIdleNotifThresholdMs",
            "file_checkpointing_enabled": "fileCheckpointingEnabled",
            "terminal_progress_bar_enabled": "terminalProgressBarEnabled",
            "respect_gitignore": "respectGitignore",
            "copy_full_response": "copyFullResponse",
            "has_completed_onboarding": "hasCompletedOnboarding",
            "llm_base_url": "llm_base_url",
            "llm_api_key": "llm_api_key",
            "llm_model": "llm_model",
        }
        for k, v in d.items():
            result[key_map.get(k, k)] = v
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GlobalConfig:
        return cls(
            num_startups=data.get("numStartups", 0),
            theme=data.get("theme", "dark"),
            preferred_notif_channel=data.get("preferredNotifChannel", "auto"),
            verbose=data.get("verbose", False),
            auto_compact_enabled=data.get("autoCompactEnabled", True),
            show_turn_duration=data.get("showTurnDuration", True),
            todo_feature_enabled=data.get("todoFeatureEnabled", True),
            show_expanded_todos=data.get("showExpandedTodos", False),
            message_idle_notif_threshold_ms=data.get(
                "messageIdleNotifThresholdMs", 60000
            ),
            file_checkpointing_enabled=data.get("fileCheckpointingEnabled", True),
            terminal_progress_bar_enabled=data.get(
                "terminalProgressBarEnabled", True
            ),
            respect_gitignore=data.get("respectGitignore", True),
            copy_full_response=data.get("copyFullResponse", False),
            env=data.get("env", {}),
            projects=data.get("projects", {}),
            mcp_servers=data.get("mcpServers", {}),
            has_completed_onboarding=data.get("hasCompletedOnboarding", False),
            llm_base_url=data.get("llm_base_url"),
            llm_api_key=data.get("llm_api_key"),
            llm_model=data.get("llm_model"),
        )


# ---------------------------------------------------------------------------
# 模块级状态
# ---------------------------------------------------------------------------

# 全局锁，保护配置读写
_config_lock = threading.Lock()

# 配置读取是否已启用
_config_reading_allowed = False

# 全局配置缓存
_global_config_cache: GlobalConfig | None = None
_global_config_cache_mtime: float = 0.0


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------


def _get_home_dir() -> Path:
    """获取用户主目录。

    优先使用 HOME 环境变量（便于测试），否则使用系统默认。
    Windows 上同时检查 USERPROFILE。
    """
    home = os.environ.get("HOME")
    if home:
        return Path(home)
    userprofile = os.environ.get("USERPROFILE")
    if userprofile:
        return Path(userprofile)
    return Path.home()


def get_global_config_path() -> Path:
    """获取全局配置文件路径 ~/.agent.json。"""
    return _get_home_dir() / GLOBAL_CONFIG_FILENAME


def get_config_home_dir() -> Path:
    """获取配置主目录 ~/.agent/。"""
    return _get_home_dir() / PROJECT_CONFIG_DIR


def get_project_config_dir(project_root: Path | None = None) -> Path:
    """获取项目配置目录 .agent/。"""
    root = project_root or Path.cwd()
    return root / PROJECT_CONFIG_DIR


def get_project_settings_path(project_root: Path | None = None) -> Path:
    """获取项目设置文件路径 .agent/settings.json。"""
    return get_project_config_dir(project_root) / PROJECT_SETTINGS_FILENAME


def get_local_settings_path(project_root: Path | None = None) -> Path:
    """获取本地设置文件路径 .agent/settings.local.json。"""
    return get_project_config_dir(project_root) / LOCAL_SETTINGS_FILENAME


def get_managed_settings_path() -> Path:
    """获取管理设置文件路径 ~/.agent/managed-settings.json。"""
    return get_config_home_dir() / MANAGED_SETTINGS_FILENAME


def _strip_bom(content: str) -> str:
    """去除 BOM 标记（PowerShell 5.x 会给 UTF-8 文件添加 BOM）。"""
    if content.startswith("\ufeff"):
        return content[1:]
    return content


def _read_json_file(path: Path) -> dict[str, Any] | None:
    """安全读取 JSON 文件，返回解析后的字典或 None。"""
    try:
        raw = path.read_text(encoding="utf-8")
        return json.loads(_strip_bom(raw))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_json_file(path: Path, data: dict[str, Any]) -> None:
    """将字典写入 JSON 文件，确保目录存在。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(data, indent=2, ensure_ascii=False)
    path.write_text(content, encoding="utf-8")


def _deep_merge(base: dict, override: dict) -> dict:
    """深度合并两个字典，override 中的值优先。"""
    result = base.copy()
    for key, value in override.items():
        if (
            key in result
            and isinstance(result[key], dict)
            and isinstance(value, dict)
        ):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# 核心配置 API
# ---------------------------------------------------------------------------


def enable_configs() -> None:
    """初始化配置系统，加载全局和项目配置。

    幂等操作：多次调用不会重复初始化。
    """
    global _config_reading_allowed

    with _config_lock:
        if _config_reading_allowed:
            return
        _config_reading_allowed = True

    # 预加载全局配置以验证文件格式
    get_global_config()


def get_global_config() -> GlobalConfig:
    """读取全局配置 ~/.agent.json。

    使用缓存避免重复磁盘 I/O。
    线程安全。
    """
    global _global_config_cache, _global_config_cache_mtime

    if not _config_reading_allowed:
        raise RuntimeError("Config accessed before allowed. Call enable_configs() first.")

    with _config_lock:
        # 缓存命中
        if _global_config_cache is not None:
            return _global_config_cache

        config_path = get_global_config_path()
        data = _read_json_file(config_path)

        if data is not None:
            config = GlobalConfig.from_dict(data)
        else:
            config = GlobalConfig()

        # 从环境变量补充 LLM 配置
        _apply_llm_env_vars(config)

        # 更新缓存
        try:
            mtime = config_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        _global_config_cache = config
        _global_config_cache_mtime = mtime

        return config


def save_global_config(config: GlobalConfig | dict) -> None:
    """持久化全局配置到 ~/.agent.json。

    接受 GlobalConfig 对象或字典。线程安全。
    """
    global _global_config_cache, _global_config_cache_mtime

    with _config_lock:
        if isinstance(config, GlobalConfig):
            data = config.to_dict()
        else:
            data = config

        # 过滤掉与默认值相同的字段以保持配置文件简洁
        default = GlobalConfig()
        default_data = default.to_dict()
        filtered = {
            k: v for k, v in data.items() if v != default_data.get(k)
        }

        config_path = get_global_config_path()
        _write_json_file(config_path, filtered)

        # 更新缓存
        if isinstance(config, GlobalConfig):
            _global_config_cache = config
        else:
            _global_config_cache = GlobalConfig.from_dict(config)
        _global_config_cache_mtime = config_path.stat().st_mtime


def get_current_project_config(project_root: Path | None = None) -> ProjectConfig:
    """读取项目配置。

    项目配置存储在全局配置文件的 projects 字段中，
    以项目路径为键。线程安全。
    """
    global_config = get_global_config()
    project_path = _get_project_path_key(project_root)

    project_data = global_config.projects.get(project_path)
    if project_data is not None:
        return ProjectConfig.from_dict(project_data)

    return ProjectConfig()


def save_current_project_config(
    project_config: ProjectConfig,
    project_root: Path | None = None,
) -> None:
    """保存项目配置到全局配置文件。线程安全。"""
    global_config = get_global_config()
    project_path = _get_project_path_key(project_root)

    global_config.projects[project_path] = project_config.to_dict()
    save_global_config(global_config)


def get_project_settings(project_root: Path | None = None) -> Settings:
    """读取项目设置 .agent/settings.json。线程安全。"""
    settings_path = get_project_settings_path(project_root)
    data = _read_json_file(settings_path)

    if data is not None:
        return Settings.from_dict(data)
    return Settings()


def get_local_settings(project_root: Path | None = None) -> Settings:
    """读取本地项目设置 .agent/settings.local.json。线程安全。"""
    settings_path = get_local_settings_path(project_root)
    data = _read_json_file(settings_path)

    if data is not None:
        return Settings.from_dict(data)
    return Settings()


def get_managed_settings() -> Settings:
    """读取管理设置 ~/.agent/managed-settings.json。线程安全。"""
    settings_path = get_managed_settings_path()
    data = _read_json_file(settings_path)

    if data is not None:
        return Settings.from_dict(data)
    return Settings()


def get_user_settings() -> Settings:
    """读取用户设置 ~/.agent/settings.json。线程安全。"""
    settings_path = get_config_home_dir() / PROJECT_SETTINGS_FILENAME
    data = _read_json_file(settings_path)

    if data is not None:
        return Settings.from_dict(data)
    return Settings()


def get_initial_settings(
    project_root: Path | None = None,
    cli_flags: dict[str, Any] | None = None,
) -> Settings:
    """多源设置合并。

    合并优先级（从低到高）：
    1. 用户设置 (~/.agent/settings.json)
    2. 项目设置 (.agent/settings.json)
    3. 本地项目设置 (.agent/settings.local.json)
    4. 策略设置 (~/.agent/managed-settings.json)
    5. CLI 标志

    线程安全。
    """
    # 1. 用户设置（最低优先级）
    user_settings = get_user_settings()

    # 2. 项目设置
    project_settings = get_project_settings(project_root)

    # 3. 本地项目设置
    local_settings = get_local_settings(project_root)

    # 4. 策略设置（最高文件优先级）
    managed_settings = get_managed_settings()

    # 按优先级合并
    merged = _merge_settings(user_settings, project_settings)
    merged = _merge_settings(merged, local_settings)
    merged = _merge_settings(merged, managed_settings)

    # 5. CLI 标志覆盖
    if cli_flags:
        merged = _apply_cli_flags(merged, cli_flags)

    # 最后从环境变量补充 LLM 配置
    _apply_llm_env_vars_to_settings(merged)

    return merged


def apply_config_environment_variables(settings: Settings | None = None) -> dict[str, str]:
    """将配置映射到环境变量。

    返回需要设置的环境变量字典。
    """
    if settings is None:
        settings = get_initial_settings()

    env_vars: dict[str, str] = {}

    # 从 settings.env 中读取
    for key, value in settings.env.items():
        env_vars[key] = value

    # LLM 配置映射
    if settings.llm_api_key:
        env_vars[ENV_LLM_API_KEY] = settings.llm_api_key
    if settings.llm_base_url:
        env_vars[ENV_LLM_BASE_URL] = settings.llm_base_url

    return env_vars


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _get_project_path_key(project_root: Path | None = None) -> str:
    """获取项目路径作为配置键，统一使用正斜杠。"""
    root = project_root or Path.cwd()
    return str(root).replace("\\", "/")


def _apply_llm_env_vars(config: GlobalConfig) -> None:
    """从环境变量补充 LLM 配置到 GlobalConfig。"""
    if config.llm_api_key is None:
        config.llm_api_key = os.environ.get(ENV_LLM_API_KEY)
    if config.llm_base_url is None:
        config.llm_base_url = os.environ.get(ENV_LLM_BASE_URL)
    if config.llm_model is None:
        config.llm_model = os.environ.get(ENV_LLM_MODEL)


def _apply_llm_env_vars_to_settings(settings: Settings) -> None:
    """从环境变量补充 LLM 配置到 Settings。"""
    if settings.llm_api_key is None:
        settings.llm_api_key = os.environ.get(ENV_LLM_API_KEY)
    if settings.llm_base_url is None:
        env_val = os.environ.get(ENV_LLM_BASE_URL)
        settings.llm_base_url = env_val if env_val else DEFAULT_LLM_BASE_URL
    if settings.model is None:
        settings.model = os.environ.get(ENV_LLM_MODEL, DEFAULT_LLM_MODEL)


def _merge_settings(base: Settings, override: Settings) -> Settings:
    """合并两个 Settings 对象，override 中的非默认值覆盖 base。"""
    result = Settings()

    # 权限合并：列表追加
    result.permissions = Permissions(
        allow=base.permissions.allow + override.permissions.allow,
        deny=base.permissions.deny + override.permissions.deny,
        ask=base.permissions.ask + override.permissions.ask,
        default_mode=override.permissions.default_mode or base.permissions.default_mode,
        additional_directories=(
            base.permissions.additional_directories
            + override.permissions.additional_directories
        ),
    )

    # 标量字段：override 优先
    result.model = override.model if override.model is not None else base.model
    result.llm_base_url = (
        override.llm_base_url if override.llm_base_url is not None else base.llm_base_url
    )
    result.llm_api_key = (
        override.llm_api_key if override.llm_api_key is not None else base.llm_api_key
    )
    result.auto_compact = override.auto_compact if not base.auto_compact else override.auto_compact
    result.context_collapse = (
        override.context_collapse if override.context_collapse else base.context_collapse
    )
    result.verbose = override.verbose if override.verbose else base.verbose
    result.theme = override.theme if override.theme != "dark" else base.theme
    result.output_style = override.output_style if override.output_style else base.output_style
    result.api_key_helper = (
        override.api_key_helper if override.api_key_helper is not None else base.api_key_helper
    )
    result.enable_all_project_mcp_servers = (
        override.enable_all_project_mcp_servers
        if override.enable_all_project_mcp_servers
        else base.enable_all_project_mcp_servers
    )

    # 列表字段：追加
    result.enabled_mcpjson_servers = (
        base.enabled_mcpjson_servers + override.enabled_mcpjson_servers
    )
    result.disabled_mcpjson_servers = (
        base.disabled_mcpjson_servers + override.disabled_mcpjson_servers
    )

    # 字典字段：深度合并
    result.hooks = _deep_merge(base.hooks, override.hooks)
    result.mcp_servers = _deep_merge(base.mcp_servers, override.mcp_servers)
    result.env = {**base.env, **override.env}

    return result


def _apply_cli_flags(settings: Settings, flags: dict[str, Any]) -> Settings:
    """将 CLI 标志应用到 Settings。"""
    if "model" in flags and flags["model"]:
        settings.model = flags["model"]
    if "verbose" in flags:
        settings.verbose = flags["verbose"]
    if "llm_base_url" in flags and flags["llm_base_url"]:
        settings.llm_base_url = flags["llm_base_url"]
    if "llm_api_key" in flags and flags["llm_api_key"]:
        settings.llm_api_key = flags["llm_api_key"]
    if "auto_compact" in flags:
        settings.auto_compact = flags["auto_compact"]
    if "theme" in flags and flags["theme"]:
        settings.theme = flags["theme"]
    if "output_style" in flags and flags["output_style"]:
        settings.output_style = flags["output_style"]
    return settings


# ---------------------------------------------------------------------------
# 测试块
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import tempfile

    print("=" * 60)
    print("配置系统测试")
    print("=" * 60)

    # 测试 1: get_global_config() 返回配置对象
    print("\n--- 测试 1: get_global_config() ---")
    try:
        enable_configs()
        config = get_global_config()
        print(f"  配置对象类型: {type(config).__name__}")
        print(f"  theme: {config.theme}")
        print(f"  verbose: {config.verbose}")
        print(f"  auto_compact_enabled: {config.auto_compact_enabled}")
        print(f"  num_startups: {config.num_startups}")
        print("  [PASS] get_global_config() 成功")
    except Exception as e:
        print(f"  [FAIL] get_global_config() 失败: {e}")

    # 测试 2: save_global_config() 持久化成功
    print("\n--- 测试 2: save_global_config() ---")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            # 使用临时目录模拟主目录
            original_home = os.environ.get("HOME")
            os.environ["HOME"] = tmpdir

            # 重置缓存
            _global_config_cache = None
            _config_reading_allowed = True

            config = get_global_config()
            config.num_startups = 42
            config.theme = "light"
            config.llm_base_url = "https://custom.api.com/v1"
            save_global_config(config)

            # 验证文件已写入
            config_path = get_global_config_path()
            assert config_path.exists(), "配置文件未创建"
            raw = config_path.read_text(encoding="utf-8")
            parsed = json.loads(raw)
            assert parsed.get("numStartups") == 42, f"numStartups 不匹配: {parsed}"
            assert parsed.get("theme") == "light", f"theme 不匹配: {parsed}"
            print(f"  配置文件路径: {config_path}")
            print(f"  写入内容: {json.dumps(parsed, indent=2)}")
            print("  [PASS] save_global_config() 成功")

            # 恢复环境
            if original_home:
                os.environ["HOME"] = original_home
            else:
                del os.environ["HOME"]
            _global_config_cache = None
    except Exception as e:
        print(f"  [FAIL] save_global_config() 失败: {e}")

    # 测试 3: get_initial_settings() 多源合并
    print("\n--- 测试 3: get_initial_settings() ---")
    try:
        with tempfile.TemporaryDirectory() as home_dir, \
             tempfile.TemporaryDirectory() as project_dir_root:
            original_home = os.environ.get("HOME")
            os.environ["HOME"] = home_dir
            _global_config_cache = None
            _config_reading_allowed = True

            # 创建用户设置 (~/.agent/settings.json)
            user_dir = get_config_home_dir()
            user_dir.mkdir(parents=True, exist_ok=True)
            user_settings_path = user_dir / PROJECT_SETTINGS_FILENAME
            _write_json_file(
                user_settings_path,
                {
                    "model": "user-model",
                    "theme": "dark",
                    "verbose": True,
                    "permissions": {
                        "allow": [{"rule_type": "allow", "tool_pattern": "Read"}]
                    },
                },
            )

            # 创建项目设置 (project/.agent/settings.json)
            proj_dir = Path(project_dir_root) / PROJECT_CONFIG_DIR
            proj_dir.mkdir(parents=True, exist_ok=True)
            project_settings_path = proj_dir / PROJECT_SETTINGS_FILENAME
            _write_json_file(
                project_settings_path,
                {
                    "model": "project-model",
                    "llm_base_url": "https://project.api.com/v1",
                    "permissions": {
                        "deny": [{"rule_type": "deny", "tool_pattern": "Write"}]
                    },
                },
            )

            # 合并
            settings = get_initial_settings(
                project_root=Path(project_dir_root),
                cli_flags={"verbose": False},
            )

            print(f"  model (项目覆盖用户): {settings.model}")
            print(f"  llm_base_url: {settings.llm_base_url}")
            print(f"  verbose (CLI 覆盖): {settings.verbose}")
            print(f"  permissions.allow 数量: {len(settings.permissions.allow)}")
            print(f"  permissions.deny 数量: {len(settings.permissions.deny)}")

            # 验证合并结果
            assert settings.model == "project-model", "项目设置应覆盖用户设置"
            assert settings.verbose is False, "CLI 标志应覆盖文件设置"
            assert len(settings.permissions.allow) == 1, "权限 allow 应被合并"
            assert len(settings.permissions.deny) == 1, "权限 deny 应被合并"
            print("  [PASS] get_initial_settings() 多源合并成功")

            # 恢复环境
            if original_home:
                os.environ["HOME"] = original_home
            else:
                del os.environ["HOME"]
            _global_config_cache = None
    except Exception as e:
        print(f"  [FAIL] get_initial_settings() 失败: {e}")

    # 测试 4: LLM 配置项从环境变量读取
    print("\n--- 测试 4: LLM 环境变量 ---")
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            original_home = os.environ.get("HOME")
            os.environ["HOME"] = tmpdir
            _global_config_cache = None
            _config_reading_allowed = True

            # 设置环境变量
            os.environ[ENV_LLM_API_KEY] = "test-api-key-123"
            os.environ[ENV_LLM_BASE_URL] = "https://custom.openai.com/v1"
            os.environ[ENV_LLM_MODEL] = "gpt-4o"

            config = get_global_config()
            print(f"  llm_api_key: {config.llm_api_key}")
            print(f"  llm_base_url: {config.llm_base_url}")
            print(f"  llm_model: {config.llm_model}")

            assert config.llm_api_key == "test-api-key-123", "API key 应从环境变量读取"
            assert config.llm_base_url == "https://custom.openai.com/v1", "Base URL 应从环境变量读取"
            assert config.llm_model == "gpt-4o", "Model 应从环境变量读取"
            print("  [PASS] LLM 环境变量读取成功")

            # 测试 Settings 级别的环境变量
            settings = get_initial_settings(project_root=Path(tmpdir))
            print(f"  settings.llm_api_key: {settings.llm_api_key}")
            print(f"  settings.llm_base_url: {settings.llm_base_url}")
            print(f"  settings.model: {settings.model}")
            assert settings.llm_api_key == "test-api-key-123"
            assert settings.llm_base_url == "https://custom.openai.com/v1"
            assert settings.model == "gpt-4o"
            print("  [PASS] Settings 级别 LLM 环境变量读取成功")

            # 清理环境变量
            del os.environ[ENV_LLM_API_KEY]
            del os.environ[ENV_LLM_BASE_URL]
            del os.environ[ENV_LLM_MODEL]

            # 恢复环境
            if original_home:
                os.environ["HOME"] = original_home
            else:
                del os.environ["HOME"]
            _global_config_cache = None
    except Exception as e:
        print(f"  [FAIL] LLM 环境变量测试失败: {e}")
        # 清理
        for var in [ENV_LLM_API_KEY, ENV_LLM_BASE_URL, ENV_LLM_MODEL]:
            os.environ.pop(var, None)

    # 测试 5: apply_config_environment_variables()
    print("\n--- 测试 5: apply_config_environment_variables() ---")
    try:
        settings = Settings(
            llm_api_key="env-test-key",
            llm_base_url="https://env-test.api.com/v1",
            env={"MY_VAR": "my_value", "OTHER_VAR": "other_value"},
        )
        env_vars = apply_config_environment_variables(settings)
        print(f"  环境变量映射: {env_vars}")
        assert env_vars.get(ENV_LLM_API_KEY) == "env-test-key"
        assert env_vars.get(ENV_LLM_BASE_URL) == "https://env-test.api.com/v1"
        assert env_vars.get("MY_VAR") == "my_value"
        print("  [PASS] apply_config_environment_variables() 成功")
    except Exception as e:
        print(f"  [FAIL] apply_config_environment_variables() 失败: {e}")

    # 测试 6: 线程安全
    print("\n--- 测试 6: 线程安全 ---")
    try:
        import concurrent.futures

        _config_reading_allowed = True
        _global_config_cache = None

        def read_config():
            return get_global_config()

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(read_config) for _ in range(50)]
            results = [f.result() for f in futures]

        assert all(isinstance(r, GlobalConfig) for r in results)
        print(f"  50 个并发读取全部成功")
        print("  [PASS] 线程安全测试通过")
    except Exception as e:
        print(f"  [FAIL] 线程安全测试失败: {e}")

    print("\n" + "=" * 60)
    print("所有测试完成")
    print("=" * 60)
