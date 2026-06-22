import { useState } from 'react'
import ChatStream from './ai/ChatStream'
import ChatInput from './ai/ChatInput'
import ContextPanel from './ai/ContextPanel'
import ModifiedFilesPanel from './ai/ModifiedFilesPanel'
import PermissionDialog from './ai/PermissionDialog'
import type { ChatMessage as ChatMessageType, PermissionRequest, TokenUsage } from '../hooks/useChat'

interface AIPanelProps {
  // 来自 useChat 的状态
  messages: ChatMessageType[]
  isStreaming: boolean
  sendMessage: (prompt: string) => void
  tokenUsage: TokenUsage
  permissionRequest: PermissionRequest | null
  resolvePermission: (decision: 'allow' | 'deny' | 'always_allow') => void
}

function AIPanel({
  messages,
  isStreaming,
  sendMessage,
  tokenUsage,
  permissionRequest,
  resolvePermission,
}: AIPanelProps) {
  // 信息子面板是否展开
  const [infoExpanded, setInfoExpanded] = useState(true)

  return (
    <div
      style={{
        backgroundColor: 'var(--bg-secondary)',
        borderLeft: '1px solid var(--border)',
        display: 'flex',
        flexDirection: 'column',
        height: '100%',
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {/* 顶部标题栏 */}
      <div
        style={{
          height: '36px',
          display: 'flex',
          alignItems: 'center',
          padding: '0 8px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <span style={{ fontSize: '13px', color: 'var(--text-primary)' }}>AI 对话</span>
      </div>

      {/* 对话流（占满中间，可滚动） */}
      <ChatStream messages={messages} />

      {/* 信息子面板（可折叠） */}
      <div style={{ borderTop: '1px solid var(--border)', flexShrink: 0 }}>
        <button
          onClick={() => setInfoExpanded(!infoExpanded)}
          style={{
            width: '100%',
            padding: '6px 12px',
            border: 'none',
            backgroundColor: 'var(--bg-tertiary)',
            color: 'var(--text-secondary)',
            fontSize: '12px',
            cursor: 'pointer',
            textAlign: 'left',
            display: 'flex',
            justifyContent: 'space-between',
          }}
        >
          <span>上下文 & 变更</span>
          <span>{infoExpanded ? '▼' : '▶'}</span>
        </button>
        {infoExpanded && (
          <>
            <ContextPanel usage={tokenUsage} />
            <ModifiedFilesPanel />
          </>
        )}
      </div>

      {/* 底部输入框 */}
      <div
        style={{
          padding: '8px 12px',
          borderTop: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <ChatInput onSend={sendMessage} disabled={isStreaming} />
      </div>

      {/* 权限确认模态层 */}
      {permissionRequest && (
        <PermissionDialog request={permissionRequest} onResolve={resolvePermission} />
      )}
    </div>
  )
}

export default AIPanel
