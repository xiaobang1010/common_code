// 聊天工作区（AI 面板）的全局状态 store
// 状态规范化：blockIds + blocksById，组件按 selector 局部订阅，
// 流式更新只触发当前工作块重渲，不再带动整棵 App 树
// 逻辑迁移自 hooks/useChat.ts

import { create } from 'zustand'
import { permissionsApi, questionApi, type PermissionMode } from '../api/client'

// 工作块中的中间步骤：工具调用
export interface WorkStep {
  id: string
  type: 'tool'
  toolName: string
  args: string
  result?: string
  isRunning?: boolean
  // 该轮的推理过程（思维链）
  reasoning?: string
}

// 工作块：一次 agentic 循环的中间过程聚合
export interface WorkBlock {
  id: string
  // 用户输入
  userMessage: string
  // 中间过程：工具步骤、思维链，按真实时序排列
  steps: WorkStep[]
  // 最终回复（独立显示在工作块外，循环结束时才有）
  finalReply: string
  finalReplyStreaming: boolean
  // 状态：工作中 / 已工作（含正常结束和中断）
  status: 'running' | 'done'
  startTime: number
  endTime?: number
  // 退出原因：completed / model_error / prompt_too_long / aborted 等
  exitReason?: string
  // 最近一次后端阶段事件（如 memory_ready / model_requested），
  // 卡片文案按优先级推导时使用；首 token、工具开始的文案由渲染时推导，不写这里
  phase?: string
}

// token 用量
export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
  last_prompt_tokens: number
  last_cache_creation: number
}

// 权限请求
export interface PermissionRequest {
  request_id: string
  tool_name: string
  tool_input: unknown
  reason: string
  // 来源会话 id（后台任务跨会话弹窗标注用）
  session_id?: string
}

// 提问请求（AskUserQuestion 工具）
export interface QuestionRequest {
  request_id: string
  question: string
  options: Array<{ label: string; description: string }>
  // 来源会话 id（后台任务跨会话弹窗标注用）
  session_id?: string
}

// SSE 事件结构
interface SSEEvent {
  type: string
  event_type?: string
  content?: string
  // 后端自动建会话回传（session_meta 事件）
  session_id?: string
  usage?: {
    prompt_tokens: number
    completion_tokens: number
  }
  error?: string
  finish_reason?: string
  tool_call_id?: string
  tool_call_name?: string
  tool_call_arguments?: string
  message?: {
    role: string
    content?: string
    tool_calls?: Array<{
      id: string
      function: { name: string; arguments: string }
    }>
    tool_call_id?: string
  }
  request_id?: string
  tool_name?: string
  tool_input?: unknown
  reason?: string
  // 提问请求字段
  question?: string
  options?: Array<{ label: string; description: string }>
}

// 自增 id 生成器
let idCounter = 0
const genId = () => `msg-${Date.now()}-${idCounter++}`

// 格式化耗时为 "X分Y秒" 或 "Y秒"
export function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}秒`
  const m = Math.floor(s / 60)
  const rest = s % 60
  return `${m}分${rest}秒`
}

// 流式过程使用的模块级 ref（store 是单例，跨 action 调用保持）
const sessionIdRef = { current: null as string | null }
// 当前 SSE 连接的中止控制器：切换会话时仅断开连接（不取消后台任务）
const sseAbortRef = { current: null as AbortController | null }
const currentBlockId = { current: null as string | null }
const finalReplyRef = { current: '' }
const pendingReasoningRef = { current: '' }
const hasToolStartedRef = { current: false }
// 流式 content 节流缓冲：事件只累积，由定时器统一 flush
const pendingContentRef = { current: '' }
let flushTimer: number | null = null

// 最近一次 SSE 活动时间：任意事件（含 heartbeat）都刷新，仅更新 ref 不触发重渲。
// 供工作卡片占位形态判断「后端还活着」；UI 文案由组件自己的 1s tick 驱动，
// 不在每个 heartbeat 上 setState，避免 0.2s 一次的高频重渲
export const lastActivityAtRef = { current: Date.now() }

interface ChatState {
  // 规范化工作块：id 列表 + id 索引（未变 block 对象引用稳定，局部订阅才能生效）
  blockIds: string[]
  blocksById: Record<string, WorkBlock>
  isStreaming: boolean
  sessionId: string | null
  tokenUsage: TokenUsage
  totalCost: number
  model: string
  permissionRequest: PermissionRequest | null
  questionRequest: QuestionRequest | null
  permissionMode: PermissionMode

  sendMessage: (prompt: string) => Promise<void>
  abort: () => Promise<void>
  loadMessages: (rawMessages: Record<string, unknown>[]) => void
  clearMessages: () => void
  resolvePermission: (decision: 'allow' | 'deny' | 'always_allow') => Promise<void>
  answerQuestion: (answer: string) => Promise<void>
  setPermissionMode: (mode: PermissionMode) => Promise<void>
  setSessionId: (id: string | null) => void
  // 断开当前 SSE 连接（不取消后台任务），恢复可发送状态
  disconnectStream: () => void
  fetchState: () => Promise<void>
}

export const useChatStore = create<ChatState>((set, get) => {
  // 更新单个工作块：只替换目标对象，其余 block 引用保持不变。
  // 容错：运行中切换会话后 blocksById 被历史替换，旧流的迟到事件会打到不存在的块，
  // 此时跳过更新，避免 updater 读 undefined 字段抛错或塞进脏块
  const updateBlock = (id: string, updater: (b: WorkBlock) => WorkBlock) => {
    set(state => {
      if (!state.blocksById[id]) return {}
      return {
        blocksById: {
          ...state.blocksById,
          [id]: updater(state.blocksById[id]),
        },
      }
    })
  }

  // 立即 flush 缓冲中的流式文本：拼到 finalReply 并更新工作块。
  // 快照当前值，避免批处理时闭包读到被后续事件改动的 ref
  const flushContent = () => {
    if (flushTimer != null) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
    const pending = pendingContentRef.current
    if (!pending) return
    pendingContentRef.current = ''
    const blockId = currentBlockId.current
    if (!blockId) return
    finalReplyRef.current += pending
    const reply = finalReplyRef.current
    updateBlock(blockId, b => ({ ...b, finalReply: reply, finalReplyStreaming: true }))
  }

  // 节流入口：content 事件只累积到缓冲，约 80ms 或超过 160 字符才 flush 一次
  const onContent = (content: string) => {
    pendingContentRef.current += content
    if (flushTimer == null) {
      flushTimer = window.setTimeout(() => {
        flushTimer = null
        flushContent()
      }, 80)
    }
    // 累积较多时立即 flush，避免单次延迟过大
    if (pendingContentRef.current.length > 160) {
      flushContent()
    }
  }

  // 处理单个 SSE 事件
  const handleSSEEvent = (evt: SSEEvent) => {
    // 会话元信息（后端自动建会话回传）：更新会话 ID，不依赖 block 存在
    if (evt.type === 'session_meta' && evt.session_id) {
      setSessionId(evt.session_id)
      return
    }
    const blockId = currentBlockId.current
    if (!blockId) return

    // 非纯文本事件（完成/工具调用/错误等）到来前先 flush，避免缓冲内容滞留丢失
    if (!(evt.type === 'stream' && evt.event_type === 'content')) {
      flushContent()
    }

    if (evt.type === 'stream') {
      if (evt.event_type === 'content' && evt.content) {
        // 累积到缓冲，由节流器统一 flush
        onContent(evt.content)
      } else if (evt.event_type === 'reasoning' && evt.content) {
        // 推理过程暂存，等工具步骤创建时绑定
        pendingReasoningRef.current += evt.content
      } else if (evt.event_type === 'tool_call_delta' && evt.tool_call_name) {
        const toolName = evt.tool_call_name
        const reasoning = pendingReasoningRef.current
        pendingReasoningRef.current = ''
        hasToolStartedRef.current = true
        updateBlock(blockId, b => {
          const last = b.steps[b.steps.length - 1]
          if (last && last.type === 'tool' && last.isRunning && last.toolName === toolName) {
            return b
          }
          return {
            ...b,
            steps: [
              ...b.steps,
              { id: genId(), type: 'tool', toolName, args: evt.tool_call_arguments || '', isRunning: true, reasoning: reasoning || undefined },
            ],
          }
        })
      } else if (evt.event_type === 'phase' && evt.content) {
        // 后端阶段事件：只存最近一次，文案由 WorkBlock 按优先级推导
        updateBlock(blockId, b => ({ ...b, phase: evt.content }))
      } else if (evt.event_type === 'error') {
        updateBlock(blockId, b => ({
          ...b,
          steps: [...b.steps, { id: genId(), type: 'tool', toolName: 'error', args: '', result: `错误: ${evt.error || '未知错误'}` }],
        }))
      } else if (evt.event_type === 'done') {
        // done 什么都不做，不清空 finalReply
        updateBlock(blockId, b => ({ ...b, finalReplyStreaming: false }))
      }
    } else if (evt.type === 'message' && evt.message) {
      const msg = evt.message
      if (msg.role === 'tool') {
        // 工具结果：填到最近的运行中步骤
        const toolResult = (msg.content || '').slice(0, 500)
        updateBlock(blockId, b => {
          const idx = [...b.steps].reverse().findIndex(s => s.type === 'tool' && s.isRunning)
          if (idx === -1) return b
          const realIdx = b.steps.length - 1 - idx
          const updated = [...b.steps]
          updated[realIdx] = { ...updated[realIdx], result: toolResult, isRunning: false }
          return { ...b, steps: updated }
        })
      } else if (msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0) {
        // 中间轮（含工具调用）：清空 finalReply，下一轮 content 自然填充
        finalReplyRef.current = ''
        updateBlock(blockId, b => ({ ...b, finalReply: '', finalReplyStreaming: false }))
      } else if (msg.role === 'assistant') {
        // 最终回复：覆盖 finalReply。
        // 注意：message/done/loop_result 常在同一网络分片里连续到达，更新会被 React 批量排队；
        // loop_result 处理时会立刻清空 finalReplyRef，若 updater 闭包读 ref 会拿到被清空的值，
        // 导致最终回复在完成时消失。因此必须先快照成局部变量再传给 updater。
        finalReplyRef.current = msg.content || finalReplyRef.current
        const reply = finalReplyRef.current
        updateBlock(blockId, b => ({ ...b, finalReply: reply, finalReplyStreaming: false }))
      }
    } else if (evt.type === 'heartbeat') {
      // 心跳，忽略
    } else if (evt.type === 'permission_request') {
      set({
        permissionRequest: {
          request_id: evt.request_id || '',
          tool_name: evt.tool_name || '',
          tool_input: evt.tool_input,
          reason: evt.reason || '',
          session_id: evt.session_id,
        },
      })
    } else if (evt.type === 'question_request') {
      set({
        questionRequest: {
          request_id: evt.request_id || '',
          question: evt.question || '',
          options: evt.options || [],
          session_id: evt.session_id,
        },
      })
    } else if (evt.type === 'error') {
      // 引擎级错误：标记工作块为 done，保留已收到的 finalReply
      updateBlock(blockId, b => ({
        ...b,
        status: 'done' as const,
        endTime: Date.now(),
        exitReason: 'error',
        finalReplyStreaming: false,
        steps: b.finalReply
          ? b.steps
          : [...b.steps, { id: genId(), type: 'tool', toolName: 'error', args: '', result: `错误: ${evt.error || '未知错误'}` }],
      }))
    } else if (evt.type === 'loop_result') {
      // 循环结束：标记工作块为已工作
      updateBlock(blockId, b => ({
        ...b,
        status: 'done' as const,
        endTime: Date.now(),
        exitReason: evt.reason || '',
        finalReplyStreaming: false,
      }))
      currentBlockId.current = null
      finalReplyRef.current = ''
      pendingReasoningRef.current = ''
      hasToolStartedRef.current = false
    }
  }

  // 解析 SSE 流
  const parseSSEStream = async (response: Response) => {
    if (!response.body) return
    const reader = response.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const parts = buffer.split('\n\n')
      buffer = parts.pop() || ''
      for (const part of parts) {
        const line = part.trim()
        if (!line.startsWith('data:')) continue
        const dataStr = line.slice(5).trim()
        if (!dataStr) continue
        try {
          const evt: SSEEvent = JSON.parse(dataStr)
          // 任何事件（含 heartbeat）都算连接活动：只刷新 ref，不触发重渲
          lastActivityAtRef.current = Date.now()
          handleSSEEvent(evt)
        } catch {
          // 解析失败跳过
        }
      }
    }
  }

  // 创建新工作块
  const createBlock = (prompt: string): string => {
    const id = genId()
    finalReplyRef.current = ''
    pendingReasoningRef.current = ''
    hasToolStartedRef.current = false
    currentBlockId.current = id
    // 发送即视为活动起点：连接建立前的等待也从这里起算
    lastActivityAtRef.current = Date.now()
    set(state => ({
      blockIds: [...state.blockIds, id],
      blocksById: {
        ...state.blocksById,
        [id]: {
          id,
          userMessage: prompt,
          steps: [],
          finalReply: '',
          finalReplyStreaming: false,
          status: 'running' as const,
          startTime: Date.now(),
        },
      },
    }))
    return id
  }

  // 从后端拉取汇总状态
  const fetchState = async () => {
    try {
      const resp = await fetch('/api/state')
      const data = await resp.json()
      if (data.token_usage) set({ tokenUsage: data.token_usage })
      if (typeof data.total_cost_usd === 'number') set({ totalCost: data.total_cost_usd })
      if (data.model) set({ model: data.model })
      if (data.permission_mode === 'default' || data.permission_mode === 'full_access') {
        set({ permissionMode: data.permission_mode })
      }
    } catch {
      // 后端未就绪时静默忽略
    }
  }

  // 发送消息
  const sendMessage = async (prompt: string) => {
    if (!prompt.trim() || get().isStreaming) return

    // 斜杠命令走同步接口
    if (prompt.startsWith('/')) {
      set({ isStreaming: true })
      try {
        const resp = await fetch('/api/command', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ command: prompt }),
        })
        const data = await resp.json()
        if (data.is_skill) {
          // skill 触发：创建工作块，skill 正文作为 prompt 发到 /api/chat
          const blockId = createBlock(`Launching skill: ${data.skill_name}`)
          updateBlock(blockId, b => ({ ...b, userMessage: prompt }))
          sseAbortRef.current = new AbortController()
          const chatResp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ prompt: data.skill_prompt, session_id: sessionIdRef.current }),
            signal: sseAbortRef.current.signal,
          })
          if (!chatResp.ok) throw new Error(`HTTP ${chatResp.status}`)
          await parseSSEStream(chatResp)
        } else {
          // 普通命令：作为系统消息追加到最后一个工作块或新建
          const blockId = createBlock(prompt)
          updateBlock(blockId, b => ({
            ...b,
            status: 'done' as const,
            endTime: Date.now(),
            exitReason: 'command',
            finalReply: data.output || '',
          }))
          currentBlockId.current = null
        }
      } catch (e) {
        const blockId = currentBlockId.current
        if (blockId) {
          updateBlock(blockId, b => ({
            ...b,
            status: 'done' as const,
            endTime: Date.now(),
            exitReason: 'error',
            finalReply: `命令执行失败: ${e instanceof Error ? e.message : String(e)}`,
          }))
        }
      } finally {
        set({ isStreaming: false })
        currentBlockId.current = null
      }
      await fetchState()
      return
    }

    // 普通对话：创建工作块走 SSE 流
    set({ isStreaming: true })
    createBlock(prompt)

    try {
      sseAbortRef.current = new AbortController()
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt, session_id: sessionIdRef.current }),
        signal: sseAbortRef.current.signal,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      await parseSSEStream(resp)
    } catch (e) {
      const blockId = currentBlockId.current
      if (blockId) {
        updateBlock(blockId, b => ({
          ...b,
          status: 'done' as const,
          endTime: Date.now(),
          exitReason: 'error',
          finalReply: `请求失败: ${e instanceof Error ? e.message : String(e)}`,
        }))
      }
    } finally {
      set({ isStreaming: false })
      // 兜底 flush：异常断开时把缓冲内容落盘，避免尾部文本丢失
      flushContent()
      // 如果没有收到 loop_result（异常断开），强制标记为 done
      const blockId = currentBlockId.current
      if (blockId) {
        updateBlock(blockId, b => ({
          ...b,
          status: 'done' as const,
          endTime: Date.now(),
          exitReason: b.exitReason || 'aborted',
          finalReplyStreaming: false,
        }))
        currentBlockId.current = null
      }
    }
    await fetchState()
  }

  // 回传权限决策
  const resolvePermission = async (decision: 'allow' | 'deny' | 'always_allow') => {
    const req = get().permissionRequest
    if (!req) return
    const reqId = req.request_id
    set({ permissionRequest: null })
    try {
      await fetch('/api/permission', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ request_id: reqId, decision }),
      })
    } catch {
      // 忽略
    }
  }

  // 回传提问回答
  const answerQuestion = async (answer: string) => {
    const req = get().questionRequest
    if (!req) return
    const reqId = req.request_id
    set({ questionRequest: null })
    try {
      await questionApi.answer(reqId, answer)
    } catch {
      // 忽略
    }
  }

  // 停止当前对话
  const abort = async () => {
    try {
      await fetch('/api/abort', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // 停止作用于当前查看会话的任务（后端缺省也按查看会话处理）
        body: JSON.stringify({ session_id: sessionIdRef.current }),
      })
    } catch {
      // 忽略
    }
    const blockId = currentBlockId.current
    if (blockId) {
      // 先 flush 缓冲，避免已接收但未显示的文本丢失
      flushContent()
      updateBlock(blockId, b => ({
        ...b,
        status: 'done' as const,
        endTime: Date.now(),
        exitReason: 'aborted',
        finalReplyStreaming: false,
        // 占位阶段中止时既无步骤也无文本，兜底显示「已停止」，不留转圈残留
        finalReply: b.finalReply || (b.steps.length === 0 ? '已停止' : ''),
        // 运行中的工具步骤标记为已停止
        steps: b.steps.map(s =>
          s.isRunning ? { ...s, isRunning: false, result: s.result || '已停止' } : s
        ),
      }))
      currentBlockId.current = null
    }
    set({ isStreaming: false })
  }

  // 切换权限模式
  const setPermissionMode = async (mode: PermissionMode) => {
    try {
      await permissionsApi.setMode(mode)
      set({ permissionMode: mode })
    } catch {
      // 忽略
    }
  }

  const setSessionId = (id: string | null) => {
    sessionIdRef.current = id
    set({ sessionId: id })
  }

  // 断开当前 SSE 连接（切换会话/工作区时调用）：仅断连接不取消后台任务，
  // 任务在服务端继续跑；同时清空本地流式状态，让其他会话可以发消息
  const disconnectStream = () => {
    sseAbortRef.current?.abort()
    sseAbortRef.current = null
    currentBlockId.current = null
    flushContent()
    set({ isStreaming: false })
  }

  // 清空
  const clearMessages = () => {
    set({ blockIds: [], blocksById: {} })
    currentBlockId.current = null
    finalReplyRef.current = ''
    pendingReasoningRef.current = ''
    hasToolStartedRef.current = false
    pendingContentRef.current = ''
    if (flushTimer != null) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
  }

  // 从后端历史消息恢复：扁平消息列表 -> 工作块数组
  const loadMessages = (rawMessages: Record<string, unknown>[]) => {
    const newBlocks: WorkBlock[] = []
    let currentBlock: WorkBlock | null = null

    for (const raw of rawMessages) {
      const role = raw.role as string
      const content = (raw.content as string) || ''

      if (role === 'user') {
        // 新工作块
        if (currentBlock) newBlocks.push(currentBlock)
        currentBlock = {
          id: genId(),
          userMessage: content,
          steps: [],
          finalReply: '',
          finalReplyStreaming: false,
          status: 'done',
          startTime: Date.now(),
          endTime: Date.now(),
          exitReason: 'completed',
        }
      } else if (role === 'assistant') {
        const toolCalls = raw.tool_calls as Array<{
          id: string
          function: { name: string; arguments: string }
        }> | undefined
        if (toolCalls && toolCalls.length > 0) {
          // 工具调用 assistant 消息：作为中间步骤
          if (currentBlock) {
            for (const tc of toolCalls) {
              currentBlock.steps.push({
                id: genId(),
                type: 'tool',
                toolName: tc.function.name,
                args: tc.function.arguments,
                isRunning: false,
              })
            }
          }
        } else if (currentBlock) {
          // 纯文本：作为最终回复
          currentBlock.finalReply = content
        }
      } else if (role === 'tool') {
        // 工具结果：填到最近的未完成工具步骤
        if (currentBlock) {
          const lastTool = [...currentBlock.steps].reverse().find(s => s.type === 'tool' && !s.result)
          if (lastTool) {
            lastTool.result = content.slice(0, 500)
          }
        }
      }
    }
    if (currentBlock) newBlocks.push(currentBlock)

    const blockIds = newBlocks.map(b => b.id)
    const blocksById: Record<string, WorkBlock> = {}
    for (const b of newBlocks) blocksById[b.id] = b
    set({ blockIds, blocksById })
  }

  return {
    blockIds: [],
    blocksById: {},
    isStreaming: false,
    sessionId: null,
    tokenUsage: {
      input_tokens: 0,
      output_tokens: 0,
      cache_read_input_tokens: 0,
      cache_creation_input_tokens: 0,
      last_prompt_tokens: 0,
      last_cache_creation: 0,
    },
    totalCost: 0,
    model: '',
    permissionRequest: null,
    questionRequest: null,
    permissionMode: 'default',
    sendMessage,
    abort,
    loadMessages,
    clearMessages,
    resolvePermission,
    answerQuestion,
    setPermissionMode,
    setSessionId,
    disconnectStream,
    fetchState,
  }
})
