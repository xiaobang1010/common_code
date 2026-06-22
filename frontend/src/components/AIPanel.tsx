import { useState } from 'react'
import ChatStream from './ai/ChatStream'
import ChatInput from './ai/ChatInput'
import ContextPanel from './ai/ContextPanel'
import ModifiedFilesPanel from './ai/ModifiedFilesPanel'
import type { ChatMessage as ChatMessageType, PermissionRequest, TokenUsage } from '../hooks/useChat'

interface AIPanelProps {
  messages: ChatMessageType[]
  isStreaming: boolean
  sendMessage: (prompt: string) => void
  abort: () => void
  tokenUsage: TokenUsage
  permissionRequest: PermissionRequest | null
  resolvePermission: (decision: 'allow' | 'deny' | 'always_allow') => void
}

function AIPanel({
  messages,
  isStreaming,
  sendMessage,
  abort,
  tokenUsage,
  permissionRequest,
  resolvePermission,
}: AIPanelProps) {
  const [infoExpanded, setInfoExpanded] = useState(false)

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-secondary)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        position: 'relative',
        // 微妙的顶部光晕，让 AI 面板有"主角感"
        boxShadow: isStreaming
          ? 'inset 0 1px 0 rgba(245, 166, 35, 0.15)'
          : 'inset 0 1px 0 rgba(255, 255, 255, 0.02)',
        transition: 'box-shadow 400ms ease',
      }}
    >
      {/* 顶部标题栏 - 带状态指示 */}
      <div
        style={{
          height: '44px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 16px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
          background: 'linear-gradient(180deg, rgba(245, 166, 35, 0.03), transparent)',
        }}
      >
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          {/* 状态指示点 */}
          <span
            style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              backgroundColor: isStreaming ? 'var(--accent)' : 'var(--success)',
              boxShadow: isStreaming
                ? '0 0 10px var(--accent-glow)'
                : '0 0 6px rgba(78, 201, 176, 0.4)',
              animation: isStreaming ? 'breathe 1.4s ease-in-out infinite' : 'none',
            }}
          />
          <span
            style={{
              fontSize: '13px',
              fontWeight: 500,
              color: 'var(--text-primary)',
              letterSpacing: '0.2px',
            }}
          >
            {isStreaming ? 'AI 正在工作' : 'AI 对话'}
          </span>
        </div>
        <span
          style={{
            fontSize: '11px',
            color: 'var(--text-tertiary)',
            fontFamily: 'var(--font-mono)',
            letterSpacing: '0.5px',
          }}
        >
          ⌘ + Enter
        </span>
      </div>

      {/* 对话流 */}
      <ChatStream messages={messages} />

      {/* 信息子面板（可折叠） */}
      <div
        style={{
          borderTop: '1px solid var(--border)',
          flexShrink: 0,
          backgroundColor: 'var(--bg-base)',
        }}
      >
        <button
          onClick={() => setInfoExpanded(!infoExpanded)}
          style={{
            width: '100%',
            padding: '8px 16px',
            border: 'none',
            backgroundColor: 'transparent',
            color: 'var(--text-secondary)',
            fontSize: '11px',
            cursor: 'pointer',
            textAlign: 'left',
            display: 'flex',
            justifyContent: 'space-between',
            alignItems: 'center',
            fontFamily: 'var(--font-ui)',
            letterSpacing: '0.5px',
            textTransform: 'uppercase',
            transition: 'color var(--transition-fast)',
          }}
          onMouseEnter={(e) => (e.currentTarget.style.color = 'var(--text-primary)')}
          onMouseLeave={(e) => (e.currentTarget.style.color = 'var(--text-secondary)')}
        >
          <span>上下文 & 变更</span>
          <span style={{ fontSize: '10px' }}>{infoExpanded ? '▾' : '▸'}</span>
        </button>
        {infoExpanded && (
          <>
            <ContextPanel usage={tokenUsage} />
            <ModifiedFilesPanel />
          </>
        )}
      </div>

      {/* 底部输入区 */}
      <div
        style={{
          padding: '12px 16px 14px',
          borderTop: '1px solid var(--border)',
          flexShrink: 0,
          background: 'linear-gradient(180deg, transparent, rgba(0, 0, 0, 0.15))',
        }}
      >
        <ChatInput
          onSend={sendMessage}
          disabled={isStreaming}
          isStreaming={isStreaming}
          onStop={abort}
          permissionRequest={permissionRequest}
          onResolve={resolvePermission}
        />
      </div>
    </div>
  )
}

export default AIPanel
