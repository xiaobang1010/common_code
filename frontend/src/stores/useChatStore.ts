// 聊天工作区（AI 面板）的全局状态 store
// 状态规范化：blockIds + blocksById，组件按 selector 局部订阅，
// 流式更新只触发当前工作块重渲，不再带动整棵 App 树
// 轨迹模型：一次 agentic 循环的过程与回复统一为按真实时序入列的时间线
// （text/reasoning/tool 三类一等事件交错排布），最终回复即最后一个 text 项
// 逻辑迁移自 hooks/useChat.ts

import { create } from 'zustand'
import { permissionsApi, questionApi, type PermissionMode, type TurnExitInfo } from '../api/client'
import { parseUserMessage, extractContentParts } from '../utils/skillParse'
import { openFilesInPanel } from './panelBridge'

// 时间线事件：任务轨迹的三类一等事件，按 SSE 到达的真实时序入列
export interface TimelineItem {
  id: string
  // text = 正文（过渡叙述与最终回复）；reasoning = 思维链；tool = 工具调用；
  // compact = 上下文压缩分隔线（进行中/完成/失败）
  type: 'text' | 'reasoning' | 'tool' | 'compact'
  // text 正文 / reasoning 思维链全文
  content?: string
  // text/reasoning 是否仍在流式追加（open）；类型切换或回合结束时关闭
  open?: boolean
  // ---- 以下仅 tool 项 ----
  toolName?: string
  args?: string
  result?: string
  isRunning?: boolean
  // ---- 以下仅 compact 项 ----
  // 压缩状态：running（进行中）/ done（完成）/ failed（失败）
  compactStatus?: 'running' | 'done' | 'failed'
  // 压缩前后统一计数 token 数（done 时展示）
  tokensBefore?: number
  tokensAfter?: number
  // 失败时的可操作文案
  compactReason?: string
  // ---- reasoning/tool 计时（ms，事件到达时刻），供「思考 · X秒」显示 ----
  startTime?: number
  endTime?: number
}

// 用户消息携带的图片附件（dataUrl 为完整 data URL，解码后 ≤5MB）
export interface UserImage {
  name: string
  mime: string
  dataUrl: string
}

// 工作块：一次 agentic 循环的轨迹聚合
export interface WorkBlock {
  id: string
  // 用户输入
  userMessage: string
  // 用户消息附带的图片（无图时缺省）：气泡渲染缩略图，含图块隐藏编辑重发
  userImages?: UserImage[]
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
  // 该用户消息由队列「立即」转向注入（guide 转正带 _steer）：气泡挂「已引导对话」标
  userSteered?: boolean
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
// 由后端 context_metrics 生成，占比为 0 的分类不出现。
// model 非空时另带窗口元信息：window / window_source(字符串) / auto_compact_threshold。
export type ContextBreakdown = Record<string, number | string>

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
  breakdown?: Record<string, number | string>
  // 压缩事件载荷（event_type === 'compact_*' 时）
  compact_info?: {
    status: string
    tokens_before: number
    tokens_after: number
    reason: string
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
    // present_files 交付事件（role === 'present_files'）
    files?: string[]
    explanation?: string
    // 压缩摘要标记（role === 'user' 且携带）：前端隐藏不入时间线
    _compact_summary?: boolean
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

// 最近一次随消息发送的图片附件：/api/state 轮询重建时 image_url 为占位形态
// （base64 已剥离），按序用这里的缓存补回 data URL 渲染；任务结束走全量历史接口
const lastSentImagesRef = { current: [] as UserImage[] }

// 输入队列项：运行中发送的消息在后端 session_input 排队，队列条展示用
export interface QueueItem {
  id: string
  content: string
  hasImage: boolean
  delivery: string
}

// 空闲发送时队列非空的三态确认（保留/清除/取消）
export interface QueueConfirm {
  prompt: string
  images: UserImage[]
}

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

  // 会话所选推理等级（null=跟随模型默认，不注入推理参数）；切换模型时重置
  reasoningLevel: string | null
  setReasoningLevel: (level: string | null) => void
  // 流前被服务端拒绝时的可读文案（sendMessage 返回 false 时由输入区消费并清除）
  lastSendError: string | null
  clearSendError: () => void
  // 运行中发送的输入队列项（后端 session_input queued 行，轮次边界转正）
  queueItems: QueueItem[]
  // 已点「立即」但未转正的转向行：不入队列条，在对话内挂「等待引导当前任务…」
  pendingGuides: QueueItem[]
  queueState: { auto_drain: boolean; pause_reason: string | null }
  // 空闲发送且队列非空时的三态确认现场（null=无待确认）
  queueConfirm: QueueConfirm | null
  refreshQueue: () => Promise<void>
  cancelQueueItem: (id: string) => Promise<void>
  steerQueueItem: (id: string) => Promise<void>
  editQueueItem: (id: string, content: string) => Promise<void>
  reorderQueueItem: (id: string, beforeId: string | null) => Promise<void>
  clearQueue: () => Promise<void>
  toggleQueuePause: (paused: boolean) => Promise<void>
  resolveQueueConfirm: (choice: 'keep' | 'clear' | 'cancel') => Promise<void>
  sendMessage: (prompt: string, images?: UserImage[], opts?: { skipConfirm?: boolean }) => Promise<boolean>
  // 编辑历史用户消息并从该处重发：截断后续块与 DB 历史，用新文本重建该轮
  editAndResend: (blockId: string, newText: string) => Promise<boolean>
  abort: () => Promise<void>
  loadMessages: (rawMessages: Record<string, unknown>[], opts?: { runningStartedAt?: number; lastTurn?: TurnExitInfo }) => void
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
      } else if (evt.event_type === 'compact_started') {
        // 压缩进行中：关闭 open 项后插入 running 分隔线（正文/工具不再交错进压缩条目）
        updateBlock(blockId, b => {
          const base = closeOpenIn(b)
          return {
            ...base,
            timeline: [
              ...base.timeline,
              { id: genId(), type: 'compact', compactStatus: 'running' },
            ],
          }
        })
      } else if (evt.event_type === 'compact_completed') {
        // 压缩完成：把最近的 running 分隔线置为 done 并写入前后计数
        updateBlock(blockId, b => {
          const info = evt.compact_info
          const idx = [...b.timeline].reverse().findIndex(s => s.type === 'compact' && s.compactStatus === 'running')
          if (idx === -1) {
            // 无进行中条目（如刷新错过 started）：直接补一条 done
            return {
              ...b,
              timeline: [
                ...b.timeline,
                {
                  id: genId(), type: 'compact', compactStatus: 'done',
                  tokensBefore: info?.tokens_before, tokensAfter: info?.tokens_after,
                },
              ],
            }
          }
          const realIdx = b.timeline.length - 1 - idx
          const updated = [...b.timeline]
          updated[realIdx] = {
            ...updated[realIdx],
            compactStatus: 'done',
            tokensBefore: info?.tokens_before,
            tokensAfter: info?.tokens_after,
          }
          return { ...b, timeline: updated }
        })
      } else if (evt.event_type === 'compact_failed') {
        // 压缩失败：running 分隔线置 failed 并带可操作文案
        updateBlock(blockId, b => {
          const info = evt.compact_info
          const idx = [...b.timeline].reverse().findIndex(s => s.type === 'compact' && s.compactStatus === 'running')
          const reason = info?.reason || evt.content || '上下文压缩失败'
          if (idx === -1) {
            return {
              ...b,
              timeline: [
                ...b.timeline,
                { id: genId(), type: 'compact', compactStatus: 'failed', compactReason: reason },
              ],
            }
          }
          const realIdx = b.timeline.length - 1 - idx
          const updated = [...b.timeline]
          updated[realIdx] = { ...updated[realIdx], compactStatus: 'failed', compactReason: reason }
          return { ...b, timeline: updated }
        })
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
      } else if (msg.role === 'present_files') {
        // 交付事件：右侧面板按优先级顺序打开文件（桥接由 App 注册），
        // explanation 补写到交付卡开头，用户不展开文件也能看到一句话说明
        const files = Array.isArray(msg.files) ? msg.files : []
        if (files.length > 0) openFilesInPanel(files)
        const explanation = (msg.explanation || '').trim()
        if (explanation) {
          updateBlock(blockId, b => {
            const idx = [...b.timeline]
              .reverse()
              .findIndex(s => s.type === 'tool' && s.toolName === 'present_files')
            if (idx === -1) return b
            const realIdx = b.timeline.length - 1 - idx
            const item = b.timeline[realIdx]
            const updated = [...b.timeline]
            updated[realIdx] = { ...item, result: explanation + '\n' + (item.result || '') }
            return { ...b, timeline: updated }
          })
        }
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
      } else if (msg.role === 'user') {
        // 转正的队列项：直播不重复渲染气泡（历史重建负责呈现），
        // 从队列条/挂起列表移除；guide 转正给当前块挂「已引导对话」标
        const inputId = (msg as Record<string, unknown>)._input_id
        if (typeof inputId === 'string' && inputId) {
          set(state => ({
            queueItems: state.queueItems.filter(q => q.id !== inputId),
            pendingGuides: state.pendingGuides.filter(q => q.id !== inputId),
          }))
        }
        if ((msg as Record<string, unknown>)._steer === true) {
          const blockId = currentBlockId.current
          if (blockId) updateBlock(blockId, b => ({ ...b, userSteered: true }))
        }
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
      if (data.model) {
        // 活动模型变化时重置推理等级选择（等级列表按新模型重新渲染）
        if (data.model !== get().model) set({ model: data.model, reasoningLevel: null })
        else set({ model: data.model })
      }
      if (data.permission_mode === 'default' || data.permission_mode === 'full_access') {
        set({ permissionMode: data.permission_mode })
      }
    } catch {
      // 后端未就绪时静默忽略
    }
  }

  // 发起 /api/chat SSE 并驱动工作块渲染。
  // editUserIndex 非 null 时走编辑重发通道（后端截断该可见用户消息之后的历史）。
  // images 仅首发路径传入（编辑重发恒空）；reasoning_level 随会话所选等级携带。
  // 返回 {ok:false,error} 表示流前被服务端拒绝（400），调用方回滚本轮工作块；
  // 流中断等异常仍按原语义把失败提示写进块内并返回 ok:true
  const runChatSSE = async (
    prompt: string,
    editUserIndex: number | null = null,
    images: UserImage[] = [],
  ): Promise<{ ok: boolean; error?: string; queued?: boolean }> => {
    try {
      sseAbortRef.current = new AbortController()
      const body: Record<string, unknown> = { prompt, session_id: sessionIdRef.current }
      if (editUserIndex !== null) body.edit_user_index = editUserIndex
      const level = get().reasoningLevel
      if (level) body.reasoning_level = level
      if (images.length > 0) {
        body.images = images.map((im) => ({ name: im.name, mime: im.mime, data_url: im.dataUrl }))
      }
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
        signal: sseAbortRef.current.signal,
      })
      if (!resp.ok) {
        // 流前拒绝：解析服务端 ok/error 文案上抛，不改动块（由调用方回滚）
        let msg = `HTTP ${resp.status}`
        try {
          const j = await resp.json()
          if (j && typeof j.error === 'string') msg = j.error
        } catch { /* 非 JSON 响应保留状态码文案 */ }
        return { ok: false, error: msg }
      }
      // 运行中被后端转入队列：返回 JSON 而非 SSE 流，按入队成功处理
      const ct = resp.headers.get('content-type') || ''
      if (ct.includes('application/json')) {
        const j = await resp.json().catch(() => null)
        if (j && j.queued) {
          void get().refreshQueue()
          return { ok: true, queued: true }
        }
        return { ok: false, error: (j && j.error) || '消息排队失败' }
      }
      lastSentImagesRef.current = images
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
      // 收尾刷新队列：本轮边界未消化完的残留行（如收尾窗口内新入队）继续呈现
      void get().refreshQueue()
      // 兜底 flush：异常断开时把缓冲内容落进时间线，避免尾部文本丢失
      flushPending()
      // 如果没有收到 loop_result（流干净断开），强制标记为 done 并标 stream_lost。
      // 注意：真实停止由 abort() 先行写入 aborted（且已把 currentBlockId 置空，
      // 这里整段跳过），所以此兜底只命中「未收到结果流就断了」的异常形态，
      // 不再复用 aborted——「用户主动停止」的标签只留给真实停止操作
      const blockId = currentBlockId.current
      if (blockId) {
        updateBlock(blockId, b => ({
          ...closeOpenIn(b),
          status: 'done' as const,
          endTime: Date.now(),
          exitReason: b.exitReason || 'stream_lost',
        }))
        currentBlockId.current = null
      }
    }
    return { ok: true }
  }

  // 回滚删除工作块：流前被拒时 DB 无对应消息，必须移除幽灵块保证 1:1
  const removeBlock = (id: string) => {
    if (currentBlockId.current === id) currentBlockId.current = null
    set(state => {
      const next = { ...state.blocksById }
      delete next[id]
      return { blockIds: state.blockIds.filter(x => x !== id), blocksById: next }
    })
  }

  // 发送消息。返回 false 表示消息被拒收（任务运行中 / 空内容 / 流前被服务端拒绝），
  // 调用方可据此保留待发附件并用 lastSendError 提示
  // 运行中入队：POST /api/chat 返回 JSON（非 SSE）即受理成功，队列条随后刷新呈现
  const enqueueSend = async (prompt: string, images: UserImage[]): Promise<boolean> => {
    try {
      const body: Record<string, unknown> = { prompt, session_id: sessionIdRef.current }
      if (images.length > 0) {
        body.images = images.map((im) => ({ name: im.name, mime: im.mime, data_url: im.dataUrl }))
      }
      const resp = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      const j = await resp.json().catch(() => null)
      if (j && j.queued) {
        await refreshQueue()
        return true
      }
      set({ lastSendError: (j && j.error) || '消息排队失败' })
      return false
    } catch {
      set({ lastSendError: '消息排队失败' })
      return false
    }
  }

  const sendMessage = async (
    prompt: string,
    images: UserImage[] = [],
    opts?: { skipConfirm?: boolean },
  ): Promise<boolean> => {
    // 空文本守卫在存在待发附件时放开（纯图片消息允许发送）
    if (!prompt.trim() && images.length === 0) return false
    // 空闲发送且队列非空：先过三态确认（旧行将随起轮全量转正，需问处置）；
    // 运行中排队与裁决后的补发（skipConfirm）不拦截；纯同步命令不起轮、在本分支后豁免
    if (
      !opts?.skipConfirm
      && !get().isStreaming
      && !prompt.startsWith('/')
      && get().queueItems.length > 0
    ) {
      set({ queueConfirm: { prompt, images } })
      return false
    }
    // 运行中发送走统一输入队列（斜杠命令是同步接口，不排队）
    if (get().isStreaming) {
      if (prompt.startsWith('/')) return false
      return enqueueSend(prompt, images)
    }

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
          // 技能续跑同样起轮：队列非空先过三态确认。裁决后以 skipConfirm 重发，
          // /api/command 对技能无副作用、重跑到达同分支
          if (!opts?.skipConfirm && get().queueItems.length > 0) {
            set({ queueConfirm: { prompt, images: [] } })
            return false
          }
          // skill 触发：创建工作块。用户气泡显示「技能徽章 + 去掉 /name 前缀的
          // 原始描述」，技能正文经 skill_prompt 发到 /api/chat
          const blockId = createBlock(`Launching skill: ${data.skill_name}`)
          const taskText = prompt.replace(/^\/\S+\s*/, '').trim() || prompt
          updateBlock(blockId, b => ({ ...b, userMessage: taskText, skillName: data.skill_name }))
          sseAbortRef.current = new AbortController()
          const skillBody: Record<string, unknown> = { prompt: data.skill_prompt, session_id: sessionIdRef.current }
          // 技能续跑同样携带会话所选推理等级（与 runChatSSE 口径一致）
          const level = get().reasoningLevel
          if (level) skillBody.reasoning_level = level
          const chatResp = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(skillBody),
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

    // 普通对话：创建工作块走 SSE 流；附件即时挂到气泡，流式期间即可见缩略图
    set({ isStreaming: true })
    const blockId = createBlock(prompt)
    if (images.length > 0) {
      updateBlock(blockId, b => ({ ...b, userImages: images }))
    }
    const res = await runChatSSE(prompt, null, images)
    if (res.queued) {
      // 后台任务在跑被转入队列：回滚本轮空工作块，由队列条接管呈现
      removeBlock(blockId)
      await fetchState()
      return true
    }
    if (!res.ok) {
      // 流前被拒：回滚本轮工作块、文案交输入区提示，附件由调用方保留重试
      removeBlock(blockId)
      set({ lastSendError: res.error || '发送失败' })
      await fetchState()
      return false
    }
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

    // 截断前快照：流前被服务端拒绝时整体还原，避免本地序号与 DB 错位
    const prevBlockIds = state.blockIds
    const prevBlocksById = state.blocksById

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
    const newBlockId = createBlock(text)
    // 重写提示不以 / 开头、不会命中 sendMessage 的技能分流，徽章在此补挂
    if (skillName) {
      const bid = currentBlockId.current
      if (bid) updateBlock(bid, b => ({ ...b, skillName }))
    }
    // 编辑重发恒不携带待发附件（images 缺省为空）
    const res = await runChatSSE(prompt, editUserIndex)
    if (!res.ok) {
      removeBlock(newBlockId)
      set({
        blockIds: prevBlockIds,
        blocksById: prevBlocksById,
        lastSendError: res.error || '发送失败',
      })
      await fetchState()
      return false
    }
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
    // 切会话同步刷新该会话的待转正队列
    void get().refreshQueue()
  }

  // 拉取当前会话未转正队列：按 delivery 分流——guide 行进对话挂起列表
  // （「等待引导当前任务…」），其余进队列条；同时带回队列态
  const refreshQueue = async () => {
    const sid = sessionIdRef.current
    if (!sid) {
      set({ queueItems: [], pendingGuides: [], queueState: { auto_drain: true, pause_reason: null } })
      return
    }
    try {
      const resp = await fetch(`/api/session_inputs?session_id=${encodeURIComponent(sid)}`)
      const j = await resp.json()
      const toItem = (it: { id: string; delivery?: string; content: unknown }): QueueItem => {
        let content = ''
        let hasImage = false
        if (typeof it.content === 'string') content = it.content
        else if (Array.isArray(it.content)) {
          content = it.content
            .filter((b: { type?: string }) => b?.type === 'text')
            .map((b: { text?: string }) => b.text || '')
            .join(' ')
          hasImage = it.content.some((b: { type?: string }) => b?.type === 'image_url')
        }
        return { id: it.id, content, hasImage, delivery: it.delivery || 'queue' }
      }
      const items: QueueItem[] = (Array.isArray(j?.items) ? j.items : [])
        .filter((it: { kind?: string }) => it.kind === 'sendText')
        .map(toItem)
      const qs = j?.queue_state || {}
      set({
        queueItems: items.filter((it) => it.delivery !== 'guide'),
        pendingGuides: items.filter((it) => it.delivery === 'guide'),
        queueState: {
          auto_drain: qs.auto_drain !== false,
          pause_reason: qs.pause_reason ?? null,
        },
      })
    } catch {
      /* 队列视图失败不影响主流程，保留上次状态 */
    }
  }

  // 队列动作统一收口：POST 后刷新视图，失败由刷新兜底呈现真实状态
  const postQueueAction = async (path: string, body: Record<string, unknown>) => {
    try {
      await fetch(path, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
    } catch {
      /* 动作失败由下次刷新兜底呈现真实状态 */
    }
    await refreshQueue()
  }

  const cancelQueueItem = async (id: string) => {
    try {
      await fetch(`/api/session_inputs/${encodeURIComponent(id)}`, { method: 'DELETE' })
    } catch {
      /* 撤销失败由下次刷新兜底呈现真实状态 */
    }
    await refreshQueue()
  }

  const steerQueueItem = (id: string) => postQueueAction('/api/session_inputs/steer', { id })
  const editQueueItem = (id: string, content: string) =>
    postQueueAction('/api/session_inputs/edit', { id, content })
  const reorderQueueItem = (id: string, beforeId: string | null) =>
    postQueueAction('/api/session_inputs/reorder', { id, before_id: beforeId })
  const clearQueue = async () => {
    const sid = sessionIdRef.current
    if (!sid) return
    await postQueueAction('/api/session_inputs/clear', { session_id: sid })
  }
  const toggleQueuePause = async (paused: boolean) => {
    const sid = sessionIdRef.current
    if (!sid) return
    // 恢复路径可能在服务端同步起轮，重拉状态让运行卡片即时浮现
    await postQueueAction('/api/session_inputs/pause', { session_id: sid, paused })
    void get().fetchState()
  }

  // 三态确认裁决：keep=原样补发；clear=先清空再补发；cancel=放弃（草稿留在输入框）
  const resolveQueueConfirm = async (choice: 'keep' | 'clear' | 'cancel') => {
    const pending = get().queueConfirm
    set({ queueConfirm: null })
    if (!pending || choice === 'cancel') return
    if (choice === 'clear') await clearQueue()
    await get().sendMessage(pending.prompt, pending.images, { skipConfirm: true })
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
  // opts.lastTurn：后端透出的最近一回合退出信息，重建时恢复真实退出原因与结束时间。
  const loadMessages = (rawMessages: Record<string, unknown>[], opts?: { runningStartedAt?: number; lastTurn?: TurnExitInfo }) => {
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
      // content 兼容字符串与 parts 数组；占位图片回落最近一次发送的缓存：
      // 数量对齐时按序号取（多图同 mime 不会错配），否则退到 mime 匹配
      const parts = extractContentParts(raw.content)
      const cache = lastSentImagesRef.current
      const images = parts.images.map((im, i) => {
        if (!im.dataUrl.includes('__omitted__')) return im
        const cached = cache.length === parts.images.length
          ? cache[i]
          : cache.find((c) => c.mime === im.mime)
        return cached ? { ...im, dataUrl: cached.dataUrl } : im
      })
      const content = parts.text

      // 压缩边界（role=system，[Compact Boundary …]）→ 重建分隔线，
      // 解析 pre-compact tokens 展示压缩前规模
      if (role === 'system' && content.startsWith('[Compact Boundary')) {
        if (currentBlock) {
          const m = /pre-compact tokens:\s*(\d+)/.exec(content)
          currentBlock.timeline.push({
            id: genId(),
            type: 'compact',
            compactStatus: 'done',
            tokensBefore: m ? Number(m[1]) : undefined,
            startTime: tsOf(raw),
            endTime: tsOf(raw),
          })
          currentBlock.endTime = tsOf(raw)
        }
        continue
      }
      // 压缩摘要消息（role=user 带 _compact_summary）→ 对模型可见、对前端隐藏
      if (role === 'user' && raw._compact_summary === true) {
        continue
      }

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
          userImages: images.length > 0 ? images : undefined,
          // skill：渐进披露重写提示 → 「技能徽章 + 任务描述」展示
          skillName: parsed.kind === 'skill' ? parsed.skillName : undefined,
          // 队列转向注入的历史消息：气泡挂「已引导对话」标（与直播分支同源）
          userSteered: raw._steer === true,
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

    // 异常回合兜底：最后一块时间线为空且无运行中任务，说明该轮在 assistant
    // 消息落库前就结束。先按 lastTurn 恢复真实退出原因与结束时间：仅当
    // user_ts 与最后一块 startTime 精确相等（后端从落库列表同源取值并做过
    // 归属确认）才可信；缺失/不匹配（旧数据、残留值、进程被强杀未落库）一律
    // 中性 no_output——不再复用 aborted，「用户主动停止」只可能来自真实停止。
    // 范围限定：时间线非空的最后一块不被 lastTurn 改写（与改造前一致）
    if (newBlocks.length > 0 && typeof runningStartedAt !== 'number') {
      const last = newBlocks[newBlocks.length - 1]
      const lastTurn = opts?.lastTurn
      const attributable =
        !!lastTurn && typeof lastTurn.user_ts === 'number' && lastTurn.user_ts === last.startTime
      if (lastTurn && attributable) {
        if (lastTurn.reason && lastTurn.reason !== 'completed') {
          last.exitReason = lastTurn.reason
        }
        // 真实结束时刻修掉重建恒 0 秒的假耗时（负值由下方统一钳制兜住）
        if (typeof lastTurn.finished_at === 'number') {
          last.endTime = lastTurn.finished_at
        }
      }
      if (last.timeline.length === 0) {
        const reason = lastTurn && attributable ? lastTurn.reason : ''
        if (reason === 'aborted') {
          // 真实停止：与直播 abort() 同款「已停止」形态
          last.timeline.push({ id: genId(), type: 'text', content: '已停止', open: false })
        } else {
          // 无可用原因、或 completed 却无落库产出的异常形态 → 中性 no_output
          if (!attributable || reason === 'completed') {
            last.exitReason = 'no_output'
          }
          last.timeline.push({ id: genId(), type: 'text', content: '本回合无输出', open: false })
        }
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
    queueItems: [],
    pendingGuides: [],
    queueState: { auto_drain: true, pause_reason: null },
    queueConfirm: null,
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
    reasoningLevel: null,
    setReasoningLevel: (level: string | null) => set({ reasoningLevel: level }),
    lastSendError: null,
    clearSendError: () => set({ lastSendError: null }),
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
    refreshQueue,
    cancelQueueItem,
    steerQueueItem,
    editQueueItem,
    reorderQueueItem,
    clearQueue,
    toggleQueuePause,
    resolveQueueConfirm,
  }
})
