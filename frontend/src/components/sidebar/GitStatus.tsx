import { useCallback, useEffect, useState } from 'react'

// 单个文件变更项
interface GitChange {
  path: string
  status: 'modified' | 'added' | 'deleted'
  staged: boolean
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
  const [commitMessage, setCommitMessage] = useState('')
  const [committing, setCommitting] = useState(false)
  const [actionError, setActionError] = useState('')

  // 加载 Git 状态
  const loadStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/git/status')
      const json = await res.json()
      setData(json)
      setError('')
    } catch (e) {
      setError('加载 Git 状态失败')
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  // 打开面板时自动加载
  useEffect(() => {
    loadStatus()
  }, [loadStatus])

  // 暂存文件
  const handleStage = async (path: string) => {
    setActionError('')
    try {
      const res = await fetch('/api/git/stage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      })
      const json = await res.json()
      if (!json.ok) {
        setActionError(json.error || '暂存失败')
        return
      }
      await loadStatus()
    } catch (e) {
      setActionError('暂存失败')
      console.error(e)
    }
  }

  // 取消暂存
  const handleUnstage = async (path: string) => {
    setActionError('')
    try {
      const res = await fetch('/api/git/unstage', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path }),
      })
      const json = await res.json()
      if (!json.ok) {
        setActionError(json.error || '取消暂存失败')
        return
      }
      await loadStatus()
    } catch (e) {
      setActionError('取消暂存失败')
      console.error(e)
    }
  }

  // 提交
  const handleCommit = async () => {
    const message = commitMessage.trim()
    if (!message) {
      return
    }
    setCommitting(true)
    setActionError('')
    try {
      const res = await fetch('/api/git/commit', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message }),
      })
      const json = await res.json()
      if (!json.ok) {
        setActionError(json.error || '提交失败')
        return
      }
      setCommitMessage('')
      await loadStatus()
    } catch (e) {
      setActionError('提交失败')
      console.error(e)
    } finally {
      setCommitting(false)
    }
  }

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
  const unstaged = changes.filter((c) => !c.staged)
  const staged = changes.filter((c) => c.staged)

  // 渲染单行变更，isStaged 决定右侧按钮是 - 还是 +
  const renderChangeRow = (change: GitChange, isStaged: boolean) => {
    const color = getStatusColor(change.status)
    return (
      <div
        key={`${isStaged ? 's' : 'u'}-${change.path}`}
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
            color,
            fontWeight: 'bold',
          }}
        >
          {getStatusLabel(change.status)}
        </span>
        {/* 文件路径，超出部分省略 */}
        <span
          style={{
            color,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            flex: 1,
          }}
        >
          {change.path}
        </span>
        {/* 操作按钮：暂存区显示 -（取消暂存），工作区显示 +（暂存） */}
        <button
          onClick={() => (isStaged ? handleUnstage(change.path) : handleStage(change.path))}
          title={isStaged ? '取消暂存' : '暂存'}
          style={{
            border: 'none',
            background: 'transparent',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: '16px',
            width: '18px',
            height: '18px',
            flexShrink: 0,
            padding: 0,
            lineHeight: 1,
          }}
        >
          {isStaged ? '−' : '+'}
        </button>
      </div>
    )
  }

  // 提交按钮是否可用
  const commitDisabled = committing || !commitMessage.trim()

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
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

      {/* 变更列表 */}
      <div style={{ flex: 1, overflow: 'auto' }}>
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
          <>
            {/* 未暂存的更改 */}
            {unstaged.length > 0 && (
              <div style={{ padding: '4px 0' }}>
                <div
                  style={{
                    padding: '4px 12px',
                    color: 'var(--text-secondary)',
                    fontSize: '11px',
                    textTransform: 'uppercase',
                  }}
                >
                  未暂存的更改
                </div>
                {unstaged.map((c) => renderChangeRow(c, false))}
              </div>
            )}
            {/* 暂存的更改 */}
            {staged.length > 0 && (
              <div style={{ padding: '4px 0' }}>
                <div
                  style={{
                    padding: '4px 12px',
                    color: 'var(--text-secondary)',
                    fontSize: '11px',
                    textTransform: 'uppercase',
                  }}
                >
                  暂存的更改
                </div>
                {staged.map((c) => renderChangeRow(c, true))}
              </div>
            )}
          </>
        )}
        {actionError && (
          <div style={{ padding: '4px 12px', color: 'var(--error)', fontSize: '12px' }}>
            {actionError}
          </div>
        )}
      </div>

      {/* 底部提交区 */}
      <div
        style={{
          borderTop: '1px solid var(--border)',
          padding: '8px',
          flexShrink: 0,
          display: 'flex',
          flexDirection: 'column',
          gap: '6px',
        }}
      >
        <textarea
          value={commitMessage}
          onChange={(e) => setCommitMessage(e.target.value)}
          placeholder="提交信息（Ctrl+Enter 提交）"
          onKeyDown={(e) => {
            if ((e.ctrlKey || e.metaKey) && e.key === 'Enter') {
              handleCommit()
            }
          }}
          rows={2}
          style={{
            width: '100%',
            boxSizing: 'border-box',
            resize: 'none',
            backgroundColor: 'var(--bg-primary)',
            border: '1px solid var(--border)',
            color: 'var(--text-primary)',
            padding: '4px 6px',
            fontSize: '13px',
            outline: 'none',
            borderRadius: '2px',
          }}
        />
        <button
          onClick={handleCommit}
          disabled={commitDisabled}
          style={{
            width: '100%',
            border: 'none',
            backgroundColor: commitDisabled ? 'var(--bg-primary)' : 'var(--accent)',
            color: commitDisabled ? 'var(--text-secondary)' : '#fff',
            padding: '6px 8px',
            fontSize: '13px',
            cursor: commitDisabled ? 'not-allowed' : 'pointer',
            borderRadius: '2px',
          }}
        >
          {committing ? '提交中...' : '提交'}
        </button>
      </div>
    </div>
  )
}

export default GitStatus
