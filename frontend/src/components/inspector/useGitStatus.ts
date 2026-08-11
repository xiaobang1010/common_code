import { useEffect, useState } from 'react'

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
}

// 轮询拉取 git 状态的 hook：首次加载 + 每 10 秒刷新
// 概要卡（产物）与审查卡共用，避免各自维护轮询逻辑
export function useGitStatus(): GitStatusData | null {
  const [data, setData] = useState<GitStatusData | null>(null)

  useEffect(() => {
    let cancelled = false

    const fetchStatus = async () => {
      try {
        const resp = await fetch('/api/git/status')
        const json = await resp.json()
        if (cancelled) return
        setData({
          branch: json.branch || '',
          changes: json.changes || [],
          totals: json.totals || { files: 0, additions: 0, deletions: 0 },
        })
      } catch {
        // 后端未就绪时静默忽略
      }
    }

    fetchStatus()
    const timer = setInterval(fetchStatus, 10000)
    return () => {
      cancelled = true
      clearInterval(timer)
    }
  }, [])

  return data
}

// 变更状态去重：同一文件可能同时有暂存和未暂存两项，按路径合并
export function dedupeChanges(changes: GitChange[]): GitChange[] {
  const seen = new Map<string, GitChange>()
  for (const c of changes) {
    if (!seen.has(c.path)) seen.set(c.path, c)
  }
  return [...seen.values()]
}
