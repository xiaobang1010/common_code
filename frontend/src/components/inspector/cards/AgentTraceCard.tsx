import { useEffect, useMemo, useRef, useState, memo } from 'react'
import { useChatStore } from '../../../stores/useChatStore'
import Markdown from '../../ai/Markdown'
import { VERB_BY_TOOL, extractObject, StepIcon, iconKind } from '../../ai/toolDisplay'

// 子代理任务条目（GET /api/subagents 返回体；registry 与 history 两路的公共字段）
interface AgentTaskInfo {
  agent_id: string
  agent_type: string
  description: string
  status: string
  mode: string
  promoted?: boolean
  created_at: number
  updated_at: number
  usage?: { total_tokens?: number; tool_uses?: number; duration_ms?: number }
  error?: string | null
  origin?: string
}

// 任务详情（GET /api/subagents/{id}；仅注册表内任务可得，历史任务 404）
interface AgentTaskDetail extends AgentTaskInfo {
  result_preview?: string
  budget?: { max_turns?: number | null; token_budget?: number | null; usage?: Record<string, number> }
}

const STATUS_LABEL: Record<string, string> = {
  pending: '等待中',
  running: '运行中',
  completed: '已完成',
  failed: '失败',
  aborted: '已中断',
  stopped: '已停止',
}

const STATUS_COLOR: Record<string, string> = {
  pending: 'var(--text-tertiary)',
  running: 'var(--accent)',
  completed: 'var(--success)',
  failed: 'var(--error)',
  aborted: 'var(--warning)',
  stopped: 'var(--text-tertiary)',
}

function statusLabel(status: string): string {
  return STATUS_LABEL[status] ?? '未知'
}

function statusColor(status: string): string {
  return STATUS_COLOR[status] ?? 'var(--text-tertiary)'
}

function isBusy(status: string): boolean {
  return status === 'running' || status === 'pending'
}

// 相对时间：列表项右侧的「x 分钟前」式轻提示
function relativeTime(epochSec: number): string {
  if (!epochSec) return ''
  const diff = Date.now() - epochSec * 1000
  if (diff < 0) return ''
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const h = Math.floor(min / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

// 耗时文案：优先用终态 usage 定稿值，运行中按创建时刻推算
function formatDurationMs(ms?: number): string {
  if (!ms || ms < 0) return '-'
  if (ms < 1000) return `${ms}ms`
  const s = Math.floor(ms / 1000)
  if (s < 60) return `${s}s`
  return `${Math.floor(s / 60)}m${s % 60}s`
}

interface AgentTraceCardProps {
  // App 层持有的选中任务（胶囊卡点入时写入）；列表内切换经 onSelectAgent 上报
  selectedAgentId: string | null
  onSelectAgent: (id: string) => void
  // 标签激活才发轮询请求：面板常挂载，隐藏时静默
  active: boolean
}

// ---------------------------------------------------------------------------
// 转录 → 轨迹步骤（纯函数，与渲染分离）
// ---------------------------------------------------------------------------

// transcript 端点返回的消息（get_agent_transcript 重建的标准消息 + timestamp）
interface TranscriptMessage {
  role: string
  content: string
  timestamp?: number | null
  tool_calls?: Array<{ id: string; function: { name: string; arguments: string } }>
  tool_call_id?: string
}

// 轨迹步骤：任务/上下文/正文/工具四类，按转录顺序排列
interface TraceStep {
  id: string
  kind: 'task' | 'context' | 'text' | 'tool'
  content?: string       // task/context/text 正文
  toolName?: string      // 仅 tool
  args?: string
  result?: string
  resultDone: boolean    // tool：结果是否已回填（未回填即仍在执行或被截断）
  offsetSec: number | null // 相对首条消息的秒数（旧转录无 timestamp 则为 null）
}

// 消息列表转轨迹步骤：user 建任务/上下文行（首条为任务，后续为队列注入的上下文），
// assistant 文本建正文行、tool_calls 逐个建工具行，tool 结果按 tool_call_id 回填。
// 转录侧已保证无悬挂调用（_filter_unresolved_tool_uses），回填不中的情况仅出现在拉取瞬间
function buildTraceSteps(messages: TranscriptMessage[]): TraceStep[] {
  const steps: TraceStep[] = []
  const byCallId = new Map<string, TraceStep>()
  const baseTs = messages.find((m) => typeof m.timestamp === 'number')?.timestamp ?? null
  let userCount = 0

  messages.forEach((m, i) => {
    const offsetSec =
      typeof m.timestamp === 'number' && baseTs !== null ? Math.max(0, Math.round(m.timestamp - baseTs)) : null

    if (m.role === 'user') {
      if (!(m.content ?? '').trim()) return
      userCount += 1
      steps.push({
        id: `u${i}`,
        kind: userCount === 1 ? 'task' : 'context',
        content: m.content,
        offsetSec,
        resultDone: false,
      })
      return
    }

    if (m.role === 'assistant') {
      if ((m.content ?? '').trim()) {
        steps.push({ id: `a${i}`, kind: 'text', content: m.content, offsetSec, resultDone: false })
      }
      for (const tc of m.tool_calls ?? []) {
        const step: TraceStep = {
          id: `t${i}-${tc.id}`,
          kind: 'tool',
          toolName: tc.function?.name ?? 'unknown',
          args: tc.function?.arguments ?? '',
          offsetSec,
          resultDone: false,
        }
        steps.push(step)
        byCallId.set(tc.id, step)
      }
      return
    }

    if (m.role === 'tool') {
      const step = m.tool_call_id ? byCallId.get(m.tool_call_id) : undefined
      if (step) {
        step.result = m.content ?? ''
        step.resultDone = true
      }
    }
  })

  return steps
}

// 行尾相对时间标签（+Ns）；旧转录无 timestamp 时不显示
function OffsetTag({ offsetSec }: { offsetSec: number | null }) {
  if (offsetSec === null) return null
  return (
    <span
      style={{
        marginLeft: 'auto',
        flexShrink: 0,
        fontSize: '10px',
        color: 'var(--text-tertiary)',
        fontFamily: 'var(--font-mono)',
      }}
    >
      +{offsetSec}s
    </span>
  )
}

// 任务/上下文行：默认收起只显一行摘要，点击展开全文（任务描述与队列注入的上下文）
function ContextRow({ step }: { step: TraceStep }) {
  const [expanded, setExpanded] = useState(false)
  const label = step.kind === 'task' ? '任务' : '上下文'
  const preview = (step.content ?? '').split('\n')[0]
  return (
    <div style={{ padding: '3px 12px' }}>
      <div
        onClick={() => setExpanded((v) => !v)}
        title={expanded ? '收起' : '展开全文'}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          cursor: 'pointer',
          fontSize: '11px',
          fontFamily: 'var(--font-ui)',
          color: 'var(--text-secondary)',
          borderRadius: 'var(--radius-sm)',
        }}
      >
        <span style={{ color: 'var(--text-tertiary)', display: 'flex', flexShrink: 0 }}>
          <StepIcon kind={step.kind === 'task' ? 'bot' : 'list'} />
        </span>
        <span style={{ flexShrink: 0, color: 'var(--text-tertiary)' }}>{label}</span>
        {!expanded && (
          <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-tertiary)' }}>
            {preview}
          </span>
        )}
        {expanded && <span style={{ flex: 1 }} />}
        <OffsetTag offsetSec={step.offsetSec} />
        <span style={{ color: 'var(--text-tertiary)', fontSize: '10px', flexShrink: 0 }}>{expanded ? '▾' : '▸'}</span>
      </div>
      {expanded && (
        <div
          style={{
            marginTop: '4px',
            marginLeft: '18px',
            fontSize: '11px',
            lineHeight: '17px',
            color: 'var(--text-secondary)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            borderLeft: '2px solid var(--border-subtle)',
            paddingLeft: '8px',
          }}
        >
          {step.content}
        </div>
      )}
    </div>
  )
}

// 正文行：assistant 文本走与消息流一致的 Markdown 渲染
const TraceTextRow = memo(function TraceTextRow({ step }: { step: TraceStep }) {
  return (
    <div style={{ padding: '4px 12px', fontSize: '12px' }}>
      <Markdown content={step.content ?? ''} />
    </div>
  )
})

// args JSON 美化：可解析则缩进两格，不可解析原样展示
function prettyArgs(args?: string): string {
  if (!args) return ''
  try {
    return JSON.stringify(JSON.parse(args), null, 2)
  } catch {
    return args
  }
}

// 工具行：短分类标签 + 对象名摘要（口径同消息流时间线），点击展开参数与结果。
// 只读展示：无任何操作入口；结果未回填时显示等待提示
const TraceToolRow = memo(function TraceToolRow({ step }: { step: TraceStep }) {
  const [expanded, setExpanded] = useState(false)
  const known = VERB_BY_TOOL[step.toolName?.toLowerCase() ?? '']
  const verb = known ?? '已执行'
  const objectText = extractObject(step.args ?? '') ?? (known ? null : step.toolName)
  const isError = step.toolName === 'error'
  const pending = !step.resultDone
  const clickable = !!(step.args || step.result) || pending
  const rowColor = isError ? 'var(--error)' : 'var(--text-secondary)'

  return (
    <div style={{ padding: '3px 12px' }}>
      <div
        onClick={clickable ? () => setExpanded((v) => !v) : undefined}
        title={clickable ? (expanded ? '收起详情' : '展开参数与结果') : undefined}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          cursor: clickable ? 'pointer' : 'default',
          fontSize: '11px',
          fontFamily: 'var(--font-ui)',
          color: rowColor,
          borderRadius: 'var(--radius-sm)',
        }}
      >
        <span style={{ color: isError ? 'var(--error)' : 'var(--text-tertiary)', display: 'flex', flexShrink: 0 }}>
          <StepIcon kind={iconKind(step.toolName ?? '')} />
        </span>
        <span style={{ flexShrink: 0 }}>{verb}</span>
        {objectText && (
          <span
            style={{
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-tertiary)',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              flex: 1,
              minWidth: 0,
            }}
          >
            {objectText}
          </span>
        )}
        {!objectText && <span style={{ flex: 1 }} />}
        {pending && <span style={{ flexShrink: 0, color: 'var(--text-tertiary)' }}>等待结果...</span>}
        <OffsetTag offsetSec={step.offsetSec} />
        {clickable && (
          <span style={{ color: 'var(--text-tertiary)', fontSize: '10px', flexShrink: 0 }}>{expanded ? '▾' : '▸'}</span>
        )}
      </div>
      {expanded && (
        <div
          style={{
            marginTop: '4px',
            marginLeft: '18px',
            display: 'flex',
            flexDirection: 'column',
            gap: '6px',
            borderLeft: '2px solid var(--border-subtle)',
            paddingLeft: '8px',
          }}
        >
          {step.args && (
            <div>
              <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-ui)', marginBottom: '2px' }}>参数</div>
              <pre
                style={{
                  margin: 0,
                  fontSize: '10px',
                  lineHeight: '15px',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-secondary)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  maxHeight: '200px',
                  overflowY: 'auto',
                }}
              >
                {prettyArgs(step.args)}
              </pre>
            </div>
          )}
          {step.resultDone && (
            <div>
              <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-ui)', marginBottom: '2px' }}>结果</div>
              <pre
                style={{
                  margin: 0,
                  fontSize: '10px',
                  lineHeight: '15px',
                  fontFamily: 'var(--font-mono)',
                  color: 'var(--text-secondary)',
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  maxHeight: '240px',
                  overflowY: 'auto',
                }}
              >
                {step.result}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  )
})

// 智能体卡：左列当前会话的子代理任务列表（运行中置顶），右侧只读执行轨迹。
// 数据来自 /api/subagents 系列端点；列表轮询 3s，选中任务运行中轨迹轮询 2s，终态停。
function AgentTraceCard({ selectedAgentId, onSelectAgent, active }: AgentTraceCardProps) {
  const sessionId = useChatStore((s) => s.sessionId)
  const [tasks, setTasks] = useState<AgentTaskInfo[]>([])
  const [detail, setDetail] = useState<AgentTaskDetail | null>(null)
  const [listLoaded, setListLoaded] = useState(false)
  // 选中任务的轨迹数据：null=尚未加载，[]+missing=转录不存在
  const [messages, setMessages] = useState<TranscriptMessage[] | null>(null)
  const [traceMissing, setTraceMissing] = useState(false)
  const listReqSeq = useRef(0)
  // 轨迹滚动容器与跟随态：用户上滚即暂停跟随，滚回底部恢复
  const scrollRef = useRef<HTMLDivElement>(null)
  const followRef = useRef(true)

  // 选中任务落位：显式选中在列表里就用它；否则默认第一个运行中，再不行第一个。
  // 派生值不上报 App，避免选中回写造成渲染环路
  const firstRunning = tasks.find((t) => isBusy(t.status))
  const effectiveSelectedId =
    selectedAgentId && tasks.some((t) => t.agent_id === selectedAgentId)
      ? selectedAgentId
      : (firstRunning ?? tasks[0])?.agent_id ?? null

  // 会话变化：清掉上一会话的列表，重取完成前不显示旧数据
  useEffect(() => {
    setTasks([])
    setListLoaded(false)
  }, [sessionId])

  // 任务列表轮询：标签激活且已有会话才发（面板常挂载，隐藏时静默）；
  // sessionId 为 null 时空态不发请求（空串会让后端不过滤而串出全部会话的任务）
  useEffect(() => {
    if (!sessionId || !active) return
    let cancelled = false
    async function poll() {
      if (cancelled) return
      const seq = ++listReqSeq.current
      try {
        const resp = await fetch(`/api/subagents?session_id=${encodeURIComponent(sessionId ?? '')}`)
        if (!resp.ok) return
        const data = await resp.json()
        if (cancelled || seq !== listReqSeq.current) return
        const list = (data.subagents ?? []) as AgentTaskInfo[]
        // 运行中置顶，组内按最近活动倒序；终态任务也保留，供回看轨迹
        const running = list.filter((t) => isBusy(t.status)).sort((a, b) => b.updated_at - a.updated_at)
        const done = list.filter((t) => !isBusy(t.status)).sort((a, b) => b.updated_at - a.updated_at)
        setTasks([...running, ...done])
        setListLoaded(true)
      } catch {
        // 网络异常下一轮自然重试
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [sessionId, active])

  const selectedTask = tasks.find((t) => t.agent_id === effectiveSelectedId) ?? null

  // 选中变化：清掉上一个任务的轨迹数据，回到跟随滚动
  useEffect(() => {
    setDetail(null)
    setMessages(null)
    setTraceMissing(false)
    followRef.current = true
  }, [effectiveSelectedId])

  // 轨迹与详情轮询：标签激活才发（面板常挂载，隐藏时静默）；
  // 选中任务运行中每 2s 重拉，终态拉一次定稿。历史任务详情 404 属预期
  //（注册表重启即空，头部回退列表条目）；transcript 404 → 空轨迹兜底文案
  useEffect(() => {
    if (!effectiveSelectedId || !active) return
    const busy = selectedTask ? isBusy(selectedTask.status) : false
    let cancelled = false
    let timer: number | undefined
    async function poll() {
      if (cancelled) return
      try {
        const [tResp, dResp] = await Promise.all([
          fetch(`/api/subagents/${effectiveSelectedId}/transcript`),
          fetch(`/api/subagents/${effectiveSelectedId}`),
        ])
        if (cancelled) return
        if (tResp.ok) {
          const data = await tResp.json()
          setMessages((data.messages ?? []) as TranscriptMessage[])
          setTraceMissing(false)
        } else if (tResp.status === 404) {
          setTraceMissing(true)
          setMessages([])
        }
        if (dResp.ok) setDetail(await dResp.json())
      } catch {
        // 网络异常下一轮自然重试
      }
      if (!cancelled && busy) timer = window.setTimeout(poll, 2000)
    }
    void poll()
    return () => {
      cancelled = true
      if (timer) window.clearTimeout(timer)
    }
  }, [effectiveSelectedId, active, selectedTask?.status])

  const steps = useMemo(() => (messages ? buildTraceSteps(messages) : []), [messages])

  // 新内容到达且处于跟随态时滚到底部；用户上滚（离开底部附近）即暂停跟随
  useEffect(() => {
    const el = scrollRef.current
    if (!el || !followRef.current) return
    el.scrollTop = el.scrollHeight
  }, [steps.length])

  const handleTraceScroll = () => {
    const el = scrollRef.current
    if (!el) return
    followRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 48
  }

  // 轨迹区：加载中 / 空态兜底 / 按序渲染四类步骤
  const traceNode = (() => {
    if (!selectedTask) return null
    if (messages === null && !traceMissing) {
      return (
        <div style={{ padding: '12px', fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-ui)' }}>
          轨迹加载中...
        </div>
      )
    }
    if (traceMissing || steps.length === 0) {
      return (
        <div style={{ padding: '12px', fontSize: '11px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-ui)' }}>
          暂无执行轨迹（转录记录不存在或为空）
        </div>
      )
    }
    return steps.map((step) => {
      if (step.kind === 'tool') return <TraceToolRow key={step.id} step={step} />
      if (step.kind === 'text') return <TraceTextRow key={step.id} step={step} />
      return <ContextRow key={step.id} step={step} />
    })
  })()

  // 空态：无会话 / 会话内无任何子代理任务
  if (!sessionId || (!selectedTask && listLoaded && tasks.length === 0)) {
    return (
      <div style={{ display: 'flex', height: '100%', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ fontSize: '12px', color: 'var(--text-tertiary)', fontFamily: 'var(--font-ui)' }}>
          {sessionId ? '当前会话暂无智能体任务' : '尚未进入会话'}
        </span>
      </div>
    )
  }

  // 详情头部数据源：注册表内任务用详情接口（多预算字段），历史任务 404 时回退列表条目
  const header = detail ?? selectedTask
  const elapsedMs = header
    ? isBusy(header.status)
      ? Math.max(0, Date.now() - header.created_at * 1000)
      : header.usage?.duration_ms
    : undefined

  return (
    <div style={{ display: 'flex', height: '100%', minHeight: 0 }}>
      {/* 左列：任务列表 */}
      <div
        style={{
          width: '200px',
          flexShrink: 0,
          borderRight: '1px solid var(--border-subtle)',
          overflowY: 'auto',
          padding: '6px',
          display: 'flex',
          flexDirection: 'column',
          gap: '2px',
        }}
      >
        {tasks.map((t) => {
          const selected = t.agent_id === effectiveSelectedId
          return (
            <div
              key={t.agent_id}
              onClick={() => onSelectAgent(t.agent_id)}
              title={t.description}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '5px 8px',
                borderRadius: 'var(--radius-sm)',
                cursor: 'pointer',
                backgroundColor: selected ? 'var(--selected-bg)' : 'transparent',
                transition: 'all var(--transition-fast)',
              }}
            >
              <span
                style={{
                  width: '6px',
                  height: '6px',
                  borderRadius: '50%',
                  flexShrink: 0,
                  backgroundColor: statusColor(t.status),
                  animation: isBusy(t.status) ? 'breathe 1.6s ease-in-out infinite' : undefined,
                }}
              />
              <span
                style={{
                  flex: 1,
                  fontSize: '11px',
                  fontFamily: 'var(--font-ui)',
                  color: selected ? 'var(--text-primary)' : 'var(--text-secondary)',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {t.description || '(未命名子任务)'}
              </span>
              <span style={{ fontSize: '10px', color: 'var(--text-tertiary)', flexShrink: 0 }}>
                {relativeTime(t.updated_at)}
              </span>
            </div>
          )
        })}
      </div>

      {/* 右侧：详情头部 + 轨迹 */}
      <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
        {header && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              padding: '8px 12px',
              borderBottom: '1px solid var(--border-subtle)',
              flexShrink: 0,
              fontSize: '11px',
              fontFamily: 'var(--font-ui)',
              color: 'var(--text-tertiary)',
              flexWrap: 'wrap',
            }}
          >
            <span style={{ display: 'flex', alignItems: 'center', gap: '5px' }}>
              <span
                style={{
                  width: '6px',
                  height: '6px',
                  borderRadius: '50%',
                  backgroundColor: statusColor(header.status),
                  animation: isBusy(header.status) ? 'breathe 1.6s ease-in-out infinite' : undefined,
                }}
              />
              <span style={{ color: 'var(--text-secondary)' }}>{statusLabel(header.status)}</span>
            </span>
            {header.agent_type && <span>{header.agent_type}</span>}
            <span>耗时 {formatDurationMs(elapsedMs)}</span>
            <span>tokens {header.usage?.total_tokens ?? '-'}</span>
            <span>工具 {header.usage?.tool_uses ?? '-'}</span>
            {header.mode === 'background' && <span>{header.promoted ? '已转后台' : '后台'}</span>}
            {/* 预算护栏仅注册表内任务可得（详情接口独有），历史任务不显示 */}
            {detail?.budget?.max_turns ? <span>轮次上限 {detail.budget.max_turns}</span> : null}
            {detail?.budget?.token_budget ? <span>预算 {detail.budget.token_budget}</span> : null}
            {header.error && <span style={{ color: 'var(--error)' }}>原因：{header.error}</span>}
          </div>
        )}
        <div ref={scrollRef} onScroll={handleTraceScroll} style={{ flex: 1, minHeight: 0, overflowY: 'auto', paddingBottom: '8px' }}>
          {traceNode}
        </div>
      </div>
    </div>
  )
}

export default AgentTraceCard
