import ChatStream from './ai/ChatStream'
import ChatInput from './ai/ChatInput'
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
  // 右侧检查器面板是否可见
  inspectorVisible: boolean
  // 切换右侧检查器面板显隐
  onToggleInspector: () => void
  // 打开设置 Modal
  onOpenSettings: () => void
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
  inspectorVisible,
  onToggleInspector,
  onOpenSettings,
}: AIPanelProps) {
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
        {/* 右侧：面板开关 + 设置 + 新建任务按钮 + 快捷键提示 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          {/* 检查器面板开关：打开/隐藏右侧概要、终端、文件、审查卡片 */}
          <button
            onClick={onToggleInspector}
            title={inspectorVisible ? '隐藏面板' : '打开面板'}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '28px',
              height: '28px',
              border: '1px solid',
              borderColor: inspectorVisible ? 'var(--accent)' : 'var(--border)',
              borderRadius: 'var(--radius-sm)',
              background: inspectorVisible ? 'var(--accent-soft)' : 'transparent',
              color: inspectorVisible ? 'var(--accent)' : 'var(--text-secondary)',
              cursor: 'pointer',
              transition: 'all var(--transition-fast)',
              flexShrink: 0,
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--accent)'
              e.currentTarget.style.color = 'var(--accent)'
            }}
            onMouseLeave={(e) => {
              if (!inspectorVisible) {
                e.currentTarget.style.borderColor = 'var(--border)'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }
            }}
          >
            {/* 侧边面板图标：主区 + 右侧栏 */}
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="16" rx="2" />
              <path d="M15 4v16" />
            </svg>
          </button>
          {/* 设置按钮：打开设置 Modal（入口从原活动栏迁移至此） */}
          <button
            onClick={onOpenSettings}
            title="设置"
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
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
              <circle cx="12" cy="12" r="3" />
              <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1z" />
            </svg>
          </button>
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
