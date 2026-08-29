import { useCallback, useEffect, useRef, useState } from 'react'
import { useWorkspaceSignal } from '../../stores/useWorkspaceSignal'

// git 变更项（含行数统计，由 /api/git/status 返回）
export interface GitChange {
  path: string
  status: string
  staged: boolean
  additions: number
  deletions: number
}

// 全局变更汇总
export interface GitTotals {
  files: number
  additions: number
  deletions: number
}

export interface GitStatusData {
  branch: string
  changes: GitChange[]
  totals: GitTotals
  // 工作区相对仓库根的路径前缀（正斜杠，工作区即仓库根为空串）：
  // changes[].path 是仓库根相对口径，剥掉此前缀即得工作区相对路径
  repoPrefix: string
}

// 轮询拉取 git 状态的 hook：首次加载 + 每 10 秒刷新 + SSE 文件事件即时刷新；
// 工作区切换（信号路径变化）时清旧数据并立即重取——/api/git/status 按服务端
// 全局「当前工作区」返回，切换动作本身不产生文件事件，不主动重取会一直
// 显示上一个工作区的数据。概要卡（产物）与审查卡共用，避免各自维护轮询
// 逻辑；refresh 供工具栏手动刷新
export function useGitStatus(): { data: GitStatusData | null; refresh: () => void } {
  const [data, setData] = useState<GitStatusData | null>(null)
  // 请求代号：每次发起递增，响应回来对不上号说明已发出更新的请求（如快速
  // 连续切换工作区），过期响应直接丢弃，避免旧工作区数据覆盖新工作区
  const genRef = useRef(0)

  const refresh = useCallback(async () => {
    const gen = ++genRef.current
    try {
      const resp = await fetch('/api/git/status')
      const json = await resp.json()
      if (gen !== genRef.current) return
      setData({
        branch: json.branch || '',
        changes: json.changes || [],
        totals: json.totals || { files: 0, additions: 0, deletions: 0 },
        repoPrefix: json.repo_prefix || '',
      })
    } catch {
      // 后端未就绪时静默忽略
    }
  }, [])

  // 当前工作区路径信号：作为 effect 依赖，切换工作区时重开轮询并立即重取
  const workspacePath = useWorkspaceSignal((s) => s.currentPath)

  useEffect(() => {
    // 切换工作区先清数据：重取完成前不闪现上一个工作区的变更数
    setData(null)
    void refresh()
    const timer = setInterval(() => void refresh(), 10000)

    // 文件变更事件到达时即时刷新 git 状态，不必被动等 10 秒轮询
    const es = new EventSource('/api/files/events')
    es.onmessage = (e) => {
      try {
        const data = JSON.parse(e.data)
        if (data.type === 'file_changed') void refresh()
      } catch {
        // 忽略无法解析的事件
      }
    }

    return () => {
      clearInterval(timer)
      es.close()
    }
  }, [refresh, workspacePath])

  return { data, refresh: () => void refresh() }
}

// 变更状态去重：同一文件可能同时有暂存和未暂存两项，按路径合并
export function dedupeChanges(changes: GitChange[]): GitChange[] {
  const seen = new Map<string, GitChange>()
  for (const c of changes) {
    if (!seen.has(c.path)) seen.set(c.path, c)
  }
  return [...seen.values()]
}
