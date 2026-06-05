"""配置常量定义。

这些常量放在单独文件中以避免循环依赖。
不要在此文件中添加任何导入。
"""

# 全局配置文件名（位于用户主目录下）
GLOBAL_CONFIG_FILENAME = ".agent.json"

# 项目配置目录名
PROJECT_CONFIG_DIR = ".agent"

# 项目设置文件名
PROJECT_SETTINGS_FILENAME = "settings.json"

# 本地设置文件名（不提交到版本控制）
LOCAL_SETTINGS_FILENAME = "settings.local.json"

# 管理设置文件名（企业策略）
MANAGED_SETTINGS_FILENAME = "managed-settings.json"

# 通知渠道
NOTIFICATION_CHANNELS = [
    "auto",
    "iterm2",
    "iterm2_with_bell",
    "terminal_bell",
    "kitty",
    "ghostty",
    "notifications_disabled",
]

# 编辑器模式
EDITOR_MODES = ["normal", "vim"]

# 队友模式
TEAMMATE_MODES = ["auto", "tmux", "in-process"]

# 默认 LLM 配置（从 .env 文件读取，此处仅作回退值）
DEFAULT_LLM_BASE_URL = "https://api-inference.modelscope.cn/v1"
DEFAULT_LLM_MODEL = "Qwen/Qwen3-235B-A22B"
DEFAULT_LLM_API_KEY = ""  # 应通过 .env 文件或环境变量设置

# LLM 环境变量名（.env 文件）
ENV_LLM_BASE_URL = "LLM_BASE_URL"
ENV_LLM_API_KEY = "LLM_API_KEY"
ENV_LLM_MODEL = "LLM_MODEL"

# 配置备份相关
MAX_BACKUPS = 5
MIN_BACKUP_INTERVAL_MS = 60_000

# 配置刷新轮询间隔（毫秒）
CONFIG_FRESHNESS_POLL_MS = 1000

# 配置写入显示阈值
CONFIG_WRITE_DISPLAY_THRESHOLD = 20
