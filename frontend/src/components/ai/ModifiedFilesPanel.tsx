import { useState, useEffect } from 'react'

interface FileChange {
  path: string
  status: string
}

function ModifiedFilesPanel() {
  const [changes, setChanges] = useState<FileChange[]>([])
  const [branch, setBranch] = useState('')

  // 拉取 git 变更状态
  const fetchStatus = async () => {
    try {
      const resp = await fetch('/api/git/status')
      const data = await resp.json()
      setBranch(data.branch || '')
      setChanges(data.changes || [])
    } catch {
      // 后端未就绪时静默忽略
    }
  }

  // 首次加载 + 定期刷新
  useEffect(() => {
    fetchStatus()
    const timer = setInterval(fetchStatus, 10000)
    return () => clearInterval(timer)
  }, [])

  // 按变更类型返回颜色
  const getStatusColor = (status: string) => {
    if (status === 'added' || status === 'untracked') return 'var(--success)'
    if (status === 'modified') return 'var(--warning)'
    if (status === 'deleted') return 'var(--error)'
    return 'var(--text-secondary)'
  }

  return (
    <div style={{ padding: '8px 12px' }}>
      <div style={{ fontSize: '12px', color: 'var(--text-secondary)', marginBottom: '6px' }}>
        变更文件 {branch && `(${branch})`}
      </div>
      {changes.length === 0 ? (
        <div style={{ fontSize: '12px', color: 'var(--text-secondary)' }}>无变更</div>
      ) : (
        changes.map((c, i) => (
          <div
            key={i}
            style={{
              display: 'flex',
              justifyContent: 'space-between',
              fontSize: '12px',
              marginBottom: '2px',
            }}
          >
            <span
              style={{
                color: 'var(--text-primary)',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}
            >
              {c.path}
            </span>
            <span
              style={{
                color: getStatusColor(c.status),
                marginLeft: '8px',
                flexShrink: 0,
              }}
            >
              {c.status}
            </span>
          </div>
        ))
      )}
    </div>
  )
}

export default ModifiedFilesPanel
