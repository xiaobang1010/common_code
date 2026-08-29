import { useCallback, useEffect, useState } from 'react'

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

// 拉取当前工作区最近活跃 spec 的勾选进度。
// 参照 useGitStatus 的 SSE 用法但差异两点：去掉 10 秒定时轮询（spec 文档
// 只在写入时变化）、新增 onopen 重连刷新（断线重连兜底）。AI 或用户改
// tasks/checklist 都会触发 file_changed 事件驱动刷新。组件卸载关闭连接。
export function useSpecProgress(): { data: SpecProgressData | null } {
  const [data, setData] = useState<SpecProgressData | null>(null)

  const refresh = useCallback(async () => {
    try {
      const resp = await fetch('/api/spec/progress')
      const json = await resp.json()
      setData({
        spec: json.spec ?? null,
        tasks: json.tasks ?? { total: 0, done: 0, items: [] },
        checks: json.checks ?? { total: 0, done: 0, items: [] },
      })
    } catch {
      // 后端未就绪时静默忽略，保留上次数据
    }
  }, [])

  useEffect(() => {
    void refresh()
    const es = new EventSource('/api/files/events')
    es.onopen = () => void refresh()
    es.onmessage = (e) => {
      try {
        const evt = JSON.parse(e.data)
        if (evt.type === 'file_changed') void refresh()
      } catch {
        // 忽略无法解析的事件
      }
    }
    return () => es.close()
  }, [refresh])

  return { data }
}
