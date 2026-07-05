import { useState, useCallback, useRef, useEffect } from 'react'
import { useSettingsStore } from '../stores/useSettingsStore'

// 对话消息类型
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  // 推理过程（思维链），和正式回复分开存储
  reasoning?: string
  toolCalls?: Array<{
    id: string
    name: string
    args: unknown
  }>
  isStreaming?: boolean
  // 推理过程是否仍在流式输出中
  isReasoningStreaming?: boolean
  // 工具执行步骤：工具名 + 参数 + 结果，用于可展开的执行过程展示
  toolStep?: {
    toolName: string
    args: string
    result?: string
    isRunning?: boolean
    // 该轮的推理过程（思维链），嵌在工具步骤里展示
    reasoning?: string
  }
}

// token 用量
export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
  // 最近一次请求的 prompt_tokens，反映当前上下文大小
  last_prompt_tokens: number
  // 最近一次请求的 cache_creation_input_tokens，反映已缓存大小
  last_cache_creation: number
}

// 权限请求
export interface PermissionRequest {
  request_id: string
  tool_name: string
  tool_input: unknown
  reason: string
}

// 循环退出结果
export interface LoopResult {
  reason: string
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
  // 工具调用增量字段
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
}

// 自增 id 生成器
let idCounter = 0
const genId = () => `msg-${Date.now()}-${idCounter++}`

export function useChat() {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [isStreaming, setIsStreaming] = useState(false)
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
  const [loopResult, setLoopResult] = useState<LoopResult | null>(null)

  // 当前正在流式输出的 assistant 消息 id
  const currentAssistantId = useRef<string | null>(null)
  // 累积流式文本的缓冲区
  const assistantContentRef = useRef<string>('')
  // 累积第一轮推理过程（思维链）的缓冲区
  const reasoningContentRef = useRef<string>('')
  // 是否已经有工具调用开始过（用于区分第一轮和后续轮 reasoning）
  const hasToolCallStartedRef = useRef<boolean>(false)
  // 暂存后续轮的推理过程，等 tool_call_delta 到来时绑定到工具步骤
  const pendingToolReasoningRef = useRef<string>('')

  // 从后端拉取汇总状态（模型、token、成本）
  const fetchState = useCallback(async () => {
    try {
      const resp = await fetch('/api/state')
      const data = await resp.json()
      if (data.token_usage) {
        setTokenUsage(data.token_usage)
      }
      if (typeof data.total_cost_usd === 'number') {
        setTotalCost(data.total_cost_usd)
      }
      if (data.model) {
        setModel(data.model)
      }
    } catch {
      // 后端未就绪时静默忽略
    }
  }, [])

  // 初始化时拉一次状态
  useEffect(() => {
    fetchState()
  }, [fetchState])

  // 订阅设置 store 的 modelVersion：设置面板改了 LLM 配置/供应商后，自动刷新 model 显示
  const modelVersion = useSettingsStore((s) => s.modelVersion)
  useEffect(() => {
    if (modelVersion > 0) {
      fetchState()
    }
  }, [modelVersion, fetchState])

  // 处理单个 SSE 事件，根据事件类型更新对应状态
  const handleSSEEvent = useCallback((evt: SSEEvent) => {
    if (evt.type === 'stream') {
      if (evt.event_type === 'content' && evt.content) {
        // 把流式文本追加到当前 assistant 消息
        assistantContentRef.current += evt.content
        const id = currentAssistantId.current
        if (id) {
          setMessages(prev =>
            prev.map(m => (m.id === id ? { ...m, content: assistantContentRef.current } : m))
          )
        }
      } else if (evt.event_type === 'reasoning' && evt.content) {
        if (!hasToolCallStartedRef.current) {
          // 第一轮推理：放在 assistant 消息上，实时展示
          reasoningContentRef.current += evt.content
          const id = currentAssistantId.current
          if (id) {
            setMessages(prev =>
              prev.map(m => (m.id === id ? {
                ...m,
                reasoning: reasoningContentRef.current,
                isReasoningStreaming: true,
              } : m))
            )
          }
        } else {
          // 后续轮推理：暂存，等对应工具步骤创建时绑定
          pendingToolReasoningRef.current += evt.content
        }
      } else if (evt.event_type === 'tool_call_delta' && evt.tool_call_name) {
        // 工具调用开始：创建一个"正在执行工具"的步骤消息
        // 只在第一次收到工具名时创建，后续的 arguments 增量忽略（避免消息爆炸）
        const toolName = evt.tool_call_name
        // 后续轮的推理绑定到这个工具步骤
        const toolReasoning = pendingToolReasoningRef.current
        pendingToolReasoningRef.current = ''
        hasToolCallStartedRef.current = true
        // 重置第一轮推理 buffer，为下一轮准备
        reasoningContentRef.current = ''
        setMessages(prev => {
          // 已有同名工具步骤且在运行中，不重复创建
          const last = prev[prev.length - 1]
          if (last?.toolStep?.isRunning && last.toolStep.toolName === toolName) {
            return prev
          }
          return [
            ...prev,
            {
              id: genId(),
              role: 'tool' as const,
              content: '',
              toolStep: {
                toolName,
                args: evt.tool_call_arguments || '',
                isRunning: true,
                reasoning: toolReasoning || undefined,
              },
            },
          ]
        })
      } else if (evt.event_type === 'usage' && evt.usage) {
        // 不在前端累加——后端 AppState 已正确累加，对话结束后 fetchState 会拉准确值
        // 前端累加会导致和后端值不一致（cache 字段没加、重复计算等）
      } else if (evt.event_type === 'error') {
        setMessages(prev => [
          ...prev,
          { id: genId(), role: 'system', content: `错误: ${evt.error || '未知错误'}` },
        ])
      } else if (evt.event_type === 'done') {
        // 流结束但不清空 currentAssistantId——后面还有 message(assistant) 和 tool 结果事件
        // 只有 loop_result(completed) 才是真正结束
        const id = currentAssistantId.current
        if (id) {
          setMessages(prev => prev.map(m => (m.id === id ? { ...m, isStreaming: false, isReasoningStreaming: false } : m)))
        }
      }
    } else if (evt.type === 'message' && evt.message) {
      const msg = evt.message
      if (msg.role === 'tool') {
        // 工具执行结果：填到最近的运行中工具步骤里，标记为完成
        const toolResult = (msg.content || '').slice(0, 500)
        setMessages(prev => {
          // 从后往前找第一个运行中的工具步骤
          const idx = [...prev].reverse().findIndex(m => m.toolStep?.isRunning)
          if (idx === -1) {
            // 没找到运行中的步骤，兜底新建一条工具消息
            return [...prev, { id: genId(), role: 'tool', content: toolResult }]
          }
          const realIdx = prev.length - 1 - idx
          const updated = [...prev]
          updated[realIdx] = {
            ...updated[realIdx],
            content: toolResult,
            toolStep: { ...updated[realIdx].toolStep!, result: toolResult, isRunning: false },
          }
          return updated
        })
      } else if (msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0) {
        // 含工具调用的 assistant 消息：更新当前流式消息，添加工具调用摘要
        setMessages(prev => {
          const id = currentAssistantId.current
          if (id) {
            // 更新已有流式消息，补充工具调用信息
            return prev.map(m =>
              m.id === id
                ? {
                    ...m,
                    content: msg.content || m.content,
                    isStreaming: false,
                    isReasoningStreaming: false,
                    toolCalls: msg.tool_calls!.map(tc => ({
                      id: tc.id,
                      name: tc.function.name,
                      args: tc.function.arguments,
                    })),
                  }
                : m
            )
          }
          // 没有流式消息（多轮工具调用场景），新建一条
          return [
            ...prev,
            {
              id: genId(),
              role: 'assistant',
              content: msg.content || '',
              toolCalls: msg.tool_calls!.map(tc => ({
                id: tc.id,
                name: tc.function.name,
                args: tc.function.arguments,
              })),
            },
          ]
        })
      } else if (msg.role === 'assistant') {
        // 纯文本 assistant 消息：后端在流式结束后会 yield 完整 assistant 消息
        // 用它来确保内容完整（防止流式累积丢失或竞态清空）
        setMessages(prev => {
          const lastAssistant = [...prev].reverse().find(m => m.role === 'assistant')
          if (lastAssistant) {
            // 有流式或刚结束的 assistant 消息，用完整内容更新它
            const newContent = msg.content || lastAssistant.content
            return prev.map(m =>
              m.id === lastAssistant.id
                ? { ...m, content: newContent, isStreaming: false, isReasoningStreaming: false }
                : m
            )
          }
          // 没有任何 assistant 消息时直接添加
          return [
            ...prev,
            { id: genId(), role: 'assistant', content: msg.content || '' },
          ]
        })
      }
    } else if (evt.type === 'heartbeat') {
      // 后端心跳，不做任何事——知道连接还活着就行
    } else if (evt.type === 'permission_request') {
      // 弹出权限确认
      setPermissionRequest({
        request_id: evt.request_id || '',
        tool_name: evt.tool_name || '',
        tool_input: evt.tool_input,
        reason: evt.reason || '',
      })
    } else if (evt.type === 'loop_result') {
      // 循环真正结束，清空状态
      currentAssistantId.current = null
      assistantContentRef.current = ''
      reasoningContentRef.current = ''
      hasToolCallStartedRef.current = false
      pendingToolReasoningRef.current = ''
      setLoopResult({ reason: evt.reason || '' })
    }
  }, [])

  // 解析 SSE 流：用 ReadableStream 逐块读取，按空行分割事件
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

        // 按双换行分割出完整事件
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
            // 解析失败的行直接跳过
          }
        }
      }
    },
    [handleSSEEvent]
  )

  // 发送消息：斜杠命令走 /api/command，普通消息走 /api/chat 的 SSE 流
  const sendMessage = useCallback(
    async (prompt: string) => {
      if (!prompt.trim() || isStreaming) return

      // 先把用户消息加到列表
      setMessages(prev => [...prev, { id: genId(), role: 'user', content: prompt }])

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

          // skill 触发：把 skill 正文作为 prompt 走 /api/chat SSE 流
          if (data.is_skill) {
            // 显示 skill 激活提示
            setMessages(prev => [
              ...prev,
              { id: genId(), role: 'system', content: `Launching skill: ${data.skill_name}` },
            ])
            // 预创建 assistant 占位消息
            const assistantId = genId()
            currentAssistantId.current = assistantId
            assistantContentRef.current = ''
            reasoningContentRef.current = ''
            hasToolCallStartedRef.current = false
            pendingToolReasoningRef.current = ''
            setMessages(prev => [
              ...prev,
              { id: assistantId, role: 'assistant', content: '', isStreaming: true },
            ])
            // skill 正文作为 prompt 发到 /api/chat
            const chatResp = await fetch('/api/chat', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ prompt: data.skill_prompt }),
            })
            if (!chatResp.ok) throw new Error(`HTTP ${chatResp.status}`)
            await parseSSEStream(chatResp)
          } else {
            // 普通命令：展示输出
            setMessages(prev => [
              ...prev,
              { id: genId(), role: 'system', content: data.output || '' },
            ])
          }
        } catch (e) {
          setMessages(prev => [
            ...prev,
            {
              id: genId(),
              role: 'system',
              content: `命令执行失败: ${e instanceof Error ? e.message : String(e)}`,
            },
          ])
        } finally {
          setIsStreaming(false)
          currentAssistantId.current = null
        }
        await fetchState()
        return
      }

      // 普通对话走 SSE 流
      setIsStreaming(true)
      // 预先创建一条空的 assistant 消息占位，后续流式内容追加到它上面
      const assistantId = genId()
      currentAssistantId.current = assistantId
      assistantContentRef.current = ''
      reasoningContentRef.current = ''
      hasToolCallStartedRef.current = false
      pendingToolReasoningRef.current = ''
      setMessages(prev => [
        ...prev,
        { id: assistantId, role: 'assistant', content: '', isStreaming: true },
      ])

      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt }),
        })
        if (!resp.ok) {
          throw new Error(`HTTP ${resp.status}`)
        }
        await parseSSEStream(resp)
      } catch (e) {
        setMessages(prev => [
          ...prev,
          {
            id: genId(),
            role: 'system',
            content: `请求失败: ${e instanceof Error ? e.message : String(e)}`,
          },
        ])
      } finally {
        setIsStreaming(false)
        currentAssistantId.current = null
      }
      // 流结束后刷新汇总状态
      await fetchState()
    },
    [isStreaming, parseSSEStream, fetchState]
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
      } catch (e) {
        setMessages(prev => [
          ...prev,
          {
            id: genId(),
            role: 'system',
            content: `权限回传失败: ${e instanceof Error ? e.message : String(e)}`,
          },
        ])
      }
    },
    [permissionRequest]
  )

  // 停止当前对话：调后端 abort 接口取消任务，并立即恢复输入状态
  const abort = useCallback(async () => {
    try {
      await fetch('/api/abort', { method: 'POST' })
    } catch {
      // 忽略网络错误，反正前端也要强制恢复
    }
    // 强制恢复输入状态，并标记当前流式消息为已完成
    const id = currentAssistantId.current
    if (id) {
      setMessages(prev => prev.map(m => (m.id === id ? { ...m, isStreaming: false } : m)))
    }
    // 把运行中的工具步骤标记为已停止
    setMessages(prev => prev.map(m =>
      m.toolStep?.isRunning
        ? { ...m, toolStep: { ...m.toolStep, isRunning: false, result: m.toolStep.result || '已停止' } }
        : m
    ))
    currentAssistantId.current = null
    assistantContentRef.current = ''
    reasoningContentRef.current = ''
    hasToolCallStartedRef.current = false
    pendingToolReasoningRef.current = ''
    setIsStreaming(false)
  }, [])

  return {
    messages,
    isStreaming,
    sendMessage,
    abort,
    tokenUsage,
    totalCost,
    model,
    permissionRequest,
    resolvePermission,
    loopResult,
  }
}
