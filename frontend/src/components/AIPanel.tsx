import { useState } from 'react'
import ChatStream from './ai/ChatStream'
import ChatInput from './ai/ChatInput'
import ContextPanel from './ai/ContextPanel'
import ModifiedFilesPanel from './ai/ModifiedFilesPanel'
import type { WorkBlock, PermissionRequest, QuestionRequest, TokenUsage } from '../hooks/useChat'
import type { PermissionMode } from '../api/client'

interface AIPanelProps {
  blocks: WorkBlock[]
  formatDuration: (ms: number) => string
  isStreaming: boolean
  sendMessage: (prompt: string) => void
  abort: () => void
  tokenUsage: TokenUsage
  permissionRequest: PermissionRequest | null
  resolvePermission: (decision: 'allow' | 'deny' | 'always_allow') => void
  questionRequest: QuestionRequest | null
  answerQuestion: (answer: string) => void
  permissionMode: PermissionMode
  onPermissionModeChange: (mode: PermissionMode) => void
  workspaceSelector: React.ReactNode
  branchSelector: React.ReactNode
  onNewSession: () => void
}

function AIPanel({
  blocks,
  formatDuration,
  isStreaming,
  sendMessage,
  abort,
  tokenUsage,
  permissionRequest,
  resolvePermission,
  questionRequest,
  answerQuestion,
  permissionMode,
  onPermissionModeChange,
  workspaceSelector,
  branchSelector,
  onNewSession,
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
      {/* 顶部标题栏 - 工作区/分支选择器 + 新建按钮 */}
      <div
        style={{
          height: '44px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          padding: '0 12px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
          background: 'linear-gradient(180deg, rgba(245, 166, 35, 0.03), transparent)',
        }}
      >
        {/* 左侧：状态指示点 + 工作区选择器 + 分支选择器 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
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
              flexShrink: 0,
            }}
          />
          {workspaceSelector}
          {branchSelector}
        </div>
        {/* 右侧：新建任务按钮 + 快捷键提示 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <button
            onClick={onNewSession}
            title="新建任务"
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '28px',
              height: '28px',
              border: '1px solid var(--border)',
              borderRadius: 'var(--radius-sm)',
              background: 'transparent',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              transition: 'all var(--transition-fast)',
              flexShrink: 0,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--accent)'
              e.currentTarget.style.color = 'var(--accent)'
              e.currentTarget.style.backgroundColor = 'var(--accent-soft)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--border)'
              e.currentTarget.style.color = 'var(--text-secondary)'
              e.currentTarget.style.backgroundColor = 'transparent'
            }}
          >
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 5v14M5 12h14" />
            </svg>
          </button>
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
      </div>

      {/* 对话流 */}
      <ChatStream blocks={blocks} formatDuration={formatDuration} />

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
          questionRequest={questionRequest}
          onAnswer={answerQuestion}
          permissionMode={permissionMode}
          onPermissionModeChange={onPermissionModeChange}
        />
      </div>
    </div>
  )
}

export default AIPanel
