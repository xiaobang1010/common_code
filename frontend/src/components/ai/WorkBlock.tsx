import { useState, useEffect, memo, useCallback, type ReactNode } from 'react'
import { useChatStore, formatDuration, lastActivityAtRef, type WorkBlock, type TimelineItem } from '../../stores/useChatStore'
import SubagentCard from './SubagentCard'
import Markdown from './Markdown'

// ---------- 用户消息中的文件引用渲染 ----------
// 输入框发送时把内联 chip 序列化为 [文件名](./工作区相对路径) 的 Markdown
// 链接，气泡里再还原成文件 chip。仅当链接 URL 以 ./ 开头才认定为文件引用，
// 外链等普通 Markdown 链接不受影响
const FILE_REF_RE = /\[([^\]]+)\]\((\.\/[^)]+)\)/g

function renderUserMessage(text: string): ReactNode[] {
  const nodes: ReactNode[] = []
  let last = 0
  let m: RegExpExecArray | null
  FILE_REF_RE.lastIndex = 0
  while ((m = FILE_REF_RE.exec(text))) {
    const [, label, url] = m
    const path = url.slice(2)
    if (m.index > last) nodes.push(text.slice(last, m.index))
    nodes.push(
      <span key={`ref-${m.index}`} className="chat-ref-chip" title={path}>
        <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
          <path d="M14 2v6h6" />
        </svg>
        <span>{label}</span>
      </span>,
    )
    last = m.index + m[0].length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

interface Props {
  // 只订阅自己的工作块：流式更新只触发本组件重渲
  blockId: string
}

// ---------- 工具行分类标签（toolName 小写归一后匹配，未知工具兜底「已执行」+ 原名） ----------
const VERB_BY_TOOL: Record<string, string> = {
  bash: '终端',
  read: '读取',
  write: '写入',
  edit: '修改',
  grep: '搜索',
  glob: '查找',
  askuserquestion: '提问',
  skill: '技能',
  agent: '子任务',
  sendmessage: '发送',
  teamcreate: '建团队',
  taskcreate: '建任务',
  taskupdate: '改任务',
  tasklist: '查任务',
  taskget: '查任务',
  summarizeteam: '汇总',
  error: '出错',
}

// 正常结束的退出原因；其余视为异常，需要弱提示与原因说明
const NORMAL_EXITS = new Set(['', 'completed', 'command'])

// 异常退出原因 → 行尾弱提示 / 展开首行原因（error 的展开首行附带错误步骤摘要，见 exitReasonLine）
// 约定：aborted 仅由真实停止操作写入（前端 abort() 与后端 abort_event 判定两处），
// 异常断流/无输出兜底走 stream_lost / no_output，不再冒用「用户主动停止」
const EXIT_HINT: Record<string, string> = {
  aborted: '已中断',
  error: '出错',
  model_error: '模型出错',
  prompt_too_long: '输入过长',
  max_output_tokens_exhausted: '输出超限',
  stream_lost: '连接中断',
  no_output: '无输出',
}
const EXIT_REASON: Record<string, string> = {
  aborted: '已中断：用户主动停止',
  model_error: '模型出错',
  prompt_too_long: '输入过长',
  max_output_tokens_exhausted: '输出超限',
  stream_lost: '已中断：连接断开，未收到回合结果',
  no_output: '已中断：本回合无输出（历史数据无退出原因）',
}

// 展开区首行原因：error 附错误步骤的 result 摘要；未知原因原样展示
function exitReasonLine(block: WorkBlock): string {
  const reason = block.exitReason ?? ''
  if (NORMAL_EXITS.has(reason)) return ''
  if (reason === 'error') {
    const detail = block.timeline.find(s => s.toolName === 'error')?.result?.replace(/^错误:\s*/, '').trim()
    return detail ? `出错：${detail}` : '出错'
  }
  return EXIT_REASON[reason] ?? reason
}

// 事件行图标按语义分组复用，单色（外层 currentColor 决定：错误红、其余中性灰）
const ICON_PATHS: Record<string, React.ReactNode> = {
  file: (<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z" /><path d="M14 2v6h6" /></>),
  search: (<><circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" /></>),
  pencil: (<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />),
  filePlus: (<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z" /><path d="M14 2v6h6M12 18v-6M9 15h6" /></>),
  terminal: (<><path d="M4 17l6-6-6-6" /><path d="M12 19h8" /></>),
  zap: (<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />),
  help: (<><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><path d="M12 17h.01" /></>),
  bot: (<><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M12 8V4" /><path d="M8 13h.01M16 13h.01" /></>),
  send: (<><path d="M22 2L11 13" /><path d="M22 2l-7 20-4-9-9-4z" /></>),
  users: (<><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>),
  list: (<><path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" /></>),
  alert: (<><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></>),
  brain: (
    <>
      <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" />
      <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" />
      <path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4" />
    </>
  ),
  dot: (<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />),
}

function iconKind(toolName: string): string {
  const n = toolName.toLowerCase()
  if (n === 'bash') return 'terminal'
  if (n === 'read') return 'file'
  if (n === 'grep' || n === 'glob') return 'search'
  if (n === 'write') return 'filePlus'
  if (n === 'edit') return 'pencil'
  if (n === 'skill') return 'zap'
  if (n === 'askuserquestion') return 'help'
  if (n === 'agent') return 'bot'
  if (n === 'sendmessage') return 'send'
  if (n === 'teamcreate') return 'users'
  if (n === 'taskcreate' || n === 'taskupdate' || n === 'tasklist' || n === 'taskget' || n === 'summarizeteam') return 'list'
  if (n === 'error') return 'alert'
  return 'dot'
}

function StepIcon({ kind }: { kind: string }) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
      {ICON_PATHS[kind] ?? ICON_PATHS.dot}
    </svg>
  )
}

// 从 args JSON 提取首个可读参数（路径/命令/问题等）作为事件行对象名
function extractObject(args: string): string | null {
  if (!args) return null
  try {
    const parsed = JSON.parse(args)
    if (parsed && typeof parsed === 'object') {
      for (const value of Object.values(parsed)) {
        if (typeof value === 'string' && value.trim()) return value.trim()
        if (typeof value === 'number') return String(value)
      }
    }
    return null
  } catch {
    return null
  }
}

// 中间截断：超长路径两端保留，完整内容放 title
function truncateMiddle(text: string, max = 48): string {
  if (text.length <= max) return text
  const keep = Math.floor((max - 1) / 2)
  return `${text.slice(0, keep)}…${text.slice(-keep)}`
}

// 文件类工具：事件行按「文件名 + 目录 + 变更统计」排布
const FILE_TOOLS = new Set(['read', 'write', 'edit'])

// 从 args 提取 file_path（文件类工具的路径参数名统一为 file_path）
function extractFilePath(args: string): string | null {
  if (!args) return null
  try {
    const parsed = JSON.parse(args)
    const p = parsed?.file_path
    return typeof p === 'string' && p.trim() ? p.trim() : null
  } catch {
    return null
  }
}

// 路径拆文件名与目录：文件名亮色不截断，目录暗色可截断
function splitPath(p: string): { name: string; dir: string } {
  const idx = Math.max(p.lastIndexOf('/'), p.lastIndexOf('\\'))
  return idx >= 0 ? { name: p.slice(idx + 1), dir: p.slice(0, idx + 1) } : { name: p, dir: '' }
}

// 从结果文本提取变更统计：edit「+a -r 行」、write「+a 行」（后端 format_model_content 产出）
function extractChangeStat(result: string | undefined): { added: number; removed: number } | null {
  if (!result) return null
  const both = result.match(/\+(\d+)\s*-\s*(\d+)\s*行/)
  if (both) return { added: Number(both[1]), removed: Number(both[2]) }
  const only = result.match(/\+\s*(\d+)\s*行/)
  return only ? { added: Number(only[1]), removed: 0 } : null
}

// 工具步骤事件行：单行低调行（灰图标 + 分类标签 + 等宽对象名 + 状态位），点击展开详情
// memo：步骤对象引用稳定时跳过重渲，避免父块更新时全部步骤重绘
const EventLine = memo(function EventLine({ step }: { step: TimelineItem }) {
  const [expanded, setExpanded] = useState(false)
  const isError = step.toolName === 'error'
  const isRunning = !!step.isRunning && !isError
  const known = VERB_BY_TOOL[step.toolName?.toLowerCase() ?? '']
  const verb = known ?? '已执行'
  // 文件类工具按「文件名+目录+统计」排布；其余仍取 args 首个可读参数
  const filePath = !isError && FILE_TOOLS.has(step.toolName?.toLowerCase() ?? '') ? extractFilePath(step.args ?? '') : null
  const stat = filePath ? extractChangeStat(step.result) : null
  const pathParts = filePath ? splitPath(filePath) : null
  // 错误步骤展示错误摘要；已知工具展示 args 提取的对象名；未知工具以原始 toolName 兜底
  const objectText = isError
    ? (step.result || '').replace(/^错误:\s*/, '')
    : pathParts
      ? null
      : extractObject(step.args ?? '') ?? (known ? null : step.toolName)
  // 可展开条件：有详情内容，或运行中（可看「等待结果...」占位）。
  // 思考已独立成行（ReasoningRow），展开区不再承载 reasoning
  const clickable = !!(step.args || step.result) || isRunning
  const rowColor = isError ? 'var(--error)' : 'var(--text-tertiary)'

  const row = (
    <>
      {isRunning ? (
        <svg className="work-spin" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}>
          <path d="M21 12a9 9 0 1 1-6.219-8.56" />
        </svg>
      ) : (
        <StepIcon kind={iconKind(step.toolName ?? '')} />
      )}
      <span style={{ flexShrink: 0 }}>{verb}</span>
      {pathParts ? (
        <>
          <span title={filePath ?? undefined} style={{ fontFamily: 'var(--font-mono)', color: rowColor === 'var(--error)' ? rowColor : 'var(--text-secondary)', flexShrink: 0 }}>
            {pathParts.name}
          </span>
          {pathParts.dir && (
            <span
              title={filePath ?? undefined}
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
              {truncateMiddle(pathParts.dir)}
            </span>
          )}
          {stat && (
            <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', flexShrink: 0 }}>
              <span style={{ color: 'var(--success)' }}>+{stat.added}</span>
              {stat.removed > 0 && <span style={{ color: 'var(--error)', marginLeft: '6px' }}>-{stat.removed}</span>}
            </span>
          )}
        </>
      ) : objectText && (
        <span
          title={objectText}
          style={{
            fontFamily: 'var(--font-mono)',
            color: isError ? 'var(--error)' : 'var(--text-secondary)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            flex: 1,
            minWidth: 0,
          }}
        >
          {truncateMiddle(objectText)}
        </span>
      )}
      {isError && <span style={{ marginLeft: 'auto', flexShrink: 0 }}>失败</span>}
      {clickable && (
        <span style={{ marginLeft: isError ? 0 : 'auto', flexShrink: 0, fontSize: '10px' }}>
          {expanded ? '▾' : '▸'}
        </span>
      )}
    </>
  )

  const rowStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    width: '100%',
    padding: '3px 0',
    background: 'transparent',
    border: 'none',
    fontSize: '12px',
    fontFamily: 'var(--font-ui)',
    color: rowColor,
    textAlign: 'left',
    userSelect: 'text',
    borderRadius: 'var(--radius-sm)',
    cursor: clickable ? 'pointer' : 'default',
  }

  // Agent 步骤：事件行下方渲染独立状态卡片（状态/耗时/usage/输出预览/停止）
  const isAgentStep = step.toolName === 'Agent' || step.toolName === 'Task'

  return (
    <div>
      {clickable ? (
        <button
          className="work-row"
          type="button"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
          style={rowStyle}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--hover-bg)')}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
        >
          {row}
        </button>
      ) : (
        <div style={rowStyle}>{row}</div>
      )}

      {isAgentStep && (
        <div style={{ padding: '0 0 6px 20px' }}>
          <SubagentCard step={step} />
        </div>
      )}

      {expanded && (
        <div style={{ padding: '2px 0 8px 20px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {step.args && (
            <div>
              <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '2px', letterSpacing: '1px', textTransform: 'uppercase' }}>
                参数
              </div>
              <pre style={{ margin: 0, fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {step.args}
              </pre>
            </div>
          )}
          {step.result && (
            <div>
              <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '2px', letterSpacing: '1px', textTransform: 'uppercase' }}>
                结果
              </div>
              <pre style={{ margin: 0, fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: '200px', overflow: 'auto' }}>
                {step.result}
              </pre>
            </div>
          )}
          {isRunning && !step.result && (
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
              等待结果...
            </div>
          )}
        </div>
      )}
    </div>
  )
})

// 思考行：脑图标 + 「思考 · X秒」，点击展开思维链全文。
// 流式期间（open）显示「思考中 · X秒」并 1s tick 递增——tick 收敛在本组件内，
// 关闭后定时器随之停止，不影响其余时间线行
const ReasoningRow = memo(function ReasoningRow({ item }: { item: TimelineItem }) {
  const [expanded, setExpanded] = useState(false)
  const isStreaming = !!item.open
  const [, force] = useState(0)
  useEffect(() => {
    if (!isStreaming) return
    const timer = setInterval(() => force((n) => n + 1), 1000)
    return () => clearInterval(timer)
  }, [isStreaming])

  const duration = (item.endTime || Date.now()) - (item.startTime || Date.now())
  const content = item.content || ''
  const clickable = !!content

  const rowStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'center',
    gap: '8px',
    width: '100%',
    padding: '3px 0',
    background: 'transparent',
    border: 'none',
    fontSize: '12px',
    fontFamily: 'var(--font-ui)',
    color: 'var(--text-tertiary)',
    textAlign: 'left',
    userSelect: 'text',
    borderRadius: 'var(--radius-sm)',
    cursor: clickable ? 'pointer' : 'default',
  }

  const row = (
    <>
      {isStreaming ? (
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}>
          <path d="M21 12a9 9 0 1 1-6.219-8.56" />
        </svg>
      ) : (
        <StepIcon kind="brain" />
      )}
      <span style={{ flexShrink: 0 }}>{isStreaming ? '思考中' : '思考'}</span>
      <span style={{ fontFamily: 'var(--font-mono)', flexShrink: 0 }}>· {formatDuration(duration)}</span>
      {clickable && (
        <span style={{ marginLeft: 'auto', flexShrink: 0, fontSize: '10px' }}>
          {expanded ? '▾' : '▸'}
        </span>
      )}
    </>
  )

  return (
    <div>
      {clickable ? (
        <button
          className="work-row"
          type="button"
          onClick={() => setExpanded(!expanded)}
          aria-expanded={expanded}
          style={rowStyle}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--hover-bg)')}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
        >
          {row}
        </button>
      ) : (
        <div style={rowStyle}>{row}</div>
      )}
      {expanded && content && (
        <div style={{
          padding: '2px 0 8px 20px',
          fontSize: '11px',
          color: 'var(--text-tertiary)',
          lineHeight: 1.6,
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-word',
          maxHeight: '300px',
          overflow: 'auto',
        }}>
          {content}
        </div>
      )}
    </div>
  )
})

// 正文行：过渡叙述与最终回复同为一等事件，统一走 Markdown（streamdown）渲染。
// 流式中（open）由 streamdown 修复未闭合语法并显示跟随末行的光标，完成后自然定格，
// 无渲染管线切换
const TextItemView = memo(function TextItemView({ item }: { item: TimelineItem }) {
  const streaming = !!item.open

  if (!item.content) return null

  return (
    <div
      style={{
        // 通栏铺满：fit-content 会让代码块/表格跟着文字宽度收缩，
        // 参考效果里卡片始终撑满可读列
        alignSelf: 'stretch',
        // 14px/1.6：与主流客户端正文实测对齐；中文回落到雅黑渲染，
        // 字面本就偏大，再放大字号会明显抢版面
        fontSize: '14px',
        lineHeight: 1.6,
        color: 'var(--text-primary)',
        wordBreak: 'break-word',
      }}
    >
      <Markdown content={item.content} streaming={streaming} />
    </div>
  )
})

// 状态行：主行（工作中/已工作 + 耗时）+ 细分隔线 + 活动行。
// 1s tick 收敛在本组件内：时间刷新与 idleMs 重算共用，事件行列表与正文行不随 tick 重渲
const StatusLine = memo(function StatusLine({ block, expanded, onToggle }: {
  block: WorkBlock
  expanded: boolean
  onToggle: (() => void) | null
}) {
  // 本地 1s tick：仅运行中的回合刷新
  const [, force] = useState(0)
  useEffect(() => {
    if (block.status === 'running') {
      const timer = setInterval(() => force((n) => n + 1), 1000)
      return () => clearInterval(timer)
    }
  }, [block.status, block.startTime])

  const isRunning = block.status === 'running'
  const duration = (block.endTime || Date.now()) - block.startTime
  const idleMs = Date.now() - lastActivityAtRef.current

  // 行尾弱提示：仅异常结束显示，收敛但不消失
  const hint = !isRunning && block.exitReason && !NORMAL_EXITS.has(block.exitReason)
    ? EXIT_HINT[block.exitReason] ?? block.exitReason
    : ''

  // 活动行：长时间无响应 > 工具执行 > 生成回复 > 阶段事件 > 等待。
  // idle 分级：思考间隙超 10s 属正常（模型慢，不误报连接异常）；
  // 超 60s 才升级为疑似连接异常的强提示
  const idleWarn = isRunning && idleMs > 60000
  const activity = (() => {
    if (!isRunning) return ''
    if (idleMs > 60000) return '长时间无响应，可能连接异常，可在输入区停止后重试'
    if (idleMs > 10000) return '模型响应较慢，可继续浏览其他区域'
    const runningStep = block.timeline.find(s => s.isRunning)
    if (runningStep) return `正在执行工具 ${runningStep.toolName}`
    const lastItem = block.timeline[block.timeline.length - 1]
    if (lastItem?.type === 'text' && lastItem.open) return '正在生成回复'
    // 逐次重试反馈：两条协议路径的重试 phase 事件都带「正在重试 n/total」，原样透出
    if (block.phase?.includes('正在重试')) return block.phase
    if (block.phase === 'model_requested') return '正在调用模型'
    if (block.phase === 'memory_ready') return '已加载上下文'
    return '等待模型响应'
  })()

  const rowStyle: React.CSSProperties = {
    display: 'flex',
    alignItems: 'baseline',
    gap: '8px',
    width: '100%',
    padding: '2px 0 6px',
    background: 'transparent',
    border: 'none',
    fontSize: '12px',
    fontFamily: 'var(--font-ui)',
    color: 'var(--text-secondary)',
    textAlign: 'left',
    userSelect: 'text',
    borderRadius: 'var(--radius-sm)',
    cursor: onToggle ? 'pointer' : 'default',
  }
  const mainRow = (
    <>
      <span style={{ fontWeight: 500 }}>{isRunning ? '工作中' : '已工作'}</span>
      <span style={{ fontFamily: 'var(--font-mono)', color: 'var(--text-tertiary)' }}>{formatDuration(duration)}</span>
      {hint && <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>· {hint}</span>}
      {onToggle && (
        <span style={{ marginLeft: 'auto', color: 'var(--text-tertiary)', fontSize: '10px' }}>
          {expanded ? '▾' : '▸'}
        </span>
      )}
    </>
  )

  return (
    <div>
      {onToggle ? (
        <button
          className="work-row"
          type="button"
          onClick={onToggle}
          aria-expanded={expanded}
          style={rowStyle}
          onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--hover-bg)')}
          onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
        >
          {mainRow}
        </button>
      ) : (
        <div style={rowStyle}>{mainRow}</div>
      )}
      <div style={{ borderBottom: '1px solid var(--border-subtle)' }} />
      {activity && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px', padding: '5px 0 8px', fontSize: '11px', color: idleWarn ? 'var(--error)' : 'var(--text-tertiary)', fontFamily: 'var(--font-ui)' }}>
          <span className="work-pulse" style={{ width: '6px', height: '6px', borderRadius: '50%', backgroundColor: idleWarn ? 'var(--error)' : 'var(--text-tertiary)', animation: 'breathe 1.6s ease-in-out infinite', flexShrink: 0 }} />
          {/* role=status 提供隐式 aria-live=polite：阶段文案变化才播报，逐秒计时不播报 */}
          <span role="status">{activity}</span>
        </div>
      )}
    </div>
  )
})

function WorkBlockView({ blockId }: Props) {
  // 局部订阅：只监听自己的工作块，其他 block 更新时不重渲
  const block = useChatStore(s => s.blocksById[blockId])
  // 展开语义：是否显示过程行（reasoning/tool）；正文行恒可见。
  // 完成后默认平铺整个时间线，点状态行可折叠过程行降噪
  const [expanded, setExpanded] = useState(true)
  const isRunning = block?.status === 'running'

  const toggleExpanded = useCallback(() => setExpanded(v => !v), [])

  // 编辑态：悬停操作组点「编辑」后气泡替换为输入框，确认走 editAndResend 截断重发。
  // hooks 放在早退 return 之前，保证调用顺序稳定
  const editAndResend = useChatStore(s => s.editAndResend)
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState('')
  const startEdit = useCallback(() => {
    setEditText(block?.userMessage ?? '')
    setEditing(true)
  }, [block?.userMessage])
  // 确认后本块会被截断移除（组件随之卸载），无需复位 editing；
  // 发起失败（块已不存在等）同样卸载，新块里已有错误提示
  const confirmEdit = useCallback(() => {
    if (!editText.trim()) return
    void editAndResend(blockId, editText)
  }, [editAndResend, blockId, editText])
  const cancelEdit = useCallback(() => setEditing(false), [])

  if (!block) return null

  const hasItems = block.timeline.length > 0
  const hasProcessRows = block.timeline.some(s => s.type !== 'text')
  // 流程区显示规则：有时间线始终显示；无内容运行中显示等待占位；
  // 无内容异常结束保留状态行（行尾灰字承载异常）；其余组合让位给空
  const showFlow = hasItems
    || isRunning
    || !!(block.exitReason && !NORMAL_EXITS.has(block.exitReason))

  // 运行中过程行只显示最近 3 条，其余折叠；结束后显示全部
  const [showAllSteps, setShowAllSteps] = useState(false)
  const processIdx = block.timeline
    .map((it, i) => (it.type === 'text' ? -1 : i))
    .filter(i => i >= 0)
  const foldRunning = isRunning && !showAllSteps && processIdx.length > 3
  const hiddenProcess = foldRunning ? processIdx.slice(0, -3) : []
  const hiddenSet = new Set(hiddenProcess)

  // 异常结束且过程行可见时，时间线首行显示原因
  const reasonText = !isRunning && expanded ? exitReasonLine(block) : ''

  // 时间线按真实时序平铺：text → 正文行；reasoning → 思考行；tool → 事件行。
  // 折叠态（!expanded）只保留正文行 + 一条「已处理 N 步」折叠条
  const timelineNodes: React.ReactNode[] = []
  let foldBarRendered = false
  let runningFoldRendered = false
  block.timeline.forEach((item, i) => {
    if (item.type === 'text') {
      timelineNodes.push(<TextItemView key={item.id} item={item} />)
      return
    }
    if (!expanded) {
      if (!foldBarRendered) {
        foldBarRendered = true
        timelineNodes.push(
          <button
            key="fold-bar"
            className="work-row"
            type="button"
            onClick={toggleExpanded}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              width: '100%',
              padding: '3px 0',
              background: 'transparent',
              border: 'none',
              fontSize: '11px',
              fontFamily: 'var(--font-ui)',
              color: 'var(--text-tertiary)',
              textAlign: 'left',
              cursor: 'pointer',
              borderRadius: 'var(--radius-sm)',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--hover-bg)')}
            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
          >
            已处理 {processIdx.length} 步 ▸
          </button>,
        )
      }
      return
    }
    if (hiddenSet.has(i)) {
      if (!runningFoldRendered) {
        runningFoldRendered = true
        timelineNodes.push(
          <button
            key="running-fold"
            className="work-row"
            type="button"
            onClick={() => setShowAllSteps(true)}
            style={{
              display: 'flex',
              alignItems: 'center',
              width: '100%',
              padding: '3px 0',
              background: 'transparent',
              border: 'none',
              fontSize: '11px',
              fontFamily: 'var(--font-ui)',
              color: 'var(--text-tertiary)',
              textAlign: 'left',
              cursor: 'pointer',
              borderRadius: 'var(--radius-sm)',
            }}
            onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--hover-bg)')}
            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
          >
            +{hiddenProcess.length} 条历史步骤
          </button>,
        )
      }
      return
    }
    timelineNodes.push(
      item.type === 'reasoning'
        ? <ReasoningRow key={item.id} item={item} />
        : <EventLine key={item.id} step={item} />,
    )
  })

  return (
    // data-workblock-running：状态胶囊卡「智能体」跳转的回退锚点（目标卡片不在 DOM 时滚到运行中块）
    <div className="work-block" data-workblock-running={isRunning || undefined} style={{ display: 'flex', flexDirection: 'column', gap: '10px', animation: 'fade-in-up 280ms ease-out' }}>
      {/* 用户消息：技能触发时首行显示「徽章 + 技能名」。
          悬停显示操作组（复制/编辑，对齐 Claude.ai 交互）；运行中块与命令块不提供编辑。
          编辑态气泡替换为输入框：Ctrl+Enter 确认重发、Esc 取消 */}
      {editing ? (
        <div
          style={{
            alignSelf: 'flex-end',
            maxWidth: '80%',
            width: '100%',
            padding: '10px 12px',
            borderRadius: 'var(--radius-lg)',
            background: 'var(--bg-tertiary)',
            boxShadow: 'var(--shadow-md)',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
            boxSizing: 'border-box',
          }}
        >
          <textarea
            autoFocus
            value={editText}
            onChange={e => setEditText(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
                e.preventDefault()
                confirmEdit()
              } else if (e.key === 'Escape') {
                e.preventDefault()
                cancelEdit()
              }
            }}
            rows={Math.min(10, Math.max(2, editText.split('\n').length))}
            style={{
              width: '100%',
              background: 'transparent',
              color: 'var(--text-primary)',
              border: 'none',
              outline: 'none',
              resize: 'vertical',
              fontSize: '14px',
              lineHeight: 1.6,
              fontFamily: 'var(--font-ui)',
              whiteSpace: 'pre-wrap',
            }}
          />
          <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px' }}>
            <button
              type="button"
              onClick={cancelEdit}
              style={{
                padding: '4px 12px',
                fontSize: '12px',
                border: '1px solid var(--border-strong)',
                borderRadius: 'var(--radius-md)',
                background: 'transparent',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
              }}
            >
              取消
            </button>
            <button
              type="button"
              onClick={confirmEdit}
              disabled={!editText.trim()}
              style={{
                padding: '4px 12px',
                fontSize: '12px',
                border: 'none',
                borderRadius: 'var(--radius-md)',
                background: 'var(--button-primary-bg)',
                color: 'var(--button-primary-text)',
                cursor: editText.trim() ? 'pointer' : 'not-allowed',
                opacity: editText.trim() ? 1 : 0.5,
              }}
            >
              重发
            </button>
          </div>
        </div>
      ) : (
        <div
          className="msg-bubble-wrap"
          style={{ alignSelf: 'flex-end', maxWidth: '80%', position: 'relative' }}
        >
          {/* 悬停操作组：贴对话条下方右缘，裸图标无底板，仅已完成对话块显示编辑 */}
          {!isRunning && block.exitReason !== 'command' && (
            <div
              className="msg-actions"
              style={{
                position: 'absolute',
                // top 与气泡底缘齐平 + 内边距留出视觉间隙：悬停从气泡移到按钮
                // 的路径不离开容器，操作组不会中途淡出
                top: '100%',
                right: 0,
                zIndex: 5,
                display: 'flex',
                gap: '10px',
                paddingTop: '5px',
              }}
            >
              <button
                type="button"
                className="msg-action-btn"
                title="复制"
                onClick={() => void navigator.clipboard.writeText(block.userMessage)}
                style={{
                  width: '22px',
                  height: '22px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: '5px',
                  border: 'none',
                  padding: 0,
                  color: 'var(--text-tertiary)',
                  cursor: 'pointer',
                }}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="9" y="9" width="13" height="13" rx="2" />
                  <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                </svg>
              </button>
              <button
                type="button"
                className="msg-action-btn"
                title="编辑"
                onClick={startEdit}
                style={{
                  width: '22px',
                  height: '22px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  borderRadius: '5px',
                  border: 'none',
                  padding: 0,
                  color: 'var(--text-tertiary)',
                  cursor: 'pointer',
                }}
              >
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
                </svg>
              </button>
            </div>
          )}
          <div
            style={{
              padding: '10px 14px',
              borderRadius: 'var(--radius-lg)',
              background: 'var(--bg-tertiary)',
              color: 'var(--text-primary)',
              fontSize: '14px',
              lineHeight: 1.6,
              wordBreak: 'break-word',
              boxShadow: 'var(--shadow-md)',
              fontWeight: 500,
              whiteSpace: 'pre-wrap',
            }}
          >
            {block.skillName && (
              <span
                style={{
                  display: 'inline-flex',
                  alignItems: 'center',
                  gap: '5px',
                  marginRight: '8px',
                  padding: '1px 9px',
                  borderRadius: 'var(--radius-sm)',
                  background: 'var(--selected-bg)',
                  color: 'var(--text-primary)',
                  fontSize: '12px',
                  fontWeight: 600,
                  verticalAlign: 'middle',
                }}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 20h9" />
                  <path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z" />
                </svg>
                {block.skillName.charAt(0).toUpperCase() + block.skillName.slice(1)}
              </span>
            )}
            {renderUserMessage(block.userMessage)}
          </div>
        </div>
      )}

      {/* 流程区：状态行 + 细分隔线 + 活动行 + 时间线（正文/思考/工具按序平铺） */}
      {showFlow && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <StatusLine
            block={block}
            expanded={expanded}
            onToggle={hasProcessRows ? toggleExpanded : null}
          />
          {reasonText && (
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', padding: '2px 0', fontFamily: 'var(--font-ui)' }}>
              {reasonText}
            </div>
          )}
          {timelineNodes}
        </div>
      )}
    </div>
  )
}

// memo：流式更新时只有当前块的对象引用变化，历史块跳过重渲
export default memo(WorkBlockView)
