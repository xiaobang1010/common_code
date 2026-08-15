// 标题栏记忆状态图标：呼吸灯右侧的大脑
// 暗色=记忆未启用/未就绪（含加载失败），蓝色脉冲=模型加载中，蓝色常亮=加载成功
// 轮询 GET /api/memory/feature；停止条件 = enabled && available 同时满足；
// 关闭开关后（store 通知 enabled 变化）重启轮询并回到暗色

import { useEffect, useState } from 'react'
import { memoryApi } from '../api/client'
import { useSettingsStore } from '../stores/useSettingsStore'

type BrainState = 'off' | 'loading' | 'ready'

const TITLES: Record<BrainState, string> = {
  off: '记忆未启用',
  loading: '记忆加载中…',
  ready: '记忆就绪',
}

function BrainStatusIcon() {
  const memoryEnabled = useSettingsStore((s) => s.memoryEnabled)
  const setMemoryEnabled = useSettingsStore((s) => s.setMemoryEnabled)
  const [state, setState] = useState<BrainState>('off')

  // 轮询 feature 状态。依赖 memoryEnabled：开启时开始轮询看加载进度；
  // 就绪后停止；关闭时（store 通知）重启轮询并回暗色
  useEffect(() => {
    let cancelled = false
    let timer: number | undefined

    const tick = async () => {
      try {
        const f = await memoryApi.feature()
        if (cancelled) return
        // 回写 store：设置面板开关与图标共享同一状态通道
        if (f.enabled !== useSettingsStore.getState().memoryEnabled) {
          setMemoryEnabled(f.enabled)
        }
        if (f.enabled && f.available) {
          setState('ready')
          return // 加载成功：停止轮询，图标常驻
        }
        setState(f.enabled && f.loading ? 'loading' : 'off')
        timer = window.setTimeout(tick, 2000)
      } catch {
        // 网络瞬时失败：保持当前状态，下轮重试
        if (!cancelled) timer = window.setTimeout(tick, 2000)
      }
    }
    tick()

    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
    }
  }, [memoryEnabled, setMemoryEnabled])

  const color = state === 'off' ? 'var(--text-tertiary)' : 'var(--info)'
  const animation = state === 'loading' ? 'breathe 1.4s ease-in-out infinite' : 'none'

  return (
    <span title={TITLES[state]} style={{ display: 'flex', alignItems: 'center', flexShrink: 0 }}>
      <svg
        width="14"
        height="14"
        viewBox="0 0 24 24"
        fill="none"
        stroke={color}
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ animation, color, transition: 'all var(--transition-fast)' }}
      >
        {/* 大脑轮廓 + 中央折线 */}
        <path d="M9.5 2.5a3.2 3.2 0 0 0-3 4.4A3.6 3.6 0 0 0 4 10.2a3.5 3.5 0 0 0 .8 5 3.5 3.5 0 0 0 2.2 4.6 3.2 3.2 0 0 0 5.5 1.4 3.2 3.2 0 0 0 5.5-1.4 3.5 3.5 0 0 0 2.2-4.6 3.5 3.5 0 0 0 .8-5 3.6 3.6 0 0 0-2.5-3.3 3.2 3.2 0 0 0-3-4.4c-.9 0-1.8.4-2.4 1.1a3.2 3.2 0 0 0-2.4-1.1z" />
        <path d="M9 9.5l2 2 4-3.5" />
      </svg>
    </span>
  )
}

export default BrainStatusIcon
