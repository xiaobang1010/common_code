import { useEffect, useState } from 'react'

// 单个文件变更项
interface GitChange {
  path: string
  status: 'modified' | 'added' | 'deleted'
}

// Git 状态接口返回结构
interface GitStatusData {
  branch: string
  changes: GitChange[]
}

// 根据变更状态返回对应颜色
function getStatusColor(status: GitChange['status']): string {
  switch (status) {
    case 'added':
      return 'var(--success)' // 绿色
    case 'modified':
      return 'var(--warning)' // 黄色
    case 'deleted':
      return 'var(--error)' // 红色
  }
}

// 根据变更状态返回对应字母标识（类似 VS Code 的 SCM 标记）
function getStatusLabel(status: GitChange['status']): string {
  switch (status) {
    case 'added':
      return 'A'
    case 'modified':
      return 'M'
    case 'deleted':
      return 'D'
  }
}

function GitStatus() {
  const [data, setData] = useState<GitStatusData | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  // 打开面板时自动加载 Git 状态
  useEffect(() => {
    const loadStatus = async () => {
      try {
        const res = await fetch('/api/git/status')
        const json = await res.json()
        setData(json)
      } catch (e) {
        setError('加载 Git 状态失败')
        console.error(e)
      } finally {
        setLoading(false)
      }
    }
    loadStatus()
  }, [])

  if (loading) {
    return (
      <div style={{ padding: '8px', color: 'var(--text-secondary)', fontSize: '13px' }}>
        加载中...
      </div>
    )
  }
  if (error) {
    return (
      <div style={{ padding: '8px', color: 'var(--error)', fontSize: '13px' }}>{error}</div>
    )
  }

  const changes = data?.changes || []

  return (
    <div style={{ flex: 1, overflow: 'auto', display: 'flex', flexDirection: 'column' }}>
      {/* 当前分支 */}
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
          padding: '8px 12px',
          color: 'var(--text-primary)',
          fontSize: '13px',
          borderBottom: '1px solid var(--border)',
          flexShrink: 0,
        }}
      >
        <span>🌿</span>
        <span>{data?.branch || '未知分支'}</span>
      </div>
      {/* 变更文件列表 */}
      {changes.length === 0 ? (
        <div
          style={{
            padding: '8px 12px',
            color: 'var(--text-secondary)',
            fontSize: '13px',
          }}
        >
          没有文件变更
        </div>
      ) : (
        <div style={{ padding: '4px 0' }}>
          {changes.map((change) => (
            <div
              key={change.path}
              title={change.path}
              style={{
                display: 'flex',
                alignItems: 'center',
                gap: '6px',
                padding: '0 12px',
                height: '24px',
                fontSize: '13px',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
              }}
            >
              {/* 状态标识 */}
              <span
                style={{
                  width: '14px',
                  textAlign: 'center',
                  flexShrink: 0,
                  color: getStatusColor(change.status),
                  fontWeight: 'bold',
                }}
              >
                {getStatusLabel(change.status)}
              </span>
              {/* 文件路径，超出部分省略 */}
              <span
                style={{
                  color: getStatusColor(change.status),
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                }}
              >
                {change.path}
              </span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

export default GitStatus
