import { useEffect, useRef } from 'react'

// 运行任务感知轮询：5s 拉一次 /api/runs 的会话键集，运行集合变化时回调。
// 用于发现「外部创建」的运行任务（如后台子代理完成自动唤起的父会话轮次）——
// 本地发起的对话有 SSE 驱动刷新，不依赖此轮询。回调里做 loadAllSessions()
// 即可同时刷新运行指示，并让既有的「当前会话有任务则轮询 /api/state」链路
// 自动接管实时消息渲染。
export function useRunsWatcher(enabled: boolean, onChanged: () => void) {
  const prevIdsRef = useRef<string[] | null>(null)
  const onChangedRef = useRef(onChanged)
  onChangedRef.current = onChanged

  useEffect(() => {
    if (!enabled) return
    let cancelled = false

    async function poll() {
      try {
        const resp = await fetch('/api/runs')
        if (!resp.ok || cancelled) return
        const data = await resp.json()
        if (cancelled) return
        const ids: string[] = ((data.running_session_ids ?? []) as string[]).slice().sort()
        const prev = prevIdsRef.current
        // 首次拉取只记基线不触发回调（页面刷新时运行中的任务是存量，非新唤起）
        if (prev !== null && JSON.stringify(prev) !== JSON.stringify(ids)) {
          onChangedRef.current()
        }
        prevIdsRef.current = ids
      } catch {
        // 网络异常下一轮自然重试
      }
    }

    void poll()
    const timer = window.setInterval(poll, 5000)
    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [enabled])
}
