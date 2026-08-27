import { useState, useEffect, memo, useCallback, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
// @ts-ignore
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { useChatStore, formatDuration, lastActivityAtRef, type WorkBlock, type WorkStep } from '../../stores/useChatStore'
import SubagentCard from './SubagentCard'

interface Props {
  // 只订阅自己的工作块：流式更新只触发本组件重渲
  blockId: string
}

// ---------- 事件行动词映射（toolName 小写归一后匹配，未知工具兜底「已执行」+ 原名） ----------
const VERB_BY_TOOL: Record<string, string> = {
  bash: '已执行命令',
  read: '已读取',
  write: '已写入',
  edit: '已修改',
  grep: '已搜索',
  glob: '已查找',
  askuserquestion: '已提问',
  skill: '已运行技能',
  agent: '已委派子任务',
  sendmessage: '已发送消息',
  teamcreate: '已创建团队',
  taskcreate: '已创建任务',
  taskupdate: '已更新任务',
  tasklist: '已查看任务',
  taskget: '已获取任务',
  summarizeteam: '已汇总团队',
  error: '出错',
}

// 正常结束的退出原因；其余视为异常，需要弱提示与原因说明
const NORMAL_EXITS = new Set(['', 'completed', 'command'])

// 异常退出原因 → 行尾弱提示 / 展开首行原因（error 的展开首行附带错误步骤摘要，见 exitReasonLine）
const EXIT_HINT: Record<string, string> = {
  aborted: '已中断',
  error: '出错',
  model_error: '模型出错',
  prompt_too_long: '输入过长',
  max_output_tokens_exhausted: '输出超限',
}
const EXIT_REASON: Record<string, string> = {
  aborted: '已中断：用户主动停止',
  model_error: '模型出错',
  prompt_too_long: '输入过长',
  max_output_tokens_exhausted: '输出超限',
}

// 展开区首行原因：error 附错误步骤的 result 摘要；未知原因原样展示
function exitReasonLine(block: WorkBlock): string {
  const reason = block.exitReason ?? ''
  if (NORMAL_EXITS.has(reason)) return ''
  if (reason === 'error') {
    const detail = block.steps.find(s => s.toolName === 'error')?.result?.replace(/^错误:\s*/, '').trim()
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

// 工具步骤事件行：单行低调行（灰图标 + 动词 + 等宽对象名 + 状态位），点击展开详情
// memo：步骤对象引用稳定时跳过重渲，避免父块更新时全部步骤重绘
const EventLine = memo(function EventLine({ step }: { step: WorkStep }) {
  const [expanded, setExpanded] = useState(false)
  const isError = step.toolName === 'error'
  const isRunning = !!step.isRunning && !isError
  const known = VERB_BY_TOOL[step.toolName.toLowerCase()]
  const verb = known ?? '已执行'
  // 错误步骤展示错误摘要；已知工具展示 args 提取的对象名；未知工具以原始 toolName 兜底
  const objectText = isError
    ? (step.result || '').replace(/^错误:\s*/, '')
    : extractObject(step.args) ?? (known ? null : step.toolName)
  // 可展开条件：有详情内容，或运行中（可看「等待结果...」占位）
  const clickable = !!(step.reasoning || step.args || step.result) || isRunning
  const rowColor = isError ? 'var(--error)' : 'var(--text-tertiary)'

  const row = (
    <>
      {isRunning ? (
        <svg className="work-spin" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}>
          <path d="M21 12a9 9 0 1 1-6.219-8.56" />
        </svg>
      ) : (
        <StepIcon kind={iconKind(step.toolName)} />
      )}
      <span style={{ flexShrink: 0 }}>{verb}</span>
      {objectText && (
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
          {step.reasoning && (
            <div>
              <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '2px', letterSpacing: '1px', textTransform: 'uppercase' }}>
                思考过程
              </div>
              <div style={{
                fontSize: '11px',
                color: 'var(--text-tertiary)',
                lineHeight: 1.6,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                maxHeight: '300px',
                overflow: 'auto',
              }}>
                {step.reasoning}
              </div>
            </div>
          )}
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

// 状态行：主行（工作中/已工作 + 耗时）+ 细分隔线 + 活动行。
// 1s tick 收敛在本组件内：时间刷新与 idleMs 重算共用，事件行列表与最终回复不随 tick 重渲
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
    const runningStep = block.steps.find(s => s.isRunning)
    if (runningStep) return `正在执行工具 ${runningStep.toolName}`
    if (block.finalReplyStreaming && block.finalReply) return '正在生成回复'
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

// Markdown 渲染配置（对话区渲染与编辑区 .md 预览共用）
export const markdownComponents = {
  code({ className, children }: { className?: string; children?: React.ReactNode }) {
    const match = /language-(\w+)/.exec(className || '')
    const codeText = String(children).replace(/\n$/, '')
    if (match) {
      // 超长代码块不做 Prism 高亮：tokenize 会生成海量 span，页面容易卡死
      if (codeText.split('\n').length > 300 || codeText.length > 20000) {
        return (
          <pre
            style={{
              background: 'var(--bg-base)',
              borderRadius: 'var(--radius-md)',
              border: '1px solid var(--border-subtle)',
              fontSize: '12px',
              margin: '8px 0',
              padding: '12px',
              overflow: 'auto',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-primary)',
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
            }}
          >
            {codeText}
          </pre>
        )
      }
      return (
        <SyntaxHighlighter
          language={match[1]}
          style={vscDarkPlus}
          PreTag="div"
          customStyle={{
            background: 'var(--bg-base)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            fontSize: '12px',
            margin: '8px 0',
          }}
        >
          {codeText}
        </SyntaxHighlighter>
      )
    }
    return (
      <code
        style={{
          backgroundColor: 'var(--code-bg)',
          border: '1px solid var(--code-border)',
          padding: '1px 5px',
          borderRadius: '4px',
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          color: 'var(--code-text)',
        }}
      >
        {children}
      </code>
    )
  },
  a({ children, href }: { children?: React.ReactNode; href?: string }) {
    return (
      <a className="markdown-link" href={href}>
        {children}
      </a>
    )
  },
  p({ children }: { children?: React.ReactNode }) {
    return <p style={{ margin: '6px 0' }}>{children}</p>
  },
  ul({ children }: { children?: React.ReactNode }) {
    return <ul style={{ margin: '6px 0', paddingLeft: '20px' }}>{children}</ul>
  },
  ol({ children }: { children?: React.ReactNode }) {
    return <ol style={{ margin: '6px 0', paddingLeft: '20px' }}>{children}</ol>
  },
  // 表格可能很宽：外层包横向滚动容器，让宽表格在限宽列内部滚动而不撑破列边界
  table({ children }: { children?: React.ReactNode }) {
    return (
      <div style={{ overflowX: 'auto' }}>
        <table>{children}</table>
      </div>
    )
  },
  h1({ children }: { children?: React.ReactNode }) {
    return <h1 style={{ fontSize: '17px', fontWeight: 600, margin: '12px 0 6px' }}>{children}</h1>
  },
  h2({ children }: { children?: React.ReactNode }) {
    return <h2 style={{ fontSize: '15px', fontWeight: 600, margin: '10px 0 4px' }}>{children}</h2>
  },
  h3({ children }: { children?: React.ReactNode }) {
    return <h3 style={{ fontSize: '14px', fontWeight: 600, margin: '8px 0 4px' }}>{children}</h3>
  },
}

// 流式期间的轻量 Markdown 渲染配置：代码块不做 Prism 高亮。
// 回复未完成时每帧全量 tokenize 会随内容变长越来越卡，等完成后切回完整高亮渲染
const lightMarkdownComponents = {
  ...markdownComponents,
  code({ className, children }: { className?: string; children?: React.ReactNode }) {
    const match = /language-(\w+)/.exec(className || '')
    if (match) {
      return (
        <pre
          style={{
            background: 'var(--bg-base)',
            borderRadius: 'var(--radius-md)',
            border: '1px solid var(--border-subtle)',
            fontSize: '12px',
            margin: '8px 0',
            padding: '12px',
            overflow: 'auto',
            fontFamily: 'var(--font-mono)',
            color: 'var(--text-primary)',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {String(children).replace(/\n$/, '')}
        </pre>
      )
    }
    return (
      <code
        style={{
          backgroundColor: 'var(--code-bg)',
          border: '1px solid var(--code-border)',
          padding: '1px 5px',
          borderRadius: '4px',
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          color: 'var(--code-text)',
        }}
      >
        {children}
      </code>
    )
  },
}

function WorkBlockView({ blockId }: Props) {
  // 局部订阅：只监听自己的工作块，其他 block 更新时不重渲
  const block = useChatStore(s => s.blocksById[blockId])
  const [expanded, setExpanded] = useState(block?.status === 'running')
  const isRunning = block?.status === 'running'

  // 展开策略：正常完成自动折叠；异常结束保持展开，让原因可见。
  // 用 prevStatusRef 只响应 running→done 的切换，历史回合加载时不自动展开
  const prevStatusRef = useRef(block?.status)
  useEffect(() => {
    if (block?.status === 'done' && prevStatusRef.current === 'running') {
      const abnormal = !!block.exitReason && !NORMAL_EXITS.has(block.exitReason)
      setExpanded(abnormal)
    }
    prevStatusRef.current = block?.status
  }, [block?.status])

  const toggleExpanded = useCallback(() => setExpanded(v => !v), [])

  // 流式结束后延迟约 250ms 再升级完整高亮：流式期间与延迟窗口内保持轻量渲染，
  // 避免长回复完成瞬间从轻量渲染切到 Prism 高亮的可见跳变
  const [highlightReady, setHighlightReady] = useState(!block?.finalReplyStreaming)
  useEffect(() => {
    if (block?.finalReplyStreaming) {
      setHighlightReady(false)
      return
    }
    const timer = window.setTimeout(() => setHighlightReady(true), 250)
    return () => clearTimeout(timer)
  }, [block?.finalReplyStreaming])

  if (!block) return null

  const hasSteps = block.steps.length > 0
  // 流程区显示规则：有步骤始终显示；无步骤运行中且未出文本时显示等待占位；
  // 无步骤异常结束保留状态行（行尾灰字承载异常）；其余组合让位给文本流
  const showFlow = hasSteps
    || (isRunning && !block.finalReply)
    || (!isRunning && !!(block.exitReason && !NORMAL_EXITS.has(block.exitReason)))

  // 运行中只显示最近 3 条步骤，其余折叠；结束后展开显示全部
  const [showAllSteps, setShowAllSteps] = useState(false)
  const foldSteps = isRunning && !showAllSteps && block.steps.length > 3
  const recentSteps = foldSteps ? block.steps.slice(-3) : block.steps
  const hiddenCount = block.steps.length - recentSteps.length

  // 异常结束且展开时，展开区首行显示原因
  const reasonText = !isRunning && expanded ? exitReasonLine(block) : ''

  return (
    // data-workblock-running：状态胶囊卡「智能体」跳转的回退锚点（目标卡片不在 DOM 时滚到运行中块）
    <div className="work-block" data-workblock-running={isRunning || undefined} style={{ display: 'flex', flexDirection: 'column', gap: '10px', animation: 'fade-in-up 280ms ease-out' }}>
      {/* 用户消息 */}
      <div
        style={{
          alignSelf: 'flex-end',
          maxWidth: '80%',
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
        {block.userMessage}
      </div>

      {/* 流程区：状态行 + 细分隔线 + 活动行 + 事件行，纯文本流排布 */}
      {showFlow && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          <StatusLine
            block={block}
            expanded={expanded}
            onToggle={hasSteps ? toggleExpanded : null}
          />
          {expanded && hasSteps && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: '2px' }}>
              {reasonText && (
                <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', padding: '2px 0', fontFamily: 'var(--font-ui)' }}>
                  {reasonText}
                </div>
              )}
              {recentSteps.map(step => (
                <EventLine key={step.id} step={step} />
              ))}
              {foldSteps && (
                <button
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
                  +{hiddenCount} 条历史步骤
                </button>
              )}
            </div>
          )}
        </div>
      )}

      {/* 最终回复（独立显示在工作块外） */}
      {block.finalReply && (
        <div
          style={{
            alignSelf: 'flex-start',
            maxWidth: '92%',
            fontSize: '14px',
            lineHeight: 1.6,
            color: 'var(--text-primary)',
            wordBreak: 'break-word',
          }}
        >
          {block.finalReplyStreaming || !highlightReady ? (
            // 流式期间与结束后的延迟窗口内用轻量渲染（代码块不高亮），
            // 窗口结束后切回完整 Markdown + 高亮
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={lightMarkdownComponents}
            >
              {block.finalReply}
            </ReactMarkdown>
          ) : (
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              components={markdownComponents}
            >
              {block.finalReply}
            </ReactMarkdown>
          )}
          {block.finalReplyStreaming && (
            <span
              style={{
                display: 'inline-block',
                width: '8px',
                height: '14px',
                backgroundColor: 'var(--text-primary)',
                marginLeft: '2px',
                verticalAlign: 'text-bottom',
                animation: 'blink 1s step-end infinite',
                borderRadius: '1px',
              }}
            />
          )}
        </div>
      )}
    </div>
  )
}

// memo：流式更新时只有当前块的对象引用变化，历史块跳过重渲
export default memo(WorkBlockView)
