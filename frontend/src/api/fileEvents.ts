// 文件变更事件共享订阅：全应用只对 /api/files/events 建一条 EventSource 长连接。
// 页面走 HTTP/1.1 时浏览器对同一地址最多 6 条并发连接，过去 4 个组件各自开一条
// 常驻连接，普通请求只剩一两条额度，AI 写盘的事件风暴下刷新请求全部排队
// （表现为文件树「刷新中」长期不灭）。收敛到单例后并发额度归还给业务请求。
// 备注：开发态 Vite 热更新重载本模块会短暂多建一条连接，生产构建无此现象。

// 事件载荷：后端广播 type=file_changed（含变更文件路径）与 type=heartbeat 心跳
export interface FileEvent {
  type: string
  path?: string
}

interface Subscriber {
  onEvent: (evt: FileEvent) => void
  onOpen?: () => void
}

const subscribers = new Set<Subscriber>()
let source: EventSource | null = null

// 懒创建单例连接；连接常驻不主动关闭（一条连接成本可忽略，页面卸载由浏览器回收）
function ensureSource(): EventSource {
  if (source) return source
  const es = new EventSource('/api/files/events')
  es.onopen = () => {
    // 断线自动重连成功（含首连）时通知订阅方做兜底刷新
    for (const sub of subscribers) sub.onOpen?.()
  }
  es.onmessage = (e) => {
    let evt: FileEvent
    try {
      evt = JSON.parse(e.data)
    } catch {
      return // 忽略无法解析的事件
    }
    for (const sub of subscribers) sub.onEvent(evt)
  }
  source = es
  return es
}

// 逐条订阅：回调拿到解析后的原始事件，适合依赖具体载荷（如变更文件路径）的消费方。
// 返回退订函数，供组件 useEffect cleanup 调用
export function subscribeFileEvents(
  onEvent: (evt: FileEvent) => void,
  opts?: { onOpen?: () => void },
): () => void {
  ensureSource()
  const sub: Subscriber = { onEvent, onOpen: opts?.onOpen }
  subscribers.add(sub)
  return () => {
    subscribers.delete(sub)
  }
}

// 防抖订阅：400ms 内连续到达的多条 file_changed 合并为一次回调（trailing 边沿
// 触发），仅供「收到变更就重拉数据」类消费方，把事件风暴摊薄成低频刷新；
// 需要事件载荷的请用 subscribeFileEvents。onOpen 不防抖，重连兜底立即生效。
// 返回退订函数，退订时连带取消未触发的挂起回调
export function subscribeFileEventsDebounced(
  onEvent: () => void,
  opts?: { onOpen?: () => void; delayMs?: number },
): () => void {
  const delay = opts?.delayMs ?? 400
  let timer: ReturnType<typeof setTimeout> | null = null
  const cancelPending = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
  }
  const unsubscribe = subscribeFileEvents(
    (evt) => {
      if (evt.type !== 'file_changed') return
      cancelPending()
      timer = setTimeout(() => {
        timer = null
        onEvent()
      }, delay)
    },
    opts,
  )
  return () => {
    cancelPending()
    unsubscribe()
  }
}
