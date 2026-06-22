import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
// @ts-ignore
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import { useState } from 'react'
import type { ChatMessage as ChatMessageType } from '../../hooks/useChat'

interface Props {
  message: ChatMessageType
}

// 可展开的工具执行步骤卡片，对齐 ZCode 的执行过程展示
function ToolStepCard({ message }: { message: ChatMessageType }) {
  const [expanded, setExpanded] = useState(false)
  const step = message.toolStep!
  const isRunning = step.isRunning

  return (
    <div
      style={{
        alignSelf: 'flex-start',
        maxWidth: '90%',
        borderRadius: 'var(--radius-md)',
        backgroundColor: 'var(--bg-base)',
        border: '1px solid var(--border-subtle)',
        borderLeft: `2px solid ${isRunning ? 'var(--accent)' : 'var(--success)'}`,
        overflow: 'hidden',
      }}
    >
      {/* 头部：点击展开/折叠 */}
      <div
        onClick={() => setExpanded(!expanded)}
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '8px',
          padding: '8px 12px',
          cursor: 'pointer',
          fontSize: '12px',
          fontFamily: 'var(--font-mono)',
          color: isRunning ? 'var(--accent)' : 'var(--text-secondary)',
          userSelect: 'none',
          transition: 'background var(--transition-fast)',
        }}
        onMouseEnter={(e) => (e.currentTarget.style.backgroundColor = 'var(--bg-tertiary)')}
        onMouseLeave={(e) => (e.currentTarget.style.backgroundColor = 'transparent')}
      >
        {/* 运行中转圈图标，完成是勾 */}
        {isRunning ? (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ animation: 'spin 1s linear infinite' }}>
            <path d="M21 12a9 9 0 1 1-6.219-8.56" />
          </svg>
        ) : (
          <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="var(--success)" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M20 6L9 17l-5-5" />
          </svg>
        )}
        <span style={{ fontWeight: 500 }}>
          {isRunning ? '执行中' : '已完成'} · {step.toolName}
        </span>
        <span style={{ marginLeft: 'auto', color: 'var(--text-tertiary)', fontSize: '10px' }}>
          {expanded ? '▾' : '▸'}
        </span>
      </div>

      {/* 展开后的详情：参数 + 结果 */}
      {expanded && (
        <div style={{ borderTop: '1px solid var(--border-subtle)', padding: '10px 12px' }}>
          {/* 参数 */}
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
          {/* 结果 */}
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
          {/* 运行中还没有结果 */}
          {isRunning && !step.result && (
            <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', fontStyle: 'italic' }}>
              等待结果...
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function ChatMessage({ message }: Props) {
  // 用户消息：右对齐，琥珀色渐变气泡
  // AI 消息：左对齐，无背景，融入面板
  // 工具消息：等宽字体，淡色卡片
  // 系统消息：居中，最小化
  const isUser = message.role === 'user'
  const isAssistant = message.role === 'assistant'
  const isTool = message.role === 'tool'
  const isSystem = message.role === 'system'

  // 有 toolStep 的工具消息：渲染可展开卡片
  if (isTool && message.toolStep) {
    return <ToolStepCard message={message} />
  }

  // 工具结果截断
  const displayContent =
    isTool && message.content.length > 240
      ? message.content.slice(0, 240) + ' …'
      : message.content

  // 系统消息：极简居中
  if (isSystem) {
    return (
      <div
        style={{
          alignSelf: 'center',
          padding: '6px 14px',
          fontSize: '11px',
          color: 'var(--text-tertiary)',
          fontFamily: 'var(--font-mono)',
          letterSpacing: '0.3px',
          backgroundColor: 'var(--bg-base)',
          borderRadius: '100px',
          border: '1px solid var(--border-subtle)',
          maxWidth: '90%',
          textAlign: 'center',
        }}
      >
        {displayContent}
      </div>
    )
  }

  // 工具消息：等宽字体，代码风格
  if (isTool) {
    return (
      <div
        style={{
          alignSelf: 'flex-start',
          maxWidth: '88%',
          padding: '10px 14px',
          borderRadius: 'var(--radius-md)',
          backgroundColor: 'var(--bg-base)',
          border: '1px solid var(--border-subtle)',
          borderLeft: '2px solid var(--success)',
          fontSize: '12px',
          fontFamily: 'var(--font-mono)',
          color: 'var(--text-secondary)',
          lineHeight: 1.6,
          wordBreak: 'break-word',
          whiteSpace: 'pre-wrap',
        }}
      >
        {/* 工具调用标识 */}
        {message.toolCalls?.map(tc => (
          <div
            key={tc.id}
            style={{
              fontSize: '11px',
              color: 'var(--accent)',
              marginBottom: '6px',
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              fontFamily: 'var(--font-ui)',
              fontWeight: 500,
            }}
          >
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
            </svg>
            调用工具 · {tc.name}
          </div>
        ))}
        {displayContent}
      </div>
    )
  }

  // 用户 / AI 消息
  return (
    <div
      style={{
        alignSelf: isUser ? 'flex-end' : 'flex-start',
        maxWidth: isUser ? '80%' : '92%',
        padding: isUser ? '10px 14px' : '4px 0',
        borderRadius: isUser ? 'var(--radius-lg)' : '0',
        background: isUser
          ? 'linear-gradient(135deg, var(--accent), #e89015)'
          : 'transparent',
        color: isUser ? '#1a1a1a' : 'var(--text-primary)',
        fontSize: '14px',
        lineHeight: 1.6,
        wordBreak: 'break-word',
        boxShadow: isUser ? '0 2px 8px rgba(245, 166, 35, 0.2)' : 'none',
        fontWeight: isUser ? 500 : 400,
      }}
    >
      {/* 工具调用摘要（assistant 消息带工具调用时） */}
      {message.toolCalls?.map(tc => (
        <div
          key={tc.id}
          style={{
            fontSize: '12px',
            color: 'var(--accent)',
            marginBottom: '6px',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            fontFamily: 'var(--font-ui)',
          }}
        >
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
          </svg>
          调用工具 · {tc.name}
        </div>
      ))}

      {/* 流式光标 */}
      {isAssistant && message.isStreaming && (
        <>
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              code({ className, children }) {
                const match = /language-(\w+)/.exec(className || '')
                if (match) {
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
                      {String(children).replace(/\n$/, '')}
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
              p({ children }) {
                return <p style={{ margin: '6px 0' }}>{children}</p>
              },
              ul({ children }) {
                return <ul style={{ margin: '6px 0', paddingLeft: '20px' }}>{children}</ul>
              },
              ol({ children }) {
                return <ol style={{ margin: '6px 0', paddingLeft: '20px' }}>{children}</ol>
              },
              h1({ children }) {
                return <h1 style={{ fontSize: '17px', fontWeight: 600, margin: '12px 0 6px' }}>{children}</h1>
              },
              h2({ children }) {
                return <h2 style={{ fontSize: '15px', fontWeight: 600, margin: '10px 0 4px' }}>{children}</h2>
              },
              h3({ children }) {
                return <h3 style={{ fontSize: '14px', fontWeight: 600, margin: '8px 0 4px' }}>{children}</h3>
              },
            }}
          >
            {displayContent || ' '}
          </ReactMarkdown>
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
        </>
      )}

      {/* 非流式的 assistant 消息 */}
      {isAssistant && !message.isStreaming && (
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            code({ className, children }) {
              const match = /language-(\w+)/.exec(className || '')
              if (match) {
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
                    {String(children).replace(/\n$/, '')}
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
            p({ children }) {
              return <p style={{ margin: '6px 0' }}>{children}</p>
            },
            ul({ children }) {
              return <ul style={{ margin: '6px 0', paddingLeft: '20px' }}>{children}</ul>
            },
            ol({ children }) {
              return <ol style={{ margin: '6px 0', paddingLeft: '20px' }}>{children}</ol>
            },
            h1({ children }) {
              return <h1 style={{ fontSize: '17px', fontWeight: 600, margin: '12px 0 6px' }}>{children}</h1>
            },
            h2({ children }) {
              return <h2 style={{ fontSize: '15px', fontWeight: 600, margin: '10px 0 4px' }}>{children}</h2>
            },
            h3({ children }) {
              return <h3 style={{ fontSize: '14px', fontWeight: 600, margin: '8px 0 4px' }}>{children}</h3>
            },
          }}
        >
          {displayContent}
        </ReactMarkdown>
      )}

      {/* 用户消息纯文本 */}
      {isUser && (
        <div style={{ whiteSpace: 'pre-wrap' }}>{displayContent}</div>
      )}
    </div>
  )
}

export default ChatMessage
