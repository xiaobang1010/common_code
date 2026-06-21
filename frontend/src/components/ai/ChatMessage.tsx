import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter'
// 样式文件没有独立的类型声明，这里忽略类型检查
// @ts-ignore
import { vscDarkPlus } from 'react-syntax-highlighter/dist/esm/styles/prism'
import type { ChatMessage as ChatMessageType } from '../../hooks/useChat'

interface Props {
  message: ChatMessageType
}

function ChatMessage({ message }: Props) {
  // 根据角色决定气泡样式
  const getStyle = (): React.CSSProperties => {
    switch (message.role) {
      case 'user':
        return {
          alignSelf: 'flex-end',
          backgroundColor: 'var(--accent)',
          color: '#fff',
        }
      case 'assistant':
        return {
          alignSelf: 'flex-start',
          backgroundColor: 'var(--bg-tertiary)',
          color: 'var(--text-primary)',
        }
      case 'tool':
        return {
          alignSelf: 'flex-start',
          backgroundColor: 'rgba(78, 201, 176, 0.12)',
          color: 'var(--success)',
          border: '1px solid rgba(78, 201, 176, 0.4)',
        }
      case 'system':
        return {
          alignSelf: 'center',
          backgroundColor: 'transparent',
          color: 'var(--text-secondary)',
        }
    }
  }

  // 工具结果超过 200 字符时截断
  const displayContent =
    message.role === 'tool' && message.content.length > 200
      ? message.content.slice(0, 200) + '...'
      : message.content

  return (
    <div
      style={{
        maxWidth: '85%',
        padding: '8px 12px',
        borderRadius: '8px',
        fontSize: '13px',
        lineHeight: 1.5,
        wordBreak: 'break-word',
        ...getStyle(),
      }}
    >
      {/* 工具调用摘要 */}
      {message.toolCalls?.map(tc => (
        <div
          key={tc.id}
          style={{ fontSize: '12px', opacity: 0.85, marginBottom: '4px' }}
        >
          [调用工具: {tc.name}]
        </div>
      ))}
      {/* assistant 消息用 Markdown 渲染，其余角色纯文本 */}
      {message.role === 'assistant' ? (
        <ReactMarkdown
          remarkPlugins={[remarkGfm]}
          components={{
            code({ className, children }) {
              // 带 language- 前缀的是代码块，走语法高亮
              const match = /language-(\w+)/.exec(className || '')
              if (match) {
                return (
                  <SyntaxHighlighter
                    language={match[1]}
                    style={vscDarkPlus}
                    PreTag="div"
                  >
                    {String(children).replace(/\n$/, '')}
                  </SyntaxHighlighter>
                )
              }
              return <code className={className}>{children}</code>
            },
          }}
        >
          {displayContent}
        </ReactMarkdown>
      ) : (
        <div style={{ whiteSpace: 'pre-wrap' }}>{displayContent}</div>
      )}
    </div>
  )
}

export default ChatMessage
