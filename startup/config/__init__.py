"""配置系统核心模块。

提供全局配置和项目配置的读取、保存、合并功能。
线程安全，使用 threading.Lock 保护共享状态。

配置文件路径：
  - 全局配置：~/.agent/config.json
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
import logging
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

from startup.config.constants import (
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
from startup.config.types import Permissions, PermissionRule, Settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Dataclass 定义
# ---------------------------------------------------------------------------


@dataclass
class CustomLLMModel:
    """自定义 LLM 模型配置。"""
    model_id: str
    context_window: int = 200000

    def to_dict(self) -> dict[str, Any]:
        return {"model_id": self.model_id, "context_window": self.context_window}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CustomLLMModel:
        return cls(
            model_id=data.get("model_id", ""),
            context_window=data.get("context_window", 200000),
        )


@dataclass
class CustomLLMProvider:
    """自定义 LLM 供应商配置。"""
    id: str
    name: str
    base_url: str
    api_key: str = ""
    api_format: str = "openai"  # "openai" 或 "anthropic"
    models: list[CustomLLMModel] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "api_key": self.api_key,
            "api_format": self.api_format,
            "models": [m.to_dict() for m in self.models],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CustomLLMProvider:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            base_url=data.get("base_url", ""),
            api_key=data.get("api_key", ""),
            api_format=data.get("api_format", "openai"),
            models=[CustomLLMModel.from_dict(m) for m in data.get("models", [])],
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
    # 自定义 LLM 供应商列表（用户在设置面板配置的供应商）
    llm_providers: list[dict[str, Any]] = field(default_factory=list)
    # 当前激活的供应商 ID（自定义供应商的 id 或插件供应商的 name）
    active_provider: str | None = None
    # 当前激活的模型 ID
    active_model: str | None = None
    # 记忆插件配置：记录激活的记忆后端名，重启后恢复
    # 结构 {"active": "memory-backend-name" | None}
    memory: dict[str, Any] = field(default_factory=dict)
    # 记忆功能总开关：默认关闭。关闭时启动不加载记忆插件与向量化模型
    memory_enabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        """转换为 JSON 友好的字典，使用 camelCase 键名。"""
        d = asdict(self)
        # 转换键名为 camelCase，与既有调用方约定保持一致
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
            "llm_providers": "llm_providers",
            "active_provider": "active_provider",
            "active_model": "active_model",
            "memory_enabled": "memoryEnabled",
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
            llm_providers=data.get("llm_providers", []),
            active_provider=data.get("active_provider"),
            active_model=data.get("active_model"),
            memory=data.get("memory", {}),
            memory_enabled=data.get("memoryEnabled", False),
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
    """获取全局配置文件路径 ~/.agent/config.json。"""
    return get_config_home_dir() / GLOBAL_CONFIG_FILENAME


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


def _ensure_config_file(path: Path, default_content: str = "{}") -> None:
    """确保配置文件存在，不存在则创建默认配置。"""
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(default_content, encoding="utf-8")
        logger.info("Created default config: %s", path)


# 默认全局配置内容（~/.agent/config.json）
_DEFAULT_GLOBAL_CONFIG = """\
{
  "llm_base_url": "",
  "llm_api_key": "",
  "llm_model": ""
}
"""

# 默认用户设置内容（~/.agent/settings.json）
_DEFAULT_USER_SETTINGS = """\
{
  "hooks": {
    "PreToolUse": [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python startup/hooks/scripts/validate_command.py",
            "timeout": 10
          }
        ]
      },
      {
        "matcher": "Write|Edit|MultiEdit",
        "hooks": [
          {
            "type": "command",
            "command": "python startup/hooks/scripts/protect_sensitive_files.py",
            "timeout": 10
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "Write|Edit|MultiEdit|Bash",
        "hooks": [
          {
            "type": "command",
            "command": "python startup/hooks/scripts/audit_log.py",
            "timeout": 15
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup",
        "hooks": [
          {
            "type": "command",
            "command": "python startup/hooks/scripts/session_context.py",
            "timeout": 15
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "python startup/hooks/scripts/pre_compact_save.py",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
"""


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

    # 确保 ~/.agent/ 目录和配置文件存在
    _ensure_config_file(get_global_config_path(), _DEFAULT_GLOBAL_CONFIG)
    _ensure_config_file(get_config_home_dir() / PROJECT_SETTINGS_FILENAME, _DEFAULT_USER_SETTINGS)

    # 预加载全局配置以验证文件格式
    get_global_config()


def get_global_config() -> GlobalConfig:
    """读取全局配置 ~/.agent/config.json。

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
    """持久化全局配置到 ~/.agent/config.json。

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
    4. CLI 标志
    5. 策略设置 (~/.agent/managed-settings.json)  ← 最高，管理员强制

    环境变量 (LLM_API_KEY, LLM_BASE_URL, LLM_MODEL) 不参与覆盖合并，
    而是在合并完成后对仍为 None 的 LLM 字段做兜底补充。

    线程安全。
    """
    # 1. 用户设置（最低优先级）
    user_settings = get_user_settings()

    # 2. 项目设置
    project_settings = get_project_settings(project_root)

    # 3. 本地项目设置
    local_settings = get_local_settings(project_root)

    # 4. 策略设置（最高优先级，管理员强制）
    managed_settings = get_managed_settings()

    # 按优先级合并（从低到高：用户 < 项目 < 本地 < CLI 标志 < 策略）
    merged = _merge_settings(user_settings, project_settings)
    merged = _merge_settings(merged, local_settings)

    # 5. CLI 标志覆盖
    if cli_flags:
        merged = _apply_cli_flags(merged, cli_flags)

    # 6. 策略设置最高优先级（管理员强制，不可被 CLI 标志绕过）
    merged = _merge_settings(merged, managed_settings)

    # 最后从环境变量补充 LLM 配置（仅当字段为 None 时兜底）
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

    # LLM 配置映射：优先用 GlobalConfig（~/.agent/config.json），这是用户配置的真实来源
    # settings.json 里的 llm 字段可能只是默认值，不能覆盖 config.json
    try:
        global_config = get_global_config()
        if global_config.llm_api_key:
            env_vars[ENV_LLM_API_KEY] = global_config.llm_api_key
        if global_config.llm_base_url:
            env_vars[ENV_LLM_BASE_URL] = global_config.llm_base_url
        if global_config.llm_model:
            env_vars[ENV_LLM_MODEL] = global_config.llm_model
    except Exception:
        # GlobalConfig 未初始化时，回退到 settings
        if settings.llm_api_key:
            env_vars[ENV_LLM_API_KEY] = settings.llm_api_key
        if settings.llm_base_url:
            env_vars[ENV_LLM_BASE_URL] = settings.llm_base_url

    return env_vars


# ---------------------------------------------------------------------------
# 内部辅助
# ---------------------------------------------------------------------------


def _apply_llm_env_vars(config: GlobalConfig) -> None:
    """从环境变量补充 LLM 配置到 GlobalConfig。"""
    if config.llm_api_key is None:
        config.llm_api_key = os.environ.get(ENV_LLM_API_KEY)
    if config.llm_base_url is None:
        config.llm_base_url = os.environ.get(ENV_LLM_BASE_URL)
    if config.llm_model is None:
        config.llm_model = os.environ.get(ENV_LLM_MODEL)


def _apply_llm_env_vars_to_settings(settings: Settings) -> None:
    """环境变量优先级最高，覆盖 LLM 配置；未设置时用默认值兜底。

    仅对 LLM 三字段 (llm_api_key / llm_base_url / model) 生效。
    注意：不在 base_url 和 model 为 None 时设默认值--那会覆盖 config.json 的配置。
    """
    env_api_key = os.environ.get(ENV_LLM_API_KEY)
    if env_api_key:
        settings.llm_api_key = env_api_key

    env_base_url = os.environ.get(ENV_LLM_BASE_URL)
    if env_base_url:
        settings.llm_base_url = env_base_url
    # 不再设默认值--让 config.json 的配置通过 get_global_config 生效

    env_model = os.environ.get(ENV_LLM_MODEL)
    if env_model:
        settings.model = env_model
    # 不再设默认值--让 config.json 的配置通过 get_global_config 生效


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
