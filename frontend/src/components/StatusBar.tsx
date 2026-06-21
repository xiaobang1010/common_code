import type { TokenUsage } from '../hooks/useChat'

interface Props {
  tokenUsage: TokenUsage
  totalCost: number
  isStreaming: boolean
  model: string
}

function StatusBar({ tokenUsage, totalCost, isStreaming, model }: Props) {
  // 合计 token 数
  const totalTokens =
    tokenUsage.input_tokens +
    tokenUsage.output_tokens +
    tokenUsage.cache_read_input_tokens +
    tokenUsage.cache_creation_input_tokens

  return (
    <div
      style={{
        height: '24px',
        backgroundColor: 'var(--accent)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        padding: '0 8px',
        fontSize: '12px',
        color: '#fff',
      }}
    >
      {/* 左侧：当前状态 */}
      <span>{isStreaming ? '思考中...' : '就绪'}</span>
      {/* 右侧：token 用量、成本、模型名 */}
      <div style={{ display: 'flex', gap: '12px' }}>
        <span>tokens: {totalTokens.toLocaleString()}</span>
        <span>$ {totalCost.toFixed(4)}</span>
        <span>model: {model || '-'}</span>
      </div>
    </div>
  )
}

export default StatusBar
