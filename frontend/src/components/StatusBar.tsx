import { useChatStore } from '../stores/useChatStore'

// 窗口大小上限（展示"当前上下文/窗口"用，无后端来源时用默认值）
const DEFAULT_WINDOW_SIZE = 200000

function StatusBar() {
  // 局部订阅：token 用量/成本/模型/流式状态变化时才重渲
  const tokenUsage = useChatStore(s => s.tokenUsage)
  const totalCost = useChatStore(s => s.totalCost)
  const isStreaming = useChatStore(s => s.isStreaming)
  const model = useChatStore(s => s.model)

  return (
    <div
      style={{
        height: '26px',
        backgroundColor: 'var(--bg-base)',
        borderTop: '1px solid var(--border-subtle)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 14px',
        fontSize: '11px',
        fontFamily: 'var(--font-mono)',
        color: 'var(--text-secondary)',
        letterSpacing: '0.2px',
      }}
    >
      {/* 左侧：状态 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
        <span
          style={{
            width: '6px',
            height: '6px',
            borderRadius: '50%',
            backgroundColor: isStreaming ? 'var(--accent)' : 'var(--success)',
            boxShadow: isStreaming
              ? '0 0 6px var(--accent-glow)'
              : '0 0 4px rgba(78, 201, 176, 0.4)',
            animation: isStreaming ? 'breathe 1.4s ease-in-out infinite' : 'none',
          }}
        />
        <span style={{ color: isStreaming ? 'var(--accent)' : 'var(--text-secondary)' }}>
          {isStreaming ? '思考中' : '就绪'}
        </span>
      </div>

      {/* 右侧：模型、上下文、缓存、成本 */}
      <div style={{ display: 'flex', gap: '16px', alignItems: 'center' }}>
        <span title="当前模型">
          <span style={{ color: 'var(--text-tertiary)' }}>model</span>{' '}
          <span style={{ color: 'var(--text-primary)' }}>{model || '-'}</span>
        </span>
        <span
          style={{
            width: '1px',
            height: '12px',
            backgroundColor: 'var(--border)',
          }}
        />
        <span title="当前上下文大小 / 窗口大小">
          <span style={{ color: 'var(--text-tertiary)' }}>上下文</span>{' '}
          <span style={{ color: 'var(--text-primary)' }}>
            {tokenUsage.last_prompt_tokens.toLocaleString()} / {DEFAULT_WINDOW_SIZE.toLocaleString()}
          </span>
        </span>
        <span
          style={{
            width: '1px',
            height: '12px',
            backgroundColor: 'var(--border)',
          }}
        />
        <span title="已缓存 token">
          <span style={{ color: 'var(--text-tertiary)' }}>缓存</span>{' '}
          <span style={{ color: 'var(--accent)' }}>
            {tokenUsage.last_cache_creation.toLocaleString()}
          </span>
        </span>
        <span
          style={{
            width: '1px',
            height: '12px',
            backgroundColor: 'var(--border)',
          }}
        />
        <span title="本次会话成本">
          <span style={{ color: 'var(--text-tertiary)' }}>cost</span>{' '}
          <span style={{ color: 'var(--accent)' }}>$ {totalCost.toFixed(4)}</span>
        </span>
      </div>
    </div>
  )
}

export default StatusBar
