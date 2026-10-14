import { describe, expect, it } from 'vitest'
import { TASK_PREVIEW_LIMIT, previewSessions } from './SessionList'
import type { SessionInfo } from '../../api/client'

function makeSession(id: string, updatedOffsetMs: number): SessionInfo {
  const updated_at = new Date(Date.now() - updatedOffsetMs).toISOString()
  return {
    id,
    title: `任务-${id}`,
    workspace_path: 'D:/ws/demo',
    branch: '',
    created_at: updated_at,
    updated_at,
    message_count: 0,
    pinned: false,
    group_id: '',
  }
}

describe('previewSessions：任务预览折叠', () => {
  it('预览上限为 5', () => {
    expect(TASK_PREVIEW_LIMIT).toBe(5)
  })

  it('空数组不报错，返回空', () => {
    expect(previewSessions([], false)).toEqual([])
    expect(previewSessions([], true)).toEqual([])
  })

  it('恰好 5 个：折叠与展开都是全量', () => {
    const sessions = Array.from({ length: 5 }, (_, i) => makeSession(String(i), i))
    expect(previewSessions(sessions, false)).toHaveLength(5)
    expect(previewSessions(sessions, true)).toHaveLength(5)
  })

  it('超过 5 个：折叠时只取前 5，展开时全量', () => {
    const sessions = Array.from({ length: 8 }, (_, i) => makeSession(String(i), i))
    const folded = previewSessions(sessions, false)
    expect(folded).toHaveLength(5)
    expect(folded).toEqual(sessions.slice(0, 5))
    expect(previewSessions(sessions, true)).toEqual(sessions)
  })

  it('不修改入参数组', () => {
    const sessions = Array.from({ length: 7 }, (_, i) => makeSession(String(i), i))
    previewSessions(sessions, false)
    expect(sessions).toHaveLength(7)
  })
})
