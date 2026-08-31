import { useCallback, useEffect, useRef, useState } from 'react'
import { useWorkspaceSignal } from '../stores/useWorkspaceSignal'

// 单条清单项：tasks.md / checklist.md 里的一行勾选项
export interface SpecCheckItem {
  text: string
  done: boolean
}

// 清单组：任务（tasks.md）或验证（checklist.md）
export interface SpecChecklist {
  total: number
  done: number
  items: SpecCheckItem[]
}

export interface SpecProgressData {
  spec: { name: string; path: string } | null
  tasks: SpecChecklist
  checks: SpecChecklist
}

// 共享进展口径：优先任务清单，任务为空回退验证清单，两者皆空返回 null。
// 胶囊卡收起态与概要卡共用此函数，保证两处「进展」数字口径一致；
// 展开态卡头不走这里（跟随「任务|验证」分组切换显示当前分组）
export function deriveProgress(
  data: SpecProgressData | null,
): { done: number; total: number; source: 'tasks' | 'checks' } | null {
  if (!data) return null
  if (data.tasks.total > 0) return { done: data.tasks.done, total: data.tasks.total, source: 'tasks' }
  if (data.checks.total > 0) return { done: data.checks.done, total: data.checks.total, source: 'checks' }
  return null
}

// 拉取当前会话归属 spec 的勾选进度。
// 参照 useGitStatus 的 SSE 用法但差异两点：去掉 10 秒定时轮询（spec 文档
// 只在写入时变化）、新增 onopen 重连刷新（断线重连兜底）。AI 或用户改
// tasks/checklist 都会触发 file_changed 事件驱动刷新。组件卸载关闭连接。
// 会话与工作区变化（sessionId / 工作区信号路径）时清旧数据并立即重取：
// 进展精确到会话（后端从会话消息识别归属 spec），切换会话必须换数据源。
export function useSpecProgress(sessionId: string | null): { data: SpecProgressData | null } {
  const [data, setData] = useState<SpecProgressData | null>(null)
  // 请求代号：每次发起递增，响应回来对不上号说明已发出更新的请求（如快速
  // 连续切换会话/工作区），过期响应直接丢弃，避免旧会话的进展覆盖新会话
  const genRef = useRef(0)

  const refresh = useCallback(async (sid: string | null) => {
    const gen = ++genRef.current
    try {
      const query = sid ? `?session_id=${encodeURIComponent(sid)}` : ''
      const resp = await fetch(`/api/spec/progress${query}`)
      const json = await resp.json()
      if (gen !== genRef.current) return
      setData({
        spec: json.spec ?? null,
        tasks: json.tasks ?? { total: 0, done: 0, items: [] },
        checks: json.checks ?? { total: 0, done: 0, items: [] },
      })
    } catch {
      // 后端未就绪时静默忽略，保留上次数据
    }
  }, [])

  // 当前工作区路径信号：与 sessionId 一同为 effect 依赖，切换即重取
  const workspacePath = useWorkspaceSignal((s) => s.currentPath)

  useEffect(() => {
    // 切换会话/工作区先清数据：重取完成前不显示上一个会话的进展
    setData(null)
    void refresh(sessionId)
    const es = new EventSource('/api/files/events')
    es.onopen = () => void refresh(sessionId)
    es.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data)
        if (evt.type === 'file_changed') void refresh(sessionId)
      } catch {
        // 忽略无法解析的事件
      }
    }
    return () => es.close()
  }, [refresh, sessionId, workspacePath])

  return { data }
}
