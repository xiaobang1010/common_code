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
        gap: '8px',
        padding: '12px',
      }}
    >
      {messages.length === 0 ? (
        <div
          style={{
            flex: 1,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--text-secondary)',
            fontSize: '14px',
          }}
        >
          开始对话...
        </div>
      ) : (
        messages.map(msg => (
          <div
            key={msg.id}
            style={{ display: 'flex', flexDirection: 'column' }}
          >
            <ChatMessage message={msg} />
            {/* 流式中的 assistant 消息末尾显示闪烁光标 */}
            {msg.isStreaming && (
              <span
                style={{
                  alignSelf: 'flex-start',
                  marginLeft: '12px',
                  color: 'var(--accent)',
                  animation: 'blink 1s infinite',
                  fontSize: '13px',
                }}
              >
                ▊
              </span>
            )}
          </div>
        ))
      )}
    </div>
  )
}

export default ChatStream
