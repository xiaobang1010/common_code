import { useState, useEffect, memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
// @ts-ignore
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { useChatStore, formatDuration, lastActivityAtRef, type WorkBlock, type WorkStep } from '../../stores/useChatStore'

interface Props {
  // 只订阅自己的工作块：流式更新只触发本组件重渲
  blockId: string
}

// 计算工作块耗时（容忍 block 尚未就绪的防御）
function useDuration(block: WorkBlock | undefined) {
  const [, force] = useState(0)
  useEffect(() => {
    if (block?.status === 'running') {
      const timer = setInterval(() => force((n) => n + 1), 1000)
      return () => clearInterval(timer)
    }
  }, [block?.status, block?.startTime])
  const end = block ? block.endTime || Date.now() : 0
  return block ? end - block.startTime : 0
}

// 工具步骤卡片（复用原 ChatMessage 的 ToolStepCard 逻辑）
// memo：步骤对象引用稳定时跳过重渲，避免父块更新时全部步骤重绘
const ToolStepView = memo(function ToolStepView({ step }: { step: WorkStep }) {
  const [expanded, setExpanded] = useState(false)
  const isRunning = step.isRunning
  const isError = step.toolName === 'error'

  return (
    <div
      style={{
        borderRadius: 'var(--radius-md)',
        backgroundColor: 'var(--bg-base)',
        border: '1px solid var(--border-subtle)',
        borderLeft: `2px solid ${isError ? 'var(--error)' : isRunning ? 'var(--info)' : 'var(--success)'}`,
        overflow: 'hidden',
      }}
    >
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '7px 12px',
          cursor: 'pointer',
          fontSize: '12px',
          fontFamily: 'var(--font-mono)',
          color: isError ? 'var(--error)' : isRunning ? 'var(--info)' : 'var(--text-secondary)',
          userSelect: 'none',
          transition: 'background var(--transition-fast)',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
        onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
      >
        {isRunning ? (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}>
            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
          </svg>
        ) : isError ? (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <circle cx="12" cy="12" r="10" />
            <path d="M12 8v4M12 16h.01" />
          </svg>
        ) : (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
            <path d="M20 6L9 17l-5-5" />
          </svg>
        )}
        <span style={{ fontWeight: 500 }}>
          {isRunning ? '执行中' : isError ? '错误' : '已完成'} · {step.toolName}
        </span>
        <span style={{ marginLeft: 'auto', color: 'var(--text-tertiary)', fontSize: '10px' }}>
          {expanded ? '▾' : '▸'}
        </span>
      </div>

      {expanded && (
        <div style={{ borderTop: '1px solid var(--border-subtle)', padding: '10px 12px' }}>
          {step.reasoning && (
            <div style={{ marginBottom: '10px' }}>
              <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '4px', letterSpacing: '1px', textTransform: 'uppercase' }}>
                思考过程
              </div>
              <div style={{
                fontSize: '11px',
                fontFamily: 'var(--font-ui)',
                color: 'var(--text-tertiary)',
                lineHeight: 1.6,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
                padding: '8px 10px',
                backgroundColor: 'var(--bg-tertiary)',
                borderRadius: 'var(--radius-sm)',
                borderLeft: '2px solid var(--text-tertiary)',
                maxHeight: '300px',
                overflow: 'auto',
              }}>
                {step.reasoning}
              </div>
            </div>
          )}
          {step.args && (
            <div style={{ marginBottom: '8px' }}>
              <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '4px', letterSpacing: '1px', textTransform: 'uppercase' }}>
                参数
              </div>
              <pre style={{ margin: 0, fontSize: '11px', fontFamily: 'var(--font-mono)', color: 'var(--text-secondary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                {step.args}
              </pre>
            </div>
          )}
          {step.result && (
            <div>
              <div style={{ fontSize: '10px', color: 'var(--text-tertiary)', marginBottom: '4px', letterSpacing: '1px', textTransform: 'uppercase' }}>
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
  const abort = useChatStore(s => s.abort)
  const [expanded, setExpanded] = useState(block?.status === 'running')
  const duration = useDuration(block)
  const isRunning = block?.status === 'running'
  const toolCount = block ? block.steps.filter(s => s.toolName !== 'error').length : 0

  // 状态变化时自动折叠
  useEffect(() => {
    if (block?.status === 'done') {
      setExpanded(false)
    }
  }, [block?.status])

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

  // 卡片显示条件：有工具步骤时始终显示；无步骤时仅在运行态且尚未出文本时显示等待占位。
  // 首 token 一到（finalReply 非空），占位让位给文本流，不出现「工作中」与文本并存
  const showCard = block.steps.length > 0 || (isRunning && !block.finalReply)

  // 距上次 SSE 活动（含 heartbeat）的间隔。heartbeat 只证明连接活着、不证明模型有产出，
  // 文案保持保守，不伪装成进展；由 useDuration 的 1s tick 驱动重算，不额外挂状态更新
  const idleMs = Date.now() - lastActivityAtRef.current

  // 纯计时文案：占位形态在无任何阶段信号时的兜底
  const waitText = (() => {
    const secs = Math.floor(duration / 1000)
    if (secs > 10) return '响应较慢，可停止后重试'
    if (secs >= 3) return `等待模型响应 · 已等待 ${formatDuration(duration)}`
    if (secs >= 1) return '等待模型响应'
    return '工作中'
  })()

  // 卡片头部状态文案，按固定优先级推导：
  // 连接健康 > 流式文本 > 运行中步骤 > 后端 phase > 计时文案。
  // 连接异常仅在 5s+ 无任何事件时触发，此时覆盖一切阶段文案最诚实
  const statusText = (() => {
    if (!isRunning) return `已工作 ${formatDuration(duration)}`
    if (idleMs > 10000) return '连接异常，可停止后重试'
    if (idleMs > 5000) return '连接不稳定，仍在等待'
    if (block.finalReplyStreaming && block.finalReply) return '正在生成回复'
    if (block.steps.some(s => s.isRunning)) return '正在执行工具'
    if (block.phase === 'memory_ready') return '已加载上下文'
    if (block.phase === 'model_requested') return '正在调用模型'
    return waitText
  })()
  // 连接异常时突出停止入口，避免卡片永久转圈
  const connectionAbnormal = idleMs > 10000

  // 退出原因文案
  const exitText = (() => {
    if (!block.exitReason) return ''
    if (block.exitReason === 'completed') return '已完成'
    if (block.exitReason === 'aborted') return '已中断'
    if (block.exitReason === 'error') return '出错'
    if (block.exitReason === 'command') return '命令'
    return block.exitReason
  })()

  return (
    <div className="work-block" style={{ display: 'flex', flexDirection: 'column', gap: '10px', animation: 'fade-in-up 280ms ease-out' }}>
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
          boxShadow: '0 2px 8px rgba(0, 0, 0, 0.2)',
          fontWeight: 500,
          whiteSpace: 'pre-wrap',
        }}
      >
        {block.userMessage}
      </div>

      {/* 工作块卡片：运行态即显示（无步骤时是等待占位形态），结束后仅保留有步骤的卡片 */}
      {showCard && (
        <div
          style={{
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-base)',
            border: '1px solid var(--border-subtle)',
            overflow: 'hidden',
          }}
        >
          {/* 头部：占位形态（无步骤）只展示状态与计时，不可展开 */}
          <div
            onClick={block.steps.length > 0 ? () => setExpanded(!expanded) : undefined}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 12px',
              cursor: block.steps.length > 0 ? 'pointer' : 'default',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              color: 'var(--text-secondary)',
              userSelect: 'none',
              transition: 'background var(--transition-fast)',
              borderBottom: expanded && block.steps.length > 0 ? '1px solid var(--border-subtle)' : 'none',
            }}
            onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
          >
            {isRunning ? (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--info)" strokeWidth="2" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}>
                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              </svg>
            ) : (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <path d="M20 6L9 17l-5-5" />
              </svg>
            )}
            <span style={{ fontWeight: 500, color: isRunning ? 'var(--info)' : 'var(--text-secondary)' }}>
              {statusText}
            </span>
            {toolCount > 0 && (
              <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>
                {toolCount} 个工具调用
              </span>
            )}
            {exitText && !isRunning && (
              <span style={{ color: 'var(--text-tertiary)', fontSize: '11px' }}>
                · {exitText}
              </span>
            )}
            {/* 运行中的停止入口：占位与步骤形态共用，点击不触发卡片折叠；连接异常时高亮 */}
            {isRunning && (
              <button
                onClick={(e) => {
                  e.stopPropagation()
                  abort()
                }}
                style={{
                  marginLeft: 'auto',
                  background: 'transparent',
                  border: `1px solid ${connectionAbnormal ? 'var(--error)' : 'var(--border-subtle)'}`,
                  borderRadius: 'var(--radius-sm)',
                  color: connectionAbnormal ? 'var(--error)' : 'var(--text-secondary)',
                  fontSize: '11px',
                  padding: '2px 10px',
                  cursor: 'pointer',
                  transition: 'border-color var(--transition-fast), color var(--transition-fast)',
                }}
                onMouseEnter={(e) => {
                  e.currentTarget.style.borderColor = 'var(--error)'
                  e.currentTarget.style.color = 'var(--error)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.borderColor = connectionAbnormal ? 'var(--error)' : 'var(--border-subtle)'
                  e.currentTarget.style.color = connectionAbnormal ? 'var(--error)' : 'var(--text-secondary)'
                }}
              >
                停止
              </button>
            )}
            {block.steps.length > 0 && (
              <span style={{ marginLeft: 'auto', color: 'var(--text-tertiary)', fontSize: '10px' }}>
                {expanded ? '▾' : '▸'}
              </span>
            )}
          </div>

          {/* 展开后的中间步骤 */}
          {expanded && block.steps.length > 0 && (
            <div style={{ padding: '10px 12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
              {block.steps.map(step => (
                <ToolStepView key={step.id} step={step} />
              ))}
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
