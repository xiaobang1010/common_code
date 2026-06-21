import type { PermissionRequest } from '../../hooks/useChat'

interface Props {
  request: PermissionRequest
  onResolve: (decision: 'allow' | 'deny' | 'always_allow') => void
}

function PermissionDialog({ request, onResolve }: Props) {
  return (
    // 遮罩层
    <div
      style={{
        position: 'fixed',
        top: 0,
        left: 0,
        right: 0,
        bottom: 0,
        backgroundColor: 'rgba(0, 0, 0, 0.5)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        zIndex: 1000,
      }}
    >
      {/* 弹窗主体 */}
      <div
        style={{
          backgroundColor: 'var(--bg-secondary)',
          border: '1px solid var(--border)',
          borderRadius: '8px',
          padding: '20px',
          width: '440px',
          maxWidth: '90vw',
        }}
      >
        <h3
          style={{
            marginBottom: '12px',
            color: 'var(--text-primary)',
            fontSize: '15px',
          }}
        >
          权限确认
        </h3>

        {/* 工具名 */}
        <div style={{ marginBottom: '8px' }}>
          <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' }}>
            工具
          </div>
          <div style={{ color: 'var(--text-primary)', fontSize: '13px' }}>
            {request.tool_name}
          </div>
        </div>

        {/* 工具参数，JSON 格式化展示 */}
        <div style={{ marginBottom: '8px' }}>
          <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' }}>
            参数
          </div>
          <pre
            style={{
              backgroundColor: 'var(--bg-tertiary)',
              padding: '8px',
              borderRadius: '4px',
              fontSize: '12px',
              color: 'var(--text-primary)',
              overflow: 'auto',
              maxHeight: '200px',
              whiteSpace: 'pre-wrap',
            }}
          >
            {JSON.stringify(request.tool_input, null, 2)}
          </pre>
        </div>

        {/* 原因 */}
        <div style={{ marginBottom: '16px' }}>
          <div style={{ color: 'var(--text-secondary)', fontSize: '12px', marginBottom: '4px' }}>
            原因
          </div>
          <div style={{ color: 'var(--text-primary)', fontSize: '13px' }}>{request.reason}</div>
        </div>

        {/* 三个操作按钮 */}
        <div style={{ display: 'flex', gap: '8px', justifyContent: 'flex-end' }}>
          <button
            onClick={() => onResolve('deny')}
            style={{
              padding: '6px 16px',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '13px',
              backgroundColor: 'var(--error)',
              color: '#fff',
            }}
          >
            拒绝
          </button>
          <button
            onClick={() => onResolve('always_allow')}
            style={{
              padding: '6px 16px',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '13px',
              backgroundColor: 'var(--accent)',
              color: '#fff',
            }}
          >
            总是允许
          </button>
          <button
            onClick={() => onResolve('allow')}
            style={{
              padding: '6px 16px',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              fontSize: '13px',
              backgroundColor: 'var(--success)',
              color: '#fff',
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
