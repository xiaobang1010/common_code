import { useRef, useEffect } from 'react'
import ChatMessage from './ChatMessage'
import type { ChatMessage as ChatMessageType } from '../../hooks/useChat'

interface Props {
  messages: ChatMessageType[]
}

function ChatStream({ messages }: Props) {
  const containerRef = useRef<HTMLDivElement>(null)

  // 消息列表变化时自动滚动到底部
  useEffect(() => {
    if (containerRef.current) {
      containerRef.current.scrollTop = containerRef.current.scrollHeight
    }
  }, [messages])

  return (
    <div
      ref={containerRef}
      style={{
        flex: 1,
        overflowY: 'auto',
        display: 'flex',
        flexDirection: 'column',
        gap: '14px',
        padding: '20px 24px',
        scrollBehavior: 'smooth',
      }}
    >
      {messages.length === 0 ? (
        <div
          style={{
            flex: 1,
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            gap: '16px',
            color: 'var(--text-tertiary)',
          }}
        >
          {/* 装饰性图标 */}
          <div
            style={{
              width: '56px',
              height: '56px',
              borderRadius: '50%',
              background: 'linear-gradient(135deg, var(--accent), #ff7a45)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              boxShadow: '0 8px 24px rgba(245, 166, 35, 0.25)',
              marginBottom: '4px',
            }}
          >
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#1a1a1a" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2a3 3 0 0 0-3 3v1H7a3 3 0 0 0-3 3v1H3a1 1 0 0 0 0 2h1v1a3 3 0 0 0 3 3h.5a3 3 0 0 0 3 3h1.5a3 3 0 0 0 3-3h.5a3 3 0 0 0 3-3v-1h1a1 1 0 0 0 0-2h-1V9a3 3 0 0 0-3-3h-2V5a3 3 0 0 0-3-3z" />
            </svg>
          </div>
          <div
            style={{
              fontSize: '15px',
              fontWeight: 500,
              color: 'var(--text-secondary)',
              letterSpacing: '0.3px',
            }}
          >
            开始与 AI 对话
          </div>
          <div
            style={{
              fontSize: '12px',
              color: 'var(--text-tertiary)',
              fontFamily: 'var(--font-mono)',
              maxWidth: '320px',
              textAlign: 'center',
              lineHeight: 1.6,
            }}
          >
            描述你想做什么，AI 会读代码、改文件、跑命令
          </div>
        </div>
      ) : (
        messages.map(msg => (
          <div
            key={msg.id}
            style={{
              display: 'flex',
              flexDirection: 'column',
              animation: 'fade-in-up 280ms ease-out',
            }}
          >
            <ChatMessage message={msg} />
          </div>
        ))
      )}
    </div>
  )
}

export default ChatStream
