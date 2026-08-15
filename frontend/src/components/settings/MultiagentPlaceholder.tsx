// 多智能体占位分区 — 编排能力规划中，不接后端
function MultiagentPlaceholder() {
  return (
    <div
      style={{
        padding: '40px 20px',
        textAlign: 'center',
        color: 'var(--text-tertiary)',
      }}
    >
      <div style={{ fontSize: '36px', marginBottom: '16px' }}>🤖</div>
      <div style={{ fontSize: '14px', color: 'var(--text-secondary)', marginBottom: '8px' }}>
        多智能体编排能力规划中
      </div>
      <div style={{ fontSize: '12px', lineHeight: 1.6, maxWidth: '400px', margin: '0 auto' }}>
        多智能体（teammate 协作、任务流转、结果聚合）目前作为运行时编排能力存在，
        尚未提供设置面板可管理的配置项。
        <br />
        详见 spec：<code style={{ color: 'var(--code-text)', fontFamily: 'var(--font-mono)' }}>
          evolve-subagent-multiagent-claude-code
        </code>
      </div>
    </div>
  )
}

export default MultiagentPlaceholder
