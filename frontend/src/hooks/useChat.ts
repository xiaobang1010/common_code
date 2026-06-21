import { useState, useCallback, useRef, useEffect } from 'react'

// 对话消息类型
export interface ChatMessage {
  id: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  toolCalls?: Array<{
    id: string
    name: string
    args: unknown
  }>
  isStreaming?: boolean
}

// token 用量
export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
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
  })
  const [totalCost, setTotalCost] = useState(0)
  const [model, setModel] = useState('')
  const [permissionRequest, setPermissionRequest] = useState<PermissionRequest | null>(null)
  const [loopResult, setLoopResult] = useState<LoopResult | null>(null)

  // 当前正在流式输出的 assistant 消息 id
  const currentAssistantId = useRef<string | null>(null)
  // 累积流式文本的缓冲区
  const assistantContentRef = useRef<string>('')

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
      } else if (evt.event_type === 'usage' && evt.usage) {
        // 累加 token 用量
        setTokenUsage(prev => ({
          ...prev,
          input_tokens: prev.input_tokens + (evt.usage?.prompt_tokens || 0),
          output_tokens: prev.output_tokens + (evt.usage?.completion_tokens || 0),
        }))
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
          setMessages(prev => prev.map(m => (m.id === id ? { ...m, isStreaming: false } : m)))
        }
      }
    } else if (evt.type === 'message' && evt.message) {
      const msg = evt.message
      if (msg.role === 'tool') {
        // 工具执行结果
        setMessages(prev => [
          ...prev,
          { id: genId(), role: 'tool', content: (msg.content || '').slice(0, 500) },
        ])
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
                ? { ...m, content: newContent, isStreaming: false }
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
          setMessages(prev => [
            ...prev,
            { id: genId(), role: 'system', content: data.output || '' },
          ])
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

  return {
    messages,
    isStreaming,
    sendMessage,
    tokenUsage,
    totalCost,
    model,
    permissionRequest,
    resolvePermission,
    loopResult,
  }
}
