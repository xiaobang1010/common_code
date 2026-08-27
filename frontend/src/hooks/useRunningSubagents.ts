import { useEffect, useState } from 'react'

// 运行中子代理的轻量信息（/api/subagents 返回体的子集）
export interface RunningSubagent {
  agent_id: string
  description: string
  status: string
}

// 轮询当前会话运行中/等待中子代理的 hook：供状态胶囊卡「智能体」区块使用。
// 无会话或无运行项时返回空数组，调用方据此隐藏区块。
// 每 3 秒轮询一次，组件卸载即停。
export function useRunningSubagents(sessionId: string | null): { running: RunningSubagent[] } {
  const [running, setRunning] = useState<RunningSubagent[]>([])

  useEffect(() => {
    if (!sessionId) {
      setRunning([])
      return
    }
    let cancelled = false
    async function poll() {
      if (cancelled) return
      try {
        const resp = await fetch(`/api/subagents?session_id=${encodeURIComponent(sessionId ?? '')}`)
        if (!resp.ok) return
        const data = await resp.json()
        if (cancelled) return
        const list = (data.subagents ?? []) as Array<{ agent_id?: string; description?: string; status?: string }>
        setRunning(
          list
            .filter((s) => s.status === 'running' || s.status === 'pending')
            .map((s) => ({
              agent_id: s.agent_id ?? '',
              description: s.description ?? '(未命名子任务)',
              status: s.status ?? '',
            }))
            .filter((s) => s.agent_id !== ''),
        )
      } catch {
        // 网络异常下一轮自然重试
      }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), 3000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [sessionId])

  return { running }
}
