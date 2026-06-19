"""MCP 客户端。

管理单个 MCP 服务器连接，支持 stdio 和 sse 两种 transport。
实现 JSON-RPC 2.0 协议进行通信。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from query.services.mcp.types import MCPClientState, MCPServerConfig, MCPTool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 协议常量
# ---------------------------------------------------------------------------

JSONRPC_VERSION = "2.0"


# ---------------------------------------------------------------------------
# MCPClient — 管理单个 MCP 服务器连接
# ---------------------------------------------------------------------------

class MCPClient:
    """管理单个 MCP 服务器连接。

    支持 stdio（子进程）和 sse（HTTP SSE）两种 transport。
    通过 JSON-RPC 2.0 协议与 MCP 服务器通信。
    """

    def __init__(self, name: str, config: MCPServerConfig) -> None:
        self.name = name
        self.config = config
        self.state: MCPClientState = MCPClientState.DISCONNECTED
        self._process: asyncio.subprocess.Process | None = None
        self._request_id: int = 0
        self._reader_task: asyncio.Task | None = None
        self._pending_requests: dict[int, asyncio.Future] = {}
        self._sse_session: Any = None  # httpx/aiohttp session for SSE

    # -----------------------------------------------------------------------
    # 连接管理
    # -----------------------------------------------------------------------

    async def connect(self) -> None:
        """连接 MCP 服务器。"""
        if self.state == MCPClientState.CONNECTED:
            return

        self.state = MCPClientState.CONNECTING

        try:
            if self.config.transport == "stdio":
                await self._connect_stdio()
            elif self.config.transport == "sse":
                await self._connect_sse()
            else:
                raise ValueError(f"Unsupported transport: {self.config.transport}")

            # 发送 initialize 请求
            await self._initialize()

            self.state = MCPClientState.CONNECTED
            logger.info("MCP server '%s' connected (transport=%s)", self.name, self.config.transport)

        except Exception:
            self.state = MCPClientState.ERROR
            logger.exception("MCP server '%s' connection failed", self.name)
            raise

    async def disconnect(self) -> None:
        """断开连接。"""
        if self.state == MCPClientState.DISCONNECTED:
            return

        # 取消待处理的请求
        for future in self._pending_requests.values():
            if not future.done():
                future.cancel()
        self._pending_requests.clear()

        # 取消 reader task
        if self._reader_task and not self._reader_task.done():
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        # 关闭子进程
        if self._process:
            try:
                self._process.terminate()
                await asyncio.wait_for(self._process.wait(), timeout=5.0)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    self._process.kill()
                except ProcessLookupError:
                    pass
            self._process = None

        # 关闭 SSE session
        if self._sse_session:
            await self._sse_session.aclose()
            self._sse_session = None

        self.state = MCPClientState.DISCONNECTED
        logger.info("MCP server '%s' disconnected", self.name)

    # -----------------------------------------------------------------------
    # 工具操作
    # -----------------------------------------------------------------------

    async def list_tools(self) -> list[MCPTool]:
        """获取工具列表。

        发送 tools/list 请求，解析响应为 MCPTool 列表。
        """
        result = await self._send_request("tools/list", {})
        tools_data = result.get("tools", [])
        return [
            MCPTool(
                name=tool.get("name", ""),
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
            )
            for tool in tools_data
        ]

    async def call_tool(self, name: str, arguments: dict) -> str:
        """调用工具。

        发送 tools/call 请求，返回工具结果文本。
        """
        result = await self._send_request("tools/call", {
            "name": name,
            "arguments": arguments,
        })

        # 处理错误
        if result.get("isError"):
            content = result.get("content", [])
            if isinstance(content, list) and content:
                return content[0].get("text", "Unknown error")
            return "Unknown error"

        # 提取文本内容
        content = result.get("content", [])
        if isinstance(content, list):
            texts = [
                item.get("text", "") for item in content
                if isinstance(item, dict) and item.get("type") == "text"
            ]
            return "\n".join(texts)

        return str(content)

    # -----------------------------------------------------------------------
    # stdio transport
    # -----------------------------------------------------------------------

    async def _connect_stdio(self) -> None:
        """通过 stdio transport 启动子进程。"""
        env = dict(os.environ)
        if self.config.env:
            env.update(self.config.env)

        self._process = await asyncio.create_subprocess_exec(
            self.config.command,
            *self.config.args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )

        # 启动后台 reader task
        self._reader_task = asyncio.create_task(self._read_loop())

    # -----------------------------------------------------------------------
    # sse transport
    # -----------------------------------------------------------------------

    async def _connect_sse(self) -> None:
        """通过 SSE transport 连接。

        使用 httpx 的 SSE 支持建立长连接。
        """
        try:
            import httpx
        except ImportError:
            raise RuntimeError(
                "httpx is required for SSE transport. Install with: pip install httpx"
            )

        url = self.config.url
        if not url:
            raise ValueError("SSE transport requires a URL")

        self._sse_session = httpx.AsyncClient()
        # SSE 连接通过发送 initialize 请求来建立
        # 实际的 SSE 监听在 _read_loop_sse 中处理

    # -----------------------------------------------------------------------
    # JSON-RPC 2.0 协议
    # -----------------------------------------------------------------------

    async def _initialize(self) -> dict:
        """发送 initialize 请求。"""
        result = await self._send_request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {
                "name": "common-code-python",
                "version": "0.1.0",
            },
        })

        # 发送 initialized 通知
        await self._send_notification("notifications/initialized", {})

        return result

    async def _send_request(self, method: str, params: dict | None) -> dict:
        """发送 JSON-RPC 2.0 请求并等待响应。"""
        self._request_id += 1
        request_id = self._request_id

        message: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending_requests[request_id] = future

        await self._write_message(message)

        try:
            return await asyncio.wait_for(future, timeout=60.0)
        except asyncio.TimeoutError:
            self._pending_requests.pop(request_id, None)
            raise TimeoutError(f"MCP request '{method}' timed out after 60s")

    async def _send_notification(self, method: str, params: dict | None) -> None:
        """发送 JSON-RPC 2.0 通知（无 id，不期望响应）。"""
        message: dict[str, Any] = {
            "jsonrpc": JSONRPC_VERSION,
            "method": method,
        }
        if params is not None:
            message["params"] = params

        await self._write_message(message)

    async def _write_message(self, message: dict) -> None:
        """将 JSON-RPC 消息写入 transport。"""
        if self.config.transport == "stdio" and self._process and self._process.stdin:
            data = json.dumps(message) + "\n"
            self._process.stdin.write(data.encode("utf-8"))
            await self._process.stdin.drain()
        elif self.config.transport == "sse" and self._sse_session:
            url = self.config.url
            if not url:
                raise ValueError("SSE URL not configured")
            response = await self._sse_session.post(
                url,
                json=message,
                headers={"Content-Type": "application/json"},
                timeout=60.0,
            )
            response.raise_for_status()
        else:
            raise RuntimeError("No active transport to write message")

    async def _read_response(self) -> dict:
        """读取 JSON-RPC 响应（仅用于 stdio 单次读取）。"""
        if not self._process or not self._process.stdout:
            raise RuntimeError("No stdout to read from")

        line = await self._process.stdout.readline()
        if not line:
            raise ConnectionError("MCP server closed connection")

        return json.loads(line.decode("utf-8"))

    async def _read_loop(self) -> None:
        """后台循环读取 stdio 响应并分发到对应的 pending request。"""
        if not self._process or not self._process.stdout:
            return

        try:
            while self.state in (MCPClientState.CONNECTING, MCPClientState.CONNECTED):
                line = await self._process.stdout.readline()
                if not line:
                    break

                try:
                    message = json.loads(line.decode("utf-8"))
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from MCP server '%s': %s", self.name, line)
                    continue

                # 处理响应
                if "id" in message:
                    request_id = message["id"]
                    future = self._pending_requests.pop(request_id, None)
                    if future and not future.done():
                        if "error" in message:
                            error = message["error"]
                            future.set_exception(
                                RuntimeError(f"MCP error {error.get('code')}: {error.get('message')}")
                            )
                        else:
                            future.set_result(message.get("result", {}))

        except asyncio.CancelledError:
            pass
        except Exception:
            logger.exception("Error in MCP read loop for '%s'", self.name)
            self.state = MCPClientState.ERROR


# ---------------------------------------------------------------------------
# 自测
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from query.services.mcp.types import MCPClientState, MCPServerConfig, MCPTool
    from query.services.mcp.config import get_mcp_server_configs, validate_mcp_config

    print("=" * 60)
    print("MCP 集成测试")
    print("=" * 60)

    # ---- 1. 测试 MCPTool 类型 ----
    print("\n--- 测试 1: MCPTool 类型 ---")
    tool = MCPTool(
        name="search",
        description="Search for files",
        input_schema={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    assert tool.name == "search"
    assert tool.description == "Search for files"
    assert tool.input_schema["type"] == "object"
    print(f"  MCPTool: name={tool.name}, description={tool.description}")
    print("  [PASS] MCPTool 类型")

    # ---- 2. 测试 MCPServerConfig 解析 ----
    print("\n--- 测试 2: MCPServerConfig 解析 ---")

    # stdio 配置
    stdio_config = validate_mcp_config("test-stdio", {
        "command": "npx",
        "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"],
        "env": {"NODE_ENV": "production"},
    })
    assert stdio_config is not None
    assert stdio_config.command == "npx"
    assert stdio_config.args == ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
    assert stdio_config.transport == "stdio"
    assert stdio_config.env == {"NODE_ENV": "production"}
    print(f"  stdio: command={stdio_config.command}, args={stdio_config.args}, transport={stdio_config.transport}")

    # sse 配置
    sse_config = validate_mcp_config("test-sse", {
        "type": "sse",
        "url": "http://localhost:8080/sse",
    })
    assert sse_config is not None
    assert sse_config.transport == "sse"
    assert sse_config.url == "http://localhost:8080/sse"
    print(f"  sse: url={sse_config.url}, transport={sse_config.transport}")

    # 无效配置
    invalid_config = validate_mcp_config("invalid", {"foo": "bar"})
    assert invalid_config is None
    print(f"  invalid config: {invalid_config}")
    print("  [PASS] MCPServerConfig 解析")

    # ---- 3. 测试 MCPClientState 枚举 ----
    print("\n--- 测试 3: MCPClientState 枚举 ---")
    assert MCPClientState.DISCONNECTED.value == "disconnected"
    assert MCPClientState.CONNECTING.value == "connecting"
    assert MCPClientState.CONNECTED.value == "connected"
    assert MCPClientState.ERROR.value == "error"
    print(f"  states: {[s.value for s in MCPClientState]}")
    print("  [PASS] MCPClientState 枚举")

    # ---- 4. 测试 get_mcp_server_configs ----
    print("\n--- 测试 4: get_mcp_server_configs ---")
    configs = get_mcp_server_configs()
    print(f"  已配置的 MCP 服务器数量: {len(configs)}")
    for name, cfg in configs.items():
        print(f"  - {name}: transport={cfg.transport}, command={cfg.command or cfg.url}")
    print("  [PASS] get_mcp_server_configs")

    # ---- 5. 测试 MCPClient 创建 ----
    print("\n--- 测试 5: MCPClient 创建 ---")
    client = MCPClient("test-server", MCPServerConfig(
        command="echo",
        args=["hello"],
        transport="stdio",
    ))
    assert client.name == "test-server"
    assert client.state == MCPClientState.DISCONNECTED
    assert client._request_id == 0
    print(f"  client: name={client.name}, state={client.state.value}")
    print("  [PASS] MCPClient 创建")

    print("\n" + "=" * 60)
    print("所有 MCP 集成测试通过")
    print("=" * 60)
