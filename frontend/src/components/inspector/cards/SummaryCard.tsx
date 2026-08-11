import type { TokenUsage } from '../../../hooks/useChat'
import { useGitStatus, dedupeChanges } from '../useGitStatus'

interface SummaryCardProps {
  // 当前会话工作块数量
  blockCount: number
  // token 用量（计算上下文占比）
  usage: TokenUsage
}

// 上下文窗口大小
const WINDOW_SIZE = 200000

// 分区标题
function SectionTitle({ children }: { children: string }) {
  return (
    <div
      style={{
        fontSize: '11px',
        color: 'var(--text-tertiary)',
        letterSpacing: '0.5px',
        margin: '10px 12px 4px',
        fontFamily: 'var(--font-ui)',
      }}
    >
      {children}
    </div>
  )
}

// 空数据占位文案
function Empty({ text }: { text: string }) {
  return (
    <div style={{ fontSize: '12px', color: 'var(--text-tertiary)', padding: '2px 12px' }}>
      {text}
    </div>
  )
}

// 概要卡：进展（会话工作块）/ 产物（git 变更文件）/ 引用（上下文用量）
function SummaryCard({ blockCount, usage }: SummaryCardProps) {
  const gitStatus = useGitStatus()
  // 产物只列文件：git 会把新增目录也报为变更项，过滤掉
  const artifacts = gitStatus
    ? dedupeChanges(gitStatus.changes).filter((c) => !c.path.endsWith('/'))
    : []

  // 上下文占比：当前 prompt tokens / 窗口大小，超过 80% 变红
  const current = usage.last_prompt_tokens
  const percent = Math.min(100, (current / WINDOW_SIZE) * 100)

  return (
    <div style={{ paddingBottom: '10px' }}>
      {/* 进展 */}
      <SectionTitle>进展</SectionTitle>
      {blockCount > 0 ? (
        <div style={{ fontSize: '12px', color: 'var(--text-primary)', padding: '2px 12px' }}>
          已完成 {blockCount} 个工作块
        </div>
      ) : (
        <Empty text="暂无进展" />
      )}

      {/* 产物 */}
      <SectionTitle>产物</SectionTitle>
      {artifacts.length === 0 ? (
        <Empty text="暂无产物" />
      ) : (
        artifacts.map((c) => (
          <div
            key={c.path}
            style={{
              fontSize: '12px',
              color: 'var(--text-secondary)',
              padding: '1px 12px',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
              fontFamily: 'var(--font-mono)',
            }}
            title={c.path}
          >
            {c.path}
          </div>
        ))
      )}

      {/* 引用：上下文用量 */}
      <SectionTitle>引用</SectionTitle>
      <div style={{ padding: '4px 12px 0' }}>
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
        <div style={{ fontSize: '11px', color: 'var(--text-tertiary)', marginTop: '3px' }}>
          上下文 {current.toLocaleString()} / {WINDOW_SIZE.toLocaleString()}
        </div>
      </div>
    </div>
  )
}

export default SummaryCard
