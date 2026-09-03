// 聊天工作区（AI 面板）的全局状态 store
// 状态规范化：blockIds + blocksById，组件按 selector 局部订阅，
// 流式更新只触发当前工作块重渲，不再带动整棵 App 树
// 轨迹模型：一次 agentic 循环的过程与回复统一为按真实时序入列的时间线
// （text/reasoning/tool 三类一等事件交错排布），最终回复即最后一个 text 项
// 逻辑迁移自 hooks/useChat.ts

import { create } from 'zustand'
import { permissionsApi, questionApi, type PermissionMode } from '../api/client'
import { parseUserMessage } from '../utils/skillParse'

// 时间线事件：任务轨迹的三类一等事件，按 SSE 到达的真实时序入列
export interface TimelineItem {
  id: string
  // text = 正文（过渡叙述与最终回复）；reasoning = 思维链；tool = 工具调用
  type: 'text' | 'reasoning' | 'tool'
  // text 正文 / reasoning 思维链全文
  content?: string
  // text/reasoning 是否仍在流式追加（open）；类型切换或回合结束时关闭
  open?: boolean
  // ---- 以下仅 tool 项 ----
  toolName?: string
  args?: string
  result?: string
  isRunning?: boolean
  // ---- reasoning/tool 计时（ms，事件到达时刻），供「思考 · X秒」显示 ----
  startTime?: number
  endTime?: number
}

// 工作块：一次 agentic 循环的轨迹聚合
export interface WorkBlock {
  id: string
  // 用户输入
  userMessage: string
  // 轨迹时间线：正文、思考、工具调用按真实时序交错排列
  timeline: TimelineItem[]
  // 状态：工作中 / 已工作（含正常结束和中断）
  status: 'running' | 'done'
  startTime: number
  endTime?: number
  // 退出原因：completed / model_error / prompt_too_long / aborted 等
  exitReason?: string
  // 最近一次后端阶段事件（如 memory_ready / model_requested），
  // 卡片文案按优先级推导时使用；首 token、工具开始的文案由渲染时推导，不写这里
  phase?: string
  // 斜杠技能触发来源（如 "spec"）：用户气泡显示技能徽章而非纯文本输入
  skillName?: string
}

// token 用量
export interface TokenUsage {
  input_tokens: number
  output_tokens: number
  cache_read_input_tokens: number
  cache_creation_input_tokens: number
  // 累计实际发送的输入 token 总量（缓存命中率分母，协议无关口径）
  total_input_tokens: number
  last_prompt_tokens: number
  last_cache_creation: number
}

// 上下文分类 token 估算：{分类名: token 数, total: 总数}，
// 分类名为 system_tools / mcp_tools / skills / system_prompt / messages / other，
// 由后端 context_metrics 生成，占比为 0 的分类不出现
export type ContextBreakdown = Record<string, number>

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
  // 上下文分类估算（event_type === 'context_breakdown' 时）
  breakdown?: Record<string, number>
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
  const s = Math.floor(Math.max(0, ms) / 1000)
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
// 流式过程缓冲：正文与思考两类增量分别累积，由定时器统一 flush 入列；
// 同一时刻只可能有一种类型在流出（模型先思考后正文），flush 按思考→正文顺序
const pendingRef = { current: { text: '', reasoning: '' } }
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
  // 最近一次请求的上下文分类估算（null 表示尚未收到数据）
  contextBreakdown: ContextBreakdown | null
  totalCost: number
  model: string
  permissionRequest: PermissionRequest | null
  questionRequest: QuestionRequest | null
  permissionMode: PermissionMode

  sendMessage: (prompt: string) => Promise<boolean>
  // 编辑历史用户消息并从该处重发：截断后续块与 DB 历史，用新文本重建该轮
  editAndResend: (blockId: string, newText: string) => Promise<boolean>
  abort: () => Promise<void>
  loadMessages: (rawMessages: Record<string, unknown>[], opts?: { runningStartedAt?: number }) => void
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

  // 关闭块末尾仍 open 的 text/reasoning 项（补 endTime）。不变则原样返回，
  // 避免无谓的新对象引用触发重渲
  const closeOpenIn = (b: WorkBlock): WorkBlock => {
    const last = b.timeline[b.timeline.length - 1]
    if (!last || !last.open) return b
    return {
      ...b,
      timeline: [...b.timeline.slice(0, -1), { ...last, open: false, endTime: Date.now() }],
    }
  }

  // 流式增量入列：追加到末尾同类型的 open 项；末尾不是（或已关闭）则先关闭
  // 末尾 open 项、再新建该类型项——类型切换即时间线分段，保持真实时序
  const appendStream = (blockId: string, kind: 'text' | 'reasoning', text: string) => {
    updateBlock(blockId, b => {
      const items = [...b.timeline]
      const last = items[items.length - 1]
      const now = Date.now()
      if (last && last.open && last.type === kind) {
        items[items.length - 1] = { ...last, content: (last.content || '') + text }
      } else {
        if (last && last.open) {
          items[items.length - 1] = { ...last, open: false, endTime: now }
        }
        items.push({ id: genId(), type: kind, content: text, open: true, startTime: now })
      }
      return { ...b, timeline: items }
    })
  }

  // 立即 flush 缓冲中的流式增量：按思考→正文顺序入列。
  // 快照当前值，避免批处理时闭包读到被后续事件改动的 ref
  const flushPending = () => {
    if (flushTimer != null) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
    const reasoning = pendingRef.current.reasoning
    const text = pendingRef.current.text
    pendingRef.current = { text: '', reasoning: '' }
    const blockId = currentBlockId.current
    if (!blockId) return
    if (reasoning) appendStream(blockId, 'reasoning', reasoning)
    if (text) appendStream(blockId, 'text', text)
  }

  // 节流入口：content/reasoning 事件只累积到对应缓冲，约 80ms 或合计超 160 字符才 flush
  const onContent = (kind: 'text' | 'reasoning', content: string) => {
    pendingRef.current[kind] += content
    if (flushTimer == null) {
      flushTimer = window.setTimeout(() => {
        flushTimer = null
        flushPending()
      }, 80)
    }
    // 累积较多时立即 flush，避免单次延迟过大
    if (pendingRef.current.text.length + pendingRef.current.reasoning.length > 160) {
      flushPending()
    }
  }

  // 处理单个 SSE 事件
  const handleSSEEvent = (evt: SSEEvent) => {
    // 会话元信息（后端自动建会话回传）：更新会话 ID，不依赖 block 存在
    if (evt.type === 'session_meta' && evt.session_id) {
      setSessionId(evt.session_id)
      return
    }
    // 上下文分类估算：直接刷新面板数据，同样不依赖 block 存在，
    // 必须放在 blockId 早退守卫之前，否则无工作块场景下事件被静默丢弃
    if (evt.type === 'stream' && evt.event_type === 'context_breakdown' && evt.breakdown) {
      set({ contextBreakdown: evt.breakdown })
      return
    }
    const blockId = currentBlockId.current
    if (!blockId) return

    // 非流式文本事件（完成/工具调用/错误等）到来前先 flush，避免缓冲内容滞留丢失
    const isStreamText =
      evt.type === 'stream' && (evt.event_type === 'content' || evt.event_type === 'reasoning')
    if (!isStreamText) {
      flushPending()
    }

    if (evt.type === 'stream') {
      if (evt.event_type === 'content' && evt.content) {
        // 正文增量（含工具轮之间的过渡叙述）：累积到缓冲，节流入列
        onContent('text', evt.content)
      } else if (evt.event_type === 'reasoning' && evt.content) {
        // 思考增量：独立成项入列（带计时），不再绑定到下一个工具步骤
        onContent('reasoning', evt.content)
      } else if (evt.event_type === 'tool_call_delta') {
        // 注意分支条件不能要求 tool_call_name：OpenAI 兼容流里只有首个分片带 name，
        // 后续分片只有 arguments——以 name 有无作条件会把参数分片全部丢弃，
        // 事件行就永远拿不到命令/路径（历史恢复有文本、实时块空白的差异即源于此）
        const argsFragment = evt.tool_call_arguments || ''
        const toolName = evt.tool_call_name
        if (toolName) {
          // 工具调用开始：关闭 open 的正文/思考项（内容留在时间线，不再清空），
          // 再建 tool 项；同名运行中项视为参数延续
          updateBlock(blockId, b => {
            const base = closeOpenIn(b)
            const last = base.timeline[base.timeline.length - 1]
            if (last && last.type === 'tool' && last.isRunning && last.toolName === toolName) {
              return {
                ...base,
                timeline: [...base.timeline.slice(0, -1), { ...last, args: (last.args || '') + argsFragment }],
              }
            }
            return {
              ...base,
              timeline: [
                ...base.timeline,
                { id: genId(), type: 'tool', toolName, args: argsFragment, isRunning: true, startTime: Date.now() },
              ],
            }
          })
        } else {
          // 无 name 的纯参数分片：追加到最后一个运行中步骤。
          // OpenAI 流按 index 串行发完一个工具的分片再发下一个，尾部追加即正确归属
          updateBlock(blockId, b => {
            const last = b.timeline[b.timeline.length - 1]
            if (last && last.type === 'tool' && last.isRunning) {
              return {
                ...b,
                timeline: [...b.timeline.slice(0, -1), { ...last, args: (last.args || '') + argsFragment }],
              }
            }
            return b
          })
        }
      } else if (evt.event_type === 'phase' && evt.content) {
        // 后端阶段事件：只存最近一次，文案由 WorkBlock 按优先级推导
        updateBlock(blockId, b => ({ ...b, phase: evt.content }))
      } else if (evt.event_type === 'error') {
        // 错误保留 tool 项形态：异常展开区首行的原因提取依赖 toolName==='error'
        updateBlock(blockId, b => {
          const base = closeOpenIn(b)
          return {
            ...base,
            timeline: [
              ...base.timeline,
              { id: genId(), type: 'tool', toolName: 'error', args: '', result: `错误: ${evt.error || '未知错误'}` },
            ],
          }
        })
      } else if (evt.event_type === 'done') {
        // 模型轮结束：关闭 open 的正文/思考项（流式光标随之消失）
        updateBlock(blockId, b => closeOpenIn(b))
      }
    } else if (evt.type === 'message' && evt.message) {
      const msg = evt.message
      if (msg.role === 'tool') {
        // 工具结果：填到最近的运行中 tool 项并补计时。
        // Agent 步骤是子代理报告（含 agent_id 头行与 usage 尾部），放宽到 3 万字符，
        // 其余工具保留 500 字符防界面卡顿
        updateBlock(blockId, b => {
          const idx = [...b.timeline].reverse().findIndex(s => s.type === 'tool' && s.isRunning)
          if (idx === -1) return b
          const realIdx = b.timeline.length - 1 - idx
          const toolName = b.timeline[realIdx]?.toolName || ''
          const cap = toolName === 'Agent' || toolName === 'Task' ? 30000 : 500
          const updated = [...b.timeline]
          updated[realIdx] = {
            ...updated[realIdx],
            result: (msg.content || '').slice(0, cap),
            isRunning: false,
            endTime: Date.now(),
          }
          return { ...b, timeline: updated }
        })
      } else if (msg.role === 'assistant' && msg.tool_calls && msg.tool_calls.length > 0) {
        // 中间轮（含工具调用）：过渡叙述已由流式 content 入列，这里只关闭 open 项。
        // 直播路径忽略消息携带的 _reasoning（思考已由 reasoning 事件入列，避免重复）
        updateBlock(blockId, b => closeOpenIn(b))
      } else if (msg.role === 'assistant') {
        // 最终回复：用落库正文覆盖最后一个 text 项，保证与持久化内容一致
        // （该项可能已被 done 关闭）；末尾不是 text 项则追加新 text 项
        const content = msg.content || ''
        updateBlock(blockId, b => {
          const items = [...b.timeline]
          const last = items[items.length - 1]
          if (last && last.type === 'text') {
            items[items.length - 1] = { ...last, content, open: false, endTime: last.endTime ?? Date.now() }
          } else {
            if (last && last.open) {
              items[items.length - 1] = { ...last, open: false, endTime: Date.now() }
            }
            if (content) {
              const now = Date.now()
              items.push({ id: genId(), type: 'text', content, open: false, startTime: now, endTime: now })
            }
          }
          return { ...b, timeline: items }
        })
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
      // 引擎级错误：标记工作块为 done，保留已入列的正文；完全无正文时补一条
      // error tool 项承载错误信息（形态不变，exitReasonLine 提取路径依赖它）
      updateBlock(blockId, b => {
        const base = closeOpenIn(b)
        const hasText = base.timeline.some(s => s.type === 'text' && (s.content || '').trim())
        return {
          ...base,
          status: 'done' as const,
          endTime: Date.now(),
          exitReason: 'error',
          timeline: hasText
            ? base.timeline
            : [
                ...base.timeline,
                { id: genId(), type: 'tool', toolName: 'error', args: '', result: `错误: ${evt.error || '未知错误'}` },
              ],
        }
      })
    } else if (evt.type === 'loop_result') {
      // 循环结束：关闭 open 项并标记工作块为已工作
      updateBlock(blockId, b => ({
        ...closeOpenIn(b),
        status: 'done' as const,
        endTime: Date.now(),
        exitReason: evt.reason || '',
      }))
      currentBlockId.current = null
      pendingRef.current = { text: '', reasoning: '' }
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
    pendingRef.current = { text: '', reasoning: '' }
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
          timeline: [],
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
      // 上下文分类估算：重进会话时经 state 恢复面板数据
      if (data.context_breakdown) set({ contextBreakdown: data.context_breakdown })
      if (typeof data.total_cost_usd === 'number') set({ totalCost: data.total_cost_usd })
      if (data.model) set({ model: data.model })
      if (data.permission_mode === 'default' || data.permission_mode === 'full_access') {
        set({ permissionMode: data.permission_mode })
      }
    } catch {
      // 后端未就绪时静默忽略
    }
  }

  // 发起 /api/chat SSE 并驱动工作块渲染。
  // editUserIndex 非 null 时走编辑重发通道（后端截断该可见用户消息之后的历史）。
  // sendMessage 普通路径与 editAndResend 共用，catch/finally 语义保持一致
  const runChatSSE = async (prompt: string, editUserIndex: number | null = null) => {
    try {
      sseAbortRef.current = new AbortController()
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(
          editUserIndex === null
            ? { prompt, session_id: sessionIdRef.current }
            : { prompt, session_id: sessionIdRef.current, edit_user_index: editUserIndex }
        ),
        signal: sseAbortRef.current.signal,
      })
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      await parseSSEStream(resp)
    } catch (e) {
      const blockId = currentBlockId.current
      if (blockId) {
        // 请求失败提示以 text 项形态入列
        updateBlock(blockId, b => {
          const base = closeOpenIn(b)
          return {
            ...base,
            status: 'done' as const,
            endTime: Date.now(),
            exitReason: 'error',
            timeline: [
              ...base.timeline,
              {
                id: genId(),
                type: 'text',
                content: `请求失败: ${e instanceof Error ? e.message : String(e)}`,
                open: false,
              },
            ],
          }
        })
      }
    } finally {
      set({ isStreaming: false })
      // 兜底 flush：异常断开时把缓冲内容落进时间线，避免尾部文本丢失
      flushPending()
      // 如果没有收到 loop_result（异常断开），强制标记为 done
      const blockId = currentBlockId.current
      if (blockId) {
        updateBlock(blockId, b => ({
          ...closeOpenIn(b),
          status: 'done' as const,
          endTime: Date.now(),
          exitReason: b.exitReason || 'aborted',
        }))
        currentBlockId.current = null
      }
    }
  }

  // 发送消息。返回 false 表示消息被拒收（任务运行中 / 空内容），调用方可据此提示
  const sendMessage = async (prompt: string): Promise<boolean> => {
    if (!prompt.trim()) return false
    if (get().isStreaming) return false

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
          // skill 触发：创建工作块。用户气泡显示「技能徽章 + 去掉 /name 前缀的
          // 原始描述」，技能正文经 skill_prompt 发到 /api/chat
          const blockId = createBlock(`Launching skill: ${data.skill_name}`)
          const taskText = prompt.replace(/^\/\S+\s*/, '').trim() || prompt
          updateBlock(blockId, b => ({ ...b, userMessage: taskText, skillName: data.skill_name }))
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
          // 普通命令：输出作为 text 项入列，块直接标记完成
          const blockId = createBlock(prompt)
          updateBlock(blockId, b => ({
            ...b,
            status: 'done' as const,
            endTime: Date.now(),
            exitReason: 'command',
            timeline: data.output
              ? [{ id: genId(), type: 'text' as const, content: data.output, open: false }]
              : [],
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
            timeline: [
              ...b.timeline,
              {
                id: genId(),
                type: 'text',
                content: `命令执行失败: ${e instanceof Error ? e.message : String(e)}`,
                open: false,
              },
            ],
          }))
        }
      } finally {
        set({ isStreaming: false })
        currentBlockId.current = null
      }
      await fetchState()
      return true
    }

    // 普通对话：创建工作块走 SSE 流
    set({ isStreaming: true })
    createBlock(prompt)
    await runChatSSE(prompt)
    await fetchState()
    return true
  }

  // 编辑历史消息并从该处重发：截断该消息之后的所有块与 DB 历史，
  // 用编辑后的文本重建该位置的轮次。返回 false 表示未发起（空文本/块不存在）
  const editAndResend = async (blockId: string, newText: string): Promise<boolean> => {
    const text = newText.trim()
    if (!text) return false
    // 运行中先停止（含断开旧流），再基于最新状态定位
    if (get().isStreaming) await abort()
    const state = get()
    const targetBlock = state.blocksById[blockId]
    if (!targetBlock) return false

    // 定位 edit_user_index：块在 blockIds 中的下标，减去其前命令块数
    // （命令块是前端本地产物，DB 无对应用户消息，不计入可见用户消息序号）
    const idx = state.blockIds.indexOf(blockId)
    if (idx === -1) return false
    const commandsBefore = state.blockIds
      .slice(0, idx)
      .filter(id => state.blocksById[id]?.exitReason === 'command').length
    const editUserIndex = idx - commandsBefore

    // 本地截断：丢弃该块及其后所有块，blocksById 同步清理避免脏块残留
    const keptIds = state.blockIds.slice(0, idx)
    const keptById: Record<string, WorkBlock> = {}
    for (const id of keptIds) keptById[id] = state.blocksById[id]
    set({ blockIds: keptIds, blocksById: keptById })

    // 组装 prompt：技能块按重写提示形状重组（徽章展示靠 skillName，模板形状
    // 与 server/routers/commands/routes.py 的 skill_prompt 生成逻辑保持一致，
    // 两侧任一改动需同步）；普通块直接用编辑后文本
    const skillName = targetBlock.skillName
    const prompt = skillName
      ? [
          `Use the skill named \`${skillName}\` for this turn.`,
          `First call the \`Skill\` tool with skill="${skillName}" before doing the task.`,
          'After the skill content is loaded, follow its instructions and continue.',
          '',
          `User request: ${text}`,
        ].join('\n')
      : text

    set({ isStreaming: true })
    createBlock(text)
    // 重写提示不以 / 开头、不会命中 sendMessage 的技能分流，徽章在此补挂
    if (skillName) {
      const bid = currentBlockId.current
      if (bid) updateBlock(bid, b => ({ ...b, skillName }))
    }
    await runChatSSE(prompt, editUserIndex)
    await fetchState()
    return true
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
    // 显式断开旧 SSE 连接：后端任务已收尾，但旧流 reader 可能还压着缓冲中的
    // 迟到事件；不主动断开的话，这些事件会在用户新消息 createBlock 之后才被
    // 处理，共享的 currentBlockId 让它们打进新块（提前标 done 甚至丢事件）。
    // abort 触发旧 parseSSEStream 的 AbortError，旧 sendMessage 的 catch/finally
    // 在本轮微任务里跑完（此时 currentBlockId 已置 null，自然跳过），之后用户
    // 才可能触发新 sendMessage，迟到事件不再有落点
    sseAbortRef.current?.abort()
    sseAbortRef.current = null
    const blockId = currentBlockId.current
    if (blockId) {
      // 先 flush 缓冲，避免已接收但未显示的文本丢失
      flushPending()
      updateBlock(blockId, b => {
        const base = closeOpenIn(b)
        const hasText = base.timeline.some(s => s.type === 'text' && (s.content || '').trim())
        // 占位阶段中止时既无事件也无文本，兜底一条「已停止」text 项，不留转圈残留
        const timeline =
          !hasText && base.timeline.length === 0
            ? [{ id: genId(), type: 'text' as const, content: '已停止', open: false }]
            : base.timeline.map(s =>
                s.isRunning ? { ...s, isRunning: false, result: s.result || '已停止', endTime: Date.now() } : s,
              )
        return {
          ...base,
          status: 'done' as const,
          endTime: Date.now(),
          exitReason: 'aborted',
          timeline,
        }
      })
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
    flushPending()
    set({ isStreaming: false })
  }

  // 清空
  const clearMessages = () => {
    set({ blockIds: [], blocksById: {} })
    currentBlockId.current = null
    pendingRef.current = { text: '', reasoning: '' }
    if (flushTimer != null) {
      clearTimeout(flushTimer)
      flushTimer = null
    }
  }

  // 从后端历史消息恢复：扁平消息列表 -> 工作块时间线。
  // assistant 消息携带的下划线内部字段（_ts/_reasoning/_reasoning_ms）用于
  // 还原思考行与真实耗时；旧数据缺字段时自然降级（无思考行、耗时回退加载时刻）。
  // opts.runningStartedAt（毫秒）表示该会话有运行中任务，最后一块据此标记 running，
  // 恢复「工作中 X秒」逐秒计时与事件行运行中 spinner。
  const loadMessages = (rawMessages: Record<string, unknown>[], opts?: { runningStartedAt?: number }) => {
    const newBlocks: WorkBlock[] = []
    let currentBlock: WorkBlock | null = null
    let userMsgIndex = 0
    const sessionId = sessionIdRef.current ?? 'default'
    const runningStartedAt = opts?.runningStartedAt
    // 消息 _ts → 时间戳（毫秒），缺省回退加载时刻
    const tsOf = (raw: Record<string, unknown>) => {
      const t = raw._ts
      return typeof t === 'number' ? t : Date.now()
    }

    for (const raw of rawMessages) {
      const role = raw.role as string
      const content = (raw.content as string) || ''

      if (role === 'user') {
        const parsed = parseUserMessage(content)
        // skip：系统注入消息（Skill 正文等），不建块不显示，后续步骤归当前块
        if (parsed.kind === 'skip') continue
        // 新工作块：id 用「会话 + 消息序号」稳定派生，轮询刷新时不重挂载
        if (currentBlock) newBlocks.push(currentBlock)
        const start = tsOf(raw)
        currentBlock = {
          id: `${sessionId}:b${userMsgIndex++}`,
          userMessage: parsed.text,
          // skill：渐进披露重写提示 → 「技能徽章 + 任务描述」展示
          skillName: parsed.kind === 'skill' ? parsed.skillName : undefined,
          timeline: [],
          status: 'done',
          startTime: start,
          endTime: start,
          exitReason: 'completed',
        }
      } else if (role === 'assistant') {
        if (!currentBlock) continue
        const ts = tsOf(raw)
        // 入列时序：思考 → 正文（过渡叙述或最终回复）→ 工具调用
        const reasoning = raw._reasoning
        if (typeof reasoning === 'string' && reasoning) {
          const ms = raw._reasoning_ms
          // 非数字（旧数据/异常形态）时不做反推，起止同点显示「思考 · 0秒」
          const rStart = typeof ms === 'number' ? ts - ms : ts
          currentBlock.timeline.push({
            id: genId(),
            type: 'reasoning',
            content: reasoning,
            open: false,
            startTime: rStart,
            endTime: ts,
          })
        }
        if (content) {
          currentBlock.timeline.push({
            id: genId(),
            type: 'text',
            content,
            open: false,
            startTime: ts,
            endTime: ts,
          })
        }
        const toolCalls = raw.tool_calls as Array<{
          id: string
          function: { name: string; arguments: string }
        }> | undefined
        if (toolCalls && toolCalls.length > 0) {
          for (const tc of toolCalls) {
            currentBlock.timeline.push({
              id: genId(),
              type: 'tool',
              toolName: tc.function.name,
              args: tc.function.arguments,
              isRunning: false,
              startTime: ts,
            })
          }
        }
        currentBlock.endTime = ts
      } else if (role === 'tool') {
        // 工具结果：填到最近的未完成工具项（Agent/Task 放宽到 3 万字符）
        if (currentBlock) {
          const lastTool = [...currentBlock.timeline].reverse().find(s => s.type === 'tool' && !s.result)
          if (lastTool) {
            const cap = lastTool.toolName === 'Agent' || lastTool.toolName === 'Task' ? 30000 : 500
            lastTool.result = content.slice(0, cap)
            lastTool.endTime = tsOf(raw)
          }
          currentBlock.endTime = tsOf(raw)
        }
      }
    }
    if (currentBlock) newBlocks.push(currentBlock)

    // 运行中的后台任务：最后一块标记 running，恢复耗时与事件行 spinner
    if (newBlocks.length > 0 && typeof runningStartedAt === 'number' && Number.isFinite(runningStartedAt)) {
      const last = newBlocks[newBlocks.length - 1]
      last.status = 'running'
      last.startTime = runningStartedAt
      last.endTime = undefined
      last.exitReason = undefined
      // 末尾是正文/思考项时重新打开（流式光标与「思考中」计时延续）
      const lastItem = last.timeline[last.timeline.length - 1]
      if (lastItem && (lastItem.type === 'text' || lastItem.type === 'reasoning')) {
        lastItem.open = true
        lastItem.endTime = undefined
      }
      const lastTool = [...last.timeline].reverse().find(s => s.type === 'tool' && !s.result)
      if (lastTool) lastTool.isRunning = true
    }

    // 中断回合兜底：最后一块时间线为空且无运行中任务，说明该轮在 assistant
    // 消息落库前就被停止（abort 的直播兜底文案会被轮询历史重建抹掉），
    // 按直播 abort 同款形态重建「已停止」text 项并标 aborted
    if (newBlocks.length > 0 && typeof runningStartedAt !== 'number') {
      const last = newBlocks[newBlocks.length - 1]
      if (last.timeline.length === 0) {
        last.exitReason = 'aborted'
        last.timeline.push({ id: genId(), type: 'text', content: '已停止', open: false })
      }
    }

    // 钳制 endTime >= startTime，避免新旧消息混合出现负耗时
    for (const b of newBlocks) {
      if (b.endTime !== undefined && b.endTime < b.startTime) {
        b.endTime = b.startTime
      }
    }

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
      total_input_tokens: 0,
      last_prompt_tokens: 0,
      last_cache_creation: 0,
    },
    contextBreakdown: null,
    totalCost: 0,
    model: '',
    permissionRequest: null,
    questionRequest: null,
    permissionMode: 'default',
    sendMessage,
    editAndResend,
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
