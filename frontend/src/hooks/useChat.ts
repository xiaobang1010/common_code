import { useState, useCallback, useRef, useEffect } from 'react'
import { useSettingsStore } from '../stores/useSettingsStore'
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
}

// 提问请求（AskUserQuestion 工具）
export interface QuestionRequest {
  request_id: string
  question: string
  options: Array<{ label: string; description: string }>
}

// SSE 事件结构
interface SSEEvent {
  type: string
  event_type?: string
  content?: string
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
function formatDuration(ms: number): string {
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}秒`
  const m = Math.floor(s / 60)
  const rest = s % 60
  return `${m}分${rest}秒`
}

export function useChat() {
  const [blocks, setBlocks] = useState<WorkBlock[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const sessionIdRef = useRef<string | null>(null)
  const [tokenUsage, setTokenUsage] = useState<TokenUsage>({
    input_tokens: 0,
    output_tokens: 0,
    cache_read_input_tokens: 0,
    cache_creation_input_tokens: 0,
    last_prompt_tokens: 0,
    last_cache_creation: 0,
  })
  const [totalCost, setTotalCost] = useState(0)
  const [model, setModel] = useState('')
  const [permissionRequest, setPermissionRequest] = useState<PermissionRequest | null>(null)
  const [questionRequest, setQuestionRequest] = useState<QuestionRequest | null>(null)
  const [permissionMode, setPermissionModeState] = useState<PermissionMode>('default')

  // 当前工作块 id
  const currentBlockId = useRef<string | null>(null)
  // 累积最终回复文本
  const finalReplyRef = useRef<string>('')
  // 暂存后续轮推理，等工具步骤创建时绑定
  const pendingReasoningRef = useRef<string>('')
  // 是否已有工具调用（区分第一轮和后续轮 reasoning）
  const hasToolStartedRef = useRef<boolean>(false)
  // 实时计时
  const [tick, setTick] = useState(0)

  // 从后端拉取汇总状态
  const fetchState = useCallback(async () => {
    try {
      const resp = await fetch('/api/state')
      const data = await resp.json()
      if (data.token_usage) setTokenUsage(data.token_usage)
      if (typeof data.total_cost_usd === 'number') setTotalCost(data.total_cost_usd)
      if (data.model) setModel(data.model)
      if (data.permission_mode === 'default' || data.permission_mode === 'full_access') {
        setPermissionModeState(data.permission_mode)
      }
    } catch {
      // 后端未就绪时静默忽略
    }
  }, [])

  useEffect(() => {
    fetchState()
  }, [fetchState])

  // 订阅设置 store：改了 LLM 配置后刷新 model
  const modelVersion = useSettingsStore((s) => s.modelVersion)
  useEffect(() => {
    if (modelVersion > 0) fetchState()
  }, [modelVersion, fetchState])

  // 工作中时每秒 tick 一次更新耗时显示
  useEffect(() => {
    if (!isStreaming) return
    const timer = setInterval(() => setTick((t) => t + 1), 1000)
    return () => clearInterval(timer)
  }, [isStreaming])

  // 更新当前工作块
  const updateBlock = useCallback((id: string, updater: (b: WorkBlock) => WorkBlock) => {
    setBlocks(prev => prev.map(b => (b.id === id ? updater(b) : b)))
  }, [])

  // 处理单个 SSE 事件
  const handleSSEEvent = useCallback((evt: SSEEvent) => {
    const blockId = currentBlockId.current
    if (!blockId) return

    if (evt.type === 'stream') {
      if (evt.event_type === 'content' && evt.content) {
        // 流式文本累加到 finalReply
        finalReplyRef.current += evt.content
        updateBlock(blockId, b => ({ ...b, finalReply: finalReplyRef.current, finalReplyStreaming: true }))
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
        // 最终回复：覆盖 finalReply
        finalReplyRef.current = msg.content || finalReplyRef.current
        updateBlock(blockId, b => ({ ...b, finalReply: finalReplyRef.current, finalReplyStreaming: false }))
      }
    } else if (evt.type === 'heartbeat') {
      // 心跳，忽略
    } else if (evt.type === 'permission_request') {
      setPermissionRequest({
        request_id: evt.request_id || '',
        tool_name: evt.tool_name || '',
        tool_input: evt.tool_input,
        reason: evt.reason || '',
      })
    } else if (evt.type === 'question_request') {
      setQuestionRequest({
        request_id: evt.request_id || '',
        question: evt.question || '',
        options: evt.options || [],
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
  }, [updateBlock])

  // 解析 SSE 流
  const parseSSEStream = useCallback(
    async (response: Response) => {
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
            handleSSEEvent(evt)
          } catch {
            // 解析失败跳过
          }
        }
      }
    },
    [handleSSEEvent]
  )

  // 创建新工作块
  const createBlock = useCallback((prompt: string): string => {
    const id = genId()
    finalReplyRef.current = ''
    pendingReasoningRef.current = ''
    hasToolStartedRef.current = false
    currentBlockId.current = id
    setBlocks(prev => [
      ...prev,
      {
        id,
        userMessage: prompt,
        steps: [],
        finalReply: '',
        finalReplyStreaming: false,
        status: 'running',
        startTime: Date.now(),
      },
    ])
    return id
  }, [])

  // 发送消息
  const sendMessage = useCallback(
    async (prompt: string) => {
      if (!prompt.trim() || isStreaming) return

      // 斜杠命令走同步接口
      if (prompt.startsWith('/')) {
        setIsStreaming(true)
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
            const chatResp = await fetch('/api/chat', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ prompt: data.skill_prompt, session_id: sessionIdRef.current }),
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
          setIsStreaming(false)
          currentBlockId.current = null
        }
        await fetchState()
        return
      }

      // 普通对话：创建工作块走 SSE 流
      setIsStreaming(true)
      createBlock(prompt)

      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt, session_id: sessionIdRef.current }),
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
        setIsStreaming(false)
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
    },
    [isStreaming, parseSSEStream, fetchState, createBlock, updateBlock]
  )

  // 回传权限决策
  const resolvePermission = useCallback(
    async (decision: 'allow' | 'deny' | 'always_allow') => {
      if (!permissionRequest) return
      const reqId = permissionRequest.request_id
      setPermissionRequest(null)
      try {
        await fetch('/api/permission', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ request_id: reqId, decision }),
        })
      } catch {
        // 忽略
      }
    },
    [permissionRequest]
  )

  // 回传提问回答
  const answerQuestion = useCallback(
    async (answer: string) => {
      if (!questionRequest) return
      const reqId = questionRequest.request_id
      setQuestionRequest(null)
      try {
        await questionApi.answer(reqId, answer)
      } catch {
        // 忽略
      }
    },
    [questionRequest]
  )

  // 停止当前对话
  const abort = useCallback(async () => {
    try {
      await fetch('/api/abort', { method: 'POST' })
    } catch {
      // 忽略
    }
    const blockId = currentBlockId.current
    if (blockId) {
      updateBlock(blockId, b => ({
        ...b,
        status: 'done' as const,
        endTime: Date.now(),
        exitReason: 'aborted',
        finalReplyStreaming: false,
        // 运行中的工具步骤标记为已停止
        steps: b.steps.map(s =>
          s.isRunning ? { ...s, isRunning: false, result: s.result || '已停止' } : s
        ),
      }))
      currentBlockId.current = null
    }
    setIsStreaming(false)
  }, [updateBlock])

  // 切换权限模式
  const setPermissionMode = useCallback(async (mode: PermissionMode) => {
    try {
      await permissionsApi.setMode(mode)
      setPermissionModeState(mode)
    } catch {
      // 忽略
    }
  }, [])

  const setSessionIdWrapper = useCallback((id: string | null) => {
    sessionIdRef.current = id
    setSessionId(id)
  }, [])

  // 清空
  const clearMessages = useCallback(() => {
    setBlocks([])
    currentBlockId.current = null
    finalReplyRef.current = ''
    pendingReasoningRef.current = ''
    hasToolStartedRef.current = false
  }, [])

  // 从后端历史消息恢复：扁平消息列表 -> 工作块数组
  const loadMessages = useCallback((rawMessages: Record<string, unknown>[]) => {
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
    setBlocks(newBlocks)
  }, [])

  return {
    blocks,
    isStreaming,
    sendMessage,
    abort,
    tokenUsage,
    totalCost,
    model,
    permissionRequest,
    resolvePermission,
    questionRequest,
    answerQuestion,
    permissionMode,
    setPermissionMode,
    sessionId,
    setSessionId: setSessionIdWrapper,
    loadMessages,
    clearMessages,
    tick,
    formatDuration,
  }
}
