import { useState, useEffect, memo } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
// @ts-ignore
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { useChatStore, formatDuration, type WorkBlock, type WorkStep } from '../../stores/useChatStore'

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
        borderLeft: `2px solid ${isError ? 'var(--error)' : isRunning ? 'var(--accent)' : 'var(--success)'}`,
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
          color: isError ? 'var(--error)' : isRunning ? 'var(--accent)' : 'var(--text-secondary)',
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

// Markdown 渲染配置（复用）
const markdownComponents = {
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
          backgroundColor: 'var(--bg-tertiary)',
          padding: '1px 5px',
          borderRadius: '3px',
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          color: 'var(--accent)',
        }}
      >
        {children}
      </code>
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
          backgroundColor: 'var(--bg-tertiary)',
          padding: '1px 5px',
          borderRadius: '3px',
          fontFamily: 'var(--font-mono)',
          fontSize: '12px',
          color: 'var(--accent)',
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
  const duration = useDuration(block)
  const isRunning = block?.status === 'running'
  const toolCount = block ? block.steps.filter(s => s.toolName !== 'error').length : 0

  // 状态变化时自动折叠
  useEffect(() => {
    if (block?.status === 'done') {
      setExpanded(false)
    }
  }, [block?.status])

  if (!block) return null

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
          background: 'linear-gradient(135deg, var(--accent), #e89015)',
          color: '#1a1a1a',
          fontSize: '14px',
          lineHeight: 1.6,
          wordBreak: 'break-word',
          boxShadow: '0 2px 8px rgba(245, 166, 35, 0.2)',
          fontWeight: 500,
          whiteSpace: 'pre-wrap',
        }}
      >
        {block.userMessage}
      </div>

      {/* 中间过程工作块卡片（有步骤时才显示） */}
      {block.steps.length > 0 && (
        <div
          style={{
            borderRadius: 'var(--radius-md)',
            backgroundColor: 'var(--bg-base)',
            border: '1px solid var(--border-subtle)',
            overflow: 'hidden',
          }}
        >
          {/* 头部 */}
          <div
            onClick={() => setExpanded(!expanded)}
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '8px',
              padding: '8px 12px',
              cursor: 'pointer',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              color: 'var(--text-secondary)',
              userSelect: 'none',
              transition: 'background var(--transition-fast)',
              borderBottom: expanded ? '1px solid var(--border-subtle)' : 'none',
            }}
            onMouseOver={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
            onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
          >
            {isRunning ? (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite', flexShrink: 0 }}>
                <path d="M21 12a9 9 0 1 1-6.219-8.56" />
              </svg>
            ) : (
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
                <path d="M20 6L9 17l-5-5" />
              </svg>
            )}
            <span style={{ fontWeight: 500, color: isRunning ? 'var(--accent)' : 'var(--text-secondary)' }}>
              {isRunning ? '工作中' : '已工作'} {formatDuration(duration)}
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
            <span style={{ marginLeft: 'auto', color: 'var(--text-tertiary)', fontSize: '10px' }}>
              {expanded ? '▾' : '▸'}
            </span>
          </div>

          {/* 展开后的中间步骤 */}
          {expanded && (
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
          {block.finalReplyStreaming ? (
            // 流式期间用轻量渲染（代码块不高亮），完成后切回完整 Markdown + 高亮
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
                backgroundColor: 'var(--accent)',
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
