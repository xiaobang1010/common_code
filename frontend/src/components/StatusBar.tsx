import type { TokenUsage } from '../hooks/useChat'

interface Props {
  tokenUsage: TokenUsage
  totalCost: number
  isStreaming: boolean
  model: string
}

function StatusBar({ tokenUsage, totalCost, isStreaming, model }: Props) {
  const totalTokens =
    tokenUsage.input_tokens +
    tokenUsage.output_tokens +
    tokenUsage.cache_read_input_tokens +
    tokenUsage.cache_creation_input_tokens

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

      {/* 右侧：模型、token、成本 */}
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
        <span title="Token 用量">
          <span style={{ color: 'var(--text-tertiary)' }}>tokens</span>{' '}
          <span style={{ color: 'var(--text-primary)' }}>{totalTokens.toLocaleString()}</span>
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
