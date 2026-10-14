import { describe, expect, it } from 'vitest'
import { pickInitialWorkspace } from './pickInitialWorkspace'
import type { WorkspaceInfo } from '../api/client'

function makeWorkspace(path: string, lastUsedOffsetMs: number): WorkspaceInfo {
  return {
    path,
    name: path,
    last_used_at: new Date(Date.now() - lastUsedOffsetMs).toISOString(),
    session_count: 0,
    pinned: false,
    alias: '',
  }
}

describe('pickInitialWorkspace：初始化选中最近使用的工作区', () => {
  it('空列表返回 null', () => {
    expect(pickInitialWorkspace([])).toBeNull()
  })

  it('单元素直接选中', () => {
    const ws = makeWorkspace('D:/ws/only', 0)
    expect(pickInitialWorkspace([ws])).toBe(ws)
  })

  it('乱序列表取 last_used_at 最大者（与后端恢复同口径）', () => {
    const oldest = makeWorkspace('D:/ws/a', 3_600_000)
    const newest = makeWorkspace('D:/ws/b', 0)
    const middle = makeWorkspace('D:/ws/c', 1_800_000)
    expect(pickInitialWorkspace([oldest, newest, middle])).toBe(newest)
  })

  it('不依赖列表顺序：最新项在末尾也能选中', () => {
    const a = makeWorkspace('D:/ws/a', 1_800_000)
    const b = makeWorkspace('D:/ws/b', 0)
    expect(pickInitialWorkspace([a, b])).toBe(b)
  })
})
