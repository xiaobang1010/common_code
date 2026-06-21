import type { TokenUsage } from '../../hooks/useChat'

interface Props {
  usage: TokenUsage
  windowSize?: number
}

function ContextPanel({ usage, windowSize = 200000 }: Props) {
  // 合计已用 token
  const total =
    usage.input_tokens +
    usage.output_tokens +
    usage.cache_read_input_tokens +
    usage.cache_creation_input_tokens
  const percent = Math.min(100, (total / windowSize) * 100)

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
      <span style={{ color }}>{value.toLocaleString()}</span>
    </div>
  )

  return (
    <div style={{ padding: '8px 12px', borderBottom: '1px solid var(--border)' }}>
      <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
        Token 用量
      </div>
      <Item label="输入" value={usage.input_tokens} color="var(--text-primary)" />
      <Item label="输出" value={usage.output_tokens} color="var(--success)" />
      <Item label="缓存读取" value={usage.cache_read_input_tokens} color="var(--warning)" />
      <Item label="缓存写入" value={usage.cache_creation_input_tokens} color="var(--accent)" />
      {/* 进度条：已用 / 窗口大小 */}
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
          {total.toLocaleString()} / {windowSize.toLocaleString()}
        </div>
      </div>
    </div>
  )
}

export default ContextPanel
