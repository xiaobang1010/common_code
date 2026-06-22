import type { TokenUsage } from '../../hooks/useChat'

interface Props {
  usage: TokenUsage
  windowSize?: number
}

function ContextPanel({ usage, windowSize = 200000 }: Props) {
  // 当前上下文大小（最近一次请求的 prompt_tokens）
  const current = usage.last_prompt_tokens
  // 已缓存大小（最近一次请求的 cache_creation_input_tokens）
  const cached = usage.last_cache_creation
  // 进度按当前上下文 / 窗口大小计算，超过 80% 变红
  const percent = Math.min(100, (current / windowSize) * 100)

  // 单条用量行
  const Item = ({ label, value, color }: { label: string; value: number; color: string }) => (
    <div
      style={{
        display: 'flex',
        justifyContent: 'space-between',
        fontSize: '12px',
        marginBottom: '3px',
      }}
    >
      <span style={{ color: 'var(--text-secondary)' }}>{label}</span>
      <span style={{ color, fontFamily: 'var(--font-mono)' }}>{value.toLocaleString()}</span>
    </div>
  )

  return (
    <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--border)' }}>
      <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
        上下文用量
      </div>
      <Item label="当前上下文" value={current} color="var(--text-primary)" />
      <Item label="已缓存" value={cached} color="var(--accent)" />
      {/* 进度条：当前上下文 / 窗口大小 */}
      <div style={{ marginTop: '6px' }}>
        <div
          style={{
            height: '4px',
            backgroundColor: 'var(--bg-tertiary)',
            borderRadius: '2px',
            overflow: 'hidden',
          }}
        >
          <div
            style={{
              height: '100%',
              width: `${percent}%`,
              backgroundColor: percent > 80 ? 'var(--error)' : 'var(--accent)',
              transition: 'width 0.3s',
            }}
          />
        </div>
        <div style={{ fontSize: '11px', color: 'var(--text-secondary)', marginTop: '2px' }}>
          {current.toLocaleString()} / {windowSize.toLocaleString()}
        </div>
      </div>
    </div>
  )
}

export default ContextPanel
