"""MCP 类型定义。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# MCPServerConfig — MCP 服务器配置
# ---------------------------------------------------------------------------

@dataclass
class MCPServerConfig:
    """MCP 服务器配置。"""

    command: str = ""
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    transport: str = "stdio"  # "stdio" | "sse"
    url: str | None = None  # SSE transport 的 URL


# ---------------------------------------------------------------------------
# MCPTool — MCP 工具定义
# ---------------------------------------------------------------------------

@dataclass
class MCPTool:
    """MCP 工具定义。"""

    name: str
    description: str
    input_schema: dict = field(default_factory=dict)  # JSON Schema


# ---------------------------------------------------------------------------
# MCPClientState — 连接状态枚举
# ---------------------------------------------------------------------------

class MCPClientState(Enum):
    """MCP 客户端连接状态。"""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    ERROR = "error"
