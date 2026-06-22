import type { PermissionRequest } from '../../hooks/useChat'

interface Props {
  request: PermissionRequest
  onResolve: (decision: 'allow' | 'deny' | 'always_allow') => void
}

function PermissionDialog({ request, onResolve }: Props) {
  return (
    // 遮罩层 - 带模糊背景
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.6)',
        backdropFilter: 'blur(4px)',
        WebkitBackdropFilter: 'blur(4px)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
        animation: 'fade-in-up 200ms ease-out',
      }}
    >
      {/* 弹窗主体 - 精致玻璃质感 */}
      <div
        style={{
          backgroundColor: 'var(--bg-elevated)',
          border: '1px solid var(--border-strong)',
          borderRadius: 'var(--radius-lg)',
          padding: '24px',
          width: '460px',
          maxWidth: '90vw',
          boxShadow: 'var(--shadow-lg), 0 0 40px rgba(245, 166, 35, 0.1)',
        }}
      >
        {/* 标题区 - 带警告图标 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginBottom: '18px' }}>
          <div
            style={{
              width: '32px',
              height: '32px',
              borderRadius: 'var(--radius-md)',
              background: 'var(--accent-soft)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              flexShrink: 0,
            }}
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2 2 22h20L12 2z" />
              <path d="M12 9v4M12 17h.01" />
            </svg>
          </div>
          <h3
            style={{
              color: 'var(--text-primary)',
              fontSize: '15px',
              fontWeight: 600,
              fontFamily: 'var(--font-ui)',
              letterSpacing: '0.2px',
            }}
          >
            权限确认
          </h3>
        </div>

        {/* 工具名 */}
        <div style={{ marginBottom: '14px' }}>
          <div
            style={{
              color: 'var(--text-tertiary)',
              fontSize: '10px',
              marginBottom: '6px',
              fontFamily: 'var(--font-mono)',
              letterSpacing: '1px',
              textTransform: 'uppercase',
            }}
          >
            工具
          </div>
          <div
            style={{
              color: 'var(--accent)',
              fontSize: '14px',
              fontFamily: 'var(--font-mono)',
              fontWeight: 500,
            }}
          >
            {request.tool_name}
          </div>
        </div>

        {/* 工具参数 */}
        <div style={{ marginBottom: '14px' }}>
          <div
            style={{
              color: 'var(--text-tertiary)',
              fontSize: '10px',
              marginBottom: '6px',
              fontFamily: 'var(--font-mono)',
              letterSpacing: '1px',
              textTransform: 'uppercase',
            }}
          >
            参数
          </div>
          <pre
            style={{
              backgroundColor: 'var(--bg-base)',
              border: '1px solid var(--border-subtle)',
              padding: '12px',
              borderRadius: 'var(--radius-md)',
              fontSize: '12px',
              fontFamily: 'var(--font-mono)',
              color: 'var(--text-primary)',
              overflow: 'auto',
              maxHeight: '180px',
              whiteSpace: 'pre-wrap',
              lineHeight: 1.6,
            }}
          >
            {JSON.stringify(request.tool_input, null, 2)}
          </pre>
        </div>

        {/* 原因 */}
        <div style={{ marginBottom: '22px' }}>
          <div
            style={{
              color: 'var(--text-tertiary)',
              fontSize: '10px',
              marginBottom: '6px',
              fontFamily: 'var(--font-mono)',
              letterSpacing: '1px',
              textTransform: 'uppercase',
            }}
          >
            原因
          </div>
          <div
            style={{
              color: 'var(--text-secondary)',
              fontSize: '13px',
              lineHeight: 1.5,
            }}
          >
            {request.reason}
          </div>
        </div>

        {/* 操作按钮 */}
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={() => onResolve('deny')}
            style={{
              padding: '8px 16px',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-md)',
              cursor: 'pointer',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              fontWeight: 500,
              backgroundColor: 'transparent',
              color: 'var(--text-secondary)',
              transition: 'all var(--transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'rgba(255, 107, 107, 0.1)'
              e.currentTarget.style.borderColor = 'var(--error)'
              e.currentTarget.style.color = 'var(--error)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent'
              e.currentTarget.style.borderColor = 'var(--border-strong)'
              e.currentTarget.style.color = 'var(--text-secondary)'
            }}
          >
            拒绝
          </button>
          <button
            onClick={() => onResolve('always_allow')}
            style={{
              padding: '8px 16px',
              border: '1px solid var(--border-strong)',
              borderRadius: 'var(--radius-md)',
              cursor: 'pointer',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              fontWeight: 500,
              backgroundColor: 'transparent',
              color: 'var(--text-secondary)',
              transition: 'all var(--transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.backgroundColor = 'var(--accent-soft)'
              e.currentTarget.style.borderColor = 'var(--accent)'
              e.currentTarget.style.color = 'var(--accent)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.backgroundColor = 'transparent'
              e.currentTarget.style.borderColor = 'var(--border-strong)'
              e.currentTarget.style.color = 'var(--text-secondary)'
            }}
          >
            总是允许
          </button>
          <button
            onClick={() => onResolve('allow')}
            style={{
              padding: '8px 18px',
              border: 'none',
              borderRadius: 'var(--radius-md)',
              cursor: 'pointer',
              fontSize: '12px',
              fontFamily: 'var(--font-ui)',
              fontWeight: 600,
              background: 'linear-gradient(135deg, var(--accent), #ff7a45)',
              color: '#1a1a1a',
              boxShadow: '0 4px 12px rgba(245, 166, 35, 0.3)',
              transition: 'all var(--transition-fast)',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.transform = 'translateY(-1px)'
              e.currentTarget.style.boxShadow = '0 6px 16px rgba(245, 166, 35, 0.4)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.transform = 'translateY(0)'
              e.currentTarget.style.boxShadow = '0 4px 12px rgba(245, 166, 35, 0.3)'
            }}
          >
            允许
          </button>
        </div>
      </div>
    </div>
  )
}

export default PermissionDialog
