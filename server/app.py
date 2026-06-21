"""FastAPI 应用定义，提供 HTTP 接口供 Electron 壳调用。

路由：
  GET  /                — 测试页
  GET  /api/state       — 获取会话状态
  POST /api/chat        — SSE 流式对话
  POST /api/command     — 斜杠命令
  POST /api/permission  — 回传权限决策
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse

from query.loop import LoopResult
from query.services.api.llm import StreamEvent
from tools.commands.commands import find_command
from tools.commands.commands_context import CommandContext

# 全局变量，由 __main__.py 启动时设置
app_state: Any = None
engine: Any = None
permission_bridge: Any = None

app = FastAPI(title="Common Code Server")

# 开发阶段允许所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# GET / — 测试页
# ---------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
async def index() -> str:
    """返回端到端验证测试页。

    包含输入框、发送按钮、消息显示区、状态栏，
    用 fetch + ReadableStream 收 SSE 流，处理权限请求。
    """
    return """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>Common Code</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, "Microsoft YaHei", sans-serif; background: #1e1e1e; color: #d4d4d4; height: 100vh; display: flex; flex-direction: column; }
  #status-bar { padding: 6px 16px; background: #2d2d2d; border-bottom: 1px solid #404040; font-size: 12px; color: #888; display: flex; gap: 16px; }
  #messages { flex: 1; overflow-y: auto; padding: 16px; display: flex; flex-direction: column; gap: 12px; }
  .msg { padding: 8px 12px; border-radius: 6px; white-space: pre-wrap; word-break: break-word; }
  .msg.user { background: #2a4a6a; align-self: flex-end; max-width: 80%; }
  .msg.assistant { background: #2d2d2d; border: 1px solid #404040; max-width: 90%; }
  .msg.tool { background: #1a3a1a; border: 1px solid #2a5a2a; font-size: 12px; max-width: 90%; }
  .msg-prefix { font-weight: bold; margin-right: 6px; }
  .msg.user .msg-prefix { color: #6cb6ff; }
  .msg.assistant .msg-prefix { color: #fff; }
  .msg.tool .msg-prefix { color: #4ec9b0; }
  #input-bar { padding: 12px 16px; background: #2d2d2d; border-top: 1px solid #404040; display: flex; gap: 8px; }
  #prompt-input { flex: 1; background: #1e1e1e; border: 1px solid #404040; color: #d4d4d4; padding: 8px 12px; border-radius: 4px; font-size: 14px; }
  #prompt-input:focus { outline: none; border-color: #007acc; }
  #send-btn { background: #007acc; color: #fff; border: none; padding: 8px 20px; border-radius: 4px; cursor: pointer; font-size: 14px; }
  #send-btn:disabled { background: #555; cursor: not-allowed; }
  #send-btn:hover:not(:disabled) { background: #1a8ad4; }
  #perm-dialog { position: fixed; top: 50%; left: 50%; transform: translate(-50%,-50%); background: #2d2d2d; border: 1px solid #404040; border-radius: 8px; padding: 24px; min-width: 400px; z-index: 100; display: none; }
  #perm-dialog h3 { color: #ffc107; margin-bottom: 12px; }
  #perm-dialog .perm-info { margin: 6px 0; font-size: 13px; }
  #perm-dialog .perm-buttons { display: flex; gap: 8px; margin-top: 16px; justify-content: flex-end; }
  #perm-dialog button { padding: 6px 16px; border: none; border-radius: 4px; cursor: pointer; font-size: 13px; }
  #perm-allow { background: #4ec9b0; color: #000; }
  #perm-deny { background: #f44747; color: #fff; }
  #perm-always { background: #007acc; color: #fff; }
</style>
</head>
<body>
<div id="status-bar">
  <span id="st-model">model: -</span>
  <span id="st-tokens">tokens: 0in/0out</span>
  <span id="st-cost">cost: $0.0000</span>
</div>
<div id="messages"></div>
<div id="input-bar">
  <input id="prompt-input" type="text" placeholder="输入消息或 /命令..." autocomplete="off">
  <button id="send-btn" onclick="send()">发送</button>
</div>
<div id="perm-dialog">
  <h3>&#9888; 需要权限确认</h3>
  <div class="perm-info"><strong>工具:</strong> <span id="perm-tool"></span></div>
  <div class="perm-info"><strong>原因:</strong> <span id="perm-reason"></span></div>
  <div class="perm-info"><strong>参数:</strong> <pre id="perm-input" style="margin-top:4px;max-height:200px;overflow:auto;font-size:12px;"></pre></div>
  <div class="perm-buttons">
    <button id="perm-deny" onclick="resolvePerm('deny')">拒绝</button>
    <button id="perm-always" onclick="resolvePerm('always_allow')">总是允许</button>
    <button id="perm-allow" onclick="resolvePerm('allow')">允许</button>
  </div>
</div>
<script>
const input = document.getElementById('prompt-input');
const sendBtn = document.getElementById('send-btn');
const msgBox = document.getElementById('messages');
const permDialog = document.getElementById('perm-dialog');
let currentPermId = null;
let assistantEl = null; // 当前流式 assistant 消息元素

input.addEventListener('keydown', e => { if (e.key === 'Enter') send(); });

function addMsg(role, text) {
  const el = document.createElement('div');
  el.className = 'msg ' + role;
  const prefixMap = { user: '你', assistant: 'AI', tool: '工具' };
  el.innerHTML = '<span class="msg-prefix">' + (prefixMap[role] || role) + '</span>';
  el.appendChild(document.createTextNode(text));
  msgBox.appendChild(el);
  msgBox.scrollTop = msgBox.scrollHeight;
  return el;
}

async function send() {
  const prompt = input.value.trim();
  if (!prompt) return;
  input.value = '';
  sendBtn.disabled = true;

  // 斜杠命令走 /api/command
  if (prompt.startsWith('/')) {
    addMsg('user', prompt);
    try {
      const resp = await fetch('/api/command', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command: prompt }),
      });
      const data = await resp.json();
      addMsg('assistant', data.output || '(无输出)');
    } catch (e) {
      addMsg('assistant', '命令执行出错: ' + e.message);
    }
    sendBtn.disabled = false;
    return;
  }

  // 普通消息走 /api/chat SSE 流
  addMsg('user', prompt);
  assistantEl = null;

  try {
    const resp = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt }),
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      // 按 SSE 格式分割：data: {...}\\n\\n
      let idx;
      while ((idx = buffer.indexOf('\\n\\n')) >= 0) {
        const chunk = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        if (chunk.startsWith('data: ')) {
          handleSSE(JSON.parse(chunk.slice(6)));
        }
      }
    }
  } catch (e) {
    addMsg('assistant', '连接出错: ' + e.message);
  }
  sendBtn.disabled = false;
}

function handleSSE(ev) {
  if (ev.type === 'stream') {
    if (ev.event_type === 'content' && ev.content) {
      if (!assistantEl) assistantEl = addMsg('assistant', '');
      assistantEl.textContent += ev.content;
      msgBox.scrollTop = msgBox.scrollHeight;
    } else if (ev.event_type === 'usage' && ev.usage) {
      document.getElementById('st-tokens').textContent =
        'tokens: ' + (ev.usage.prompt_tokens || 0) + 'in/' + (ev.usage.completion_tokens || 0) + 'out';
    } else if (ev.event_type === 'error') {
      addMsg('assistant', '错误: ' + (ev.error || ''));
    }
  } else if (ev.type === 'message') {
    const m = ev.message;
    if (m.role === 'tool') {
      const preview = (m.content || '').slice(0, 200);
      addMsg('tool', preview);
    } else if (m.role === 'assistant' && m.tool_calls) {
      // 有工具调用的 assistant 消息，显示工具名
      if (assistantEl) assistantEl.textContent = m.content || '';
      m.tool_calls.forEach(tc => {
        const name = tc.function ? tc.function.name : (tc.name || 'unknown');
        addMsg('tool', '[调用工具: ' + name + ']');
      });
    }
  } else if (ev.type === 'loop_result') {
    if (ev.reason && ev.reason !== 'completed') {
      addMsg('assistant', '循环退出: ' + ev.reason + (ev.error ? ' (' + ev.error + ')' : ''));
    }
    assistantEl = null;
  } else if (ev.type === 'permission_request') {
    showPermDialog(ev);
  }
  // 流结束后刷新状态栏
  if (ev.type === 'stream' && ev.event_type === 'done') {
    fetchState();
  }
}

function showPermDialog(req) {
  currentPermId = req.request_id;
  document.getElementById('perm-tool').textContent = req.tool_name || '';
  document.getElementById('perm-reason').textContent = req.reason || '';
  document.getElementById('perm-input').textContent = JSON.stringify(req.tool_input || {}, null, 2);
  permDialog.style.display = 'block';
}

async function resolvePerm(decision) {
  permDialog.style.display = 'none';
  if (!currentPermId) return;
  try {
    await fetch('/api/permission', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ request_id: currentPermId, decision }),
    });
  } catch (e) {
    addMsg('assistant', '权限回传出错: ' + e.message);
  }
  currentPermId = null;
}

async function fetchState() {
  try {
    const resp = await fetch('/api/state');
    const data = await resp.json();
    document.getElementById('st-model').textContent = 'model: ' + (data.model || '-');
    const u = data.token_usage || {};
    document.getElementById('st-tokens').textContent =
      'tokens: ' + (u.input_tokens || 0) + 'in/' + (u.output_tokens || 0) + 'out';
    document.getElementById('st-cost').textContent = 'cost: $' + (data.total_cost_usd || 0).toFixed(4);
  } catch (e) {}
}

fetchState();
input.focus();
</script>
</body>
</html>"""


# ---------------------------------------------------------------------------
# GET /api/state — 获取会话状态
# ---------------------------------------------------------------------------


@app.get("/api/state")
async def get_state() -> dict:
    """返回会话状态：消息历史、模型、token 用量、成本。"""
    state = app_state.get_state()
    usage = state.token_usage
    return {
        "messages": engine.mutable_messages,
        "model": state.model,
        "token_usage": {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_read_input_tokens": usage.cache_read_input_tokens,
            "cache_creation_input_tokens": usage.cache_creation_input_tokens,
        },
        "total_cost_usd": state.total_cost_usd,
    }


# ---------------------------------------------------------------------------
# 事件序列化
# ---------------------------------------------------------------------------


def serialize_event(event: Any) -> dict:
    """把引擎事件序列化为 JSON 字典。

    引擎 yield 三种事件：
      - StreamEvent: 流式事件（content/usage/error/done/tool_call_delta）
      - dict: OpenAI 格式消息（assistant/tool/compact boundary）
      - LoopResult: 循环退出结果

    只放非 None 的字段，避免前端收到一堆 null。
    """
    if isinstance(event, StreamEvent):
        result: dict = {"type": "stream", "event_type": event.type}
        if event.content is not None:
            result["content"] = event.content
        if event.usage is not None:
            result["usage"] = event.usage
        if event.error is not None:
            result["error"] = str(event.error)
        if event.finish_reason is not None:
            result["finish_reason"] = event.finish_reason
        return result

    if isinstance(event, dict):
        return {"type": "message", "message": event}

    if isinstance(event, LoopResult):
        result = {"type": "loop_result", "reason": event.reason}
        if event.error is not None:
            result["error"] = str(event.error)
        return result

    # 未知事件类型，兜底处理
    return {"type": "unknown", "data": str(event)}


# ---------------------------------------------------------------------------
# POST /api/chat — SSE 流式对话
# ---------------------------------------------------------------------------


async def chat_event_stream(prompt: str):
    """SSE 事件生成器。

    引擎的 submitMessage 是 async generator，当它挂起在 permission_prompt 回调时
    （await future 等待前端决策），生成器需要能继续推 permission_request 事件。

    实现方式：用后台任务消费引擎事件放入队列，SSE 生成器从队列出队。
    队列空时（引擎可能挂起在权限回调），轮询权限桥推 permission_request 事件。
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def consume_engine():
        """后台任务：消费引擎事件入队。"""
        try:
            async for ev in engine.submitMessage(
                prompt, user_context={}, system_context={}
            ):
                await queue.put(ev)
        except Exception as e:
            await queue.put(e)
        finally:
            # 哨兵，表示引擎结束
            await queue.put(None)

    task = asyncio.create_task(consume_engine())

    try:
        while True:
            try:
                # 短超时轮询，让生成器有机会在引擎挂起时推权限请求
                ev = await asyncio.wait_for(queue.get(), timeout=0.2)
            except asyncio.TimeoutError:
                # 队列空，检查有没有待推送的权限请求
                req = permission_bridge.get_pending_permission_request()
                if req is not None:
                    yield f"data: {json.dumps(req, ensure_ascii=False, default=str)}\n\n"
                continue

            if ev is None:
                # 引擎结束，退出循环
                break

            if isinstance(ev, Exception):
                # 引擎抛异常，推错误事件
                yield f"data: {json.dumps({'type': 'error', 'error': str(ev)}, ensure_ascii=False)}\n\n"
                break

            yield f"data: {json.dumps(serialize_event(ev), ensure_ascii=False, default=str)}\n\n"

            # 每个事件后也检查权限请求
            req = permission_bridge.get_pending_permission_request()
            if req is not None:
                yield f"data: {json.dumps(req, ensure_ascii=False, default=str)}\n\n"
    finally:
        # 客户端断开或引擎结束，清理后台任务
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


@app.post("/api/chat")
async def chat(body: dict) -> StreamingResponse:
    """SSE 流式对话接口。

    请求体：{"prompt": "..."}
    返回：text/event-stream，每行 data: {JSON}\n\n
    """
    prompt = body.get("prompt", "")
    return StreamingResponse(
        chat_event_stream(prompt),
        media_type="text/event-stream",
    )


# ---------------------------------------------------------------------------
# POST /api/command — 斜杠命令
# ---------------------------------------------------------------------------


@app.post("/api/command")
async def run_command(body: dict) -> dict:
    """斜杠命令接口。

    请求体：{"command": "/cost"}
    返回：{"output": "..."}

    简化处理：命令只读不写消息历史，传消息副本避免修改引擎消息。
    """
    command = body.get("command", "")
    parts = command.strip().split(None, 1)
    cmd_name = parts[0].lower().lstrip("/")
    cmd_args = parts[1] if len(parts) > 1 else ""

    cmd = find_command(cmd_name)
    if cmd is None:
        return {"output": f"Unknown command: /{cmd_name}. Type /help for available commands."}

    # 传消息副本，避免命令修改引擎维护的消息历史
    ctx = CommandContext(
        messages=list(engine.mutable_messages),
        app_state=app_state,
        args=cmd_args,
    )
    output = await cmd.handler(ctx)
    return {"output": output}


# ---------------------------------------------------------------------------
# POST /api/permission — 回传权限决策
# ---------------------------------------------------------------------------


@app.post("/api/permission")
async def resolve_permission(body: dict) -> dict:
    """权限决策回传接口。

    请求体：{"request_id": "...", "decision": "allow"/"deny"/"always_allow"}
    返回：{"ok": true} 或 {"ok": false, "error": "request not found"}
    """
    request_id = body.get("request_id", "")
    decision = body.get("decision", "")
    ok = permission_bridge.resolve(request_id, decision)
    if ok:
        return {"ok": True}
    return {"ok": False, "error": "request not found"}
