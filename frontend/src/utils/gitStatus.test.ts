import { describe, expect, it } from 'vitest'
import {
  aggregateDirectoryStatus,
  buildStatusByPath,
  descendantStatuses,
  effectiveStatus,
  type GitFileStatus,
} from './gitStatus'

const change = (path: string, status: string) => ({ path, status })

describe('buildStatusByPath', () => {
  it('同路径多状态按 untracked>added>deleted>renamed>modified 取最高档', () => {
    const { statusByPath } = buildStatusByPath(
      [change('a.txt', 'modified'), change('a.txt', 'added')],
      '',
    )
    expect(statusByPath.get('a.txt')).toBe('added')
    const merged = buildStatusByPath(
      [change('b.txt', 'deleted'), change('b.txt', 'untracked')],
      '',
    )
    expect(merged.statusByPath.get('b.txt')).toBe('untracked')
  })

  it('词表外状态（unknown）归并 modified 且计入键集', () => {
    const { statusByPath } = buildStatusByPath([change('u.txt', 'unknown')], '')
    expect(statusByPath.get('u.txt')).toBe('modified')
    // modified 档位最低：与任何已知状态并存时让位
    const both = buildStatusByPath(
      [change('u.txt', 'unknown'), change('u.txt', 'deleted')],
      '',
    )
    expect(both.statusByPath.get('u.txt')).toBe('deleted')
  })

  it('目录尾斜杠条目剥斜杠后归 untrackedDirs，不进 statusByPath', () => {
    const { statusByPath, untrackedDirs } = buildStatusByPath([change('tmp/', 'untracked')], '')
    expect(untrackedDirs.has('tmp')).toBe(true)
    expect(statusByPath.has('tmp')).toBe(false)
  })

  it('repoPrefix 为空串时全部条目原样保留', () => {
    const { statusByPath } = buildStatusByPath([change('a.txt', 'modified'), change('sub/b.txt', 'added')], '')
    expect(statusByPath.get('a.txt')).toBe('modified')
    expect(statusByPath.get('sub/b.txt')).toBe('added')
  })

  it('repoPrefix 非空时剥前缀，前缀外与剥后空串条目丢弃', () => {
    const { statusByPath, untrackedDirs } = buildStatusByPath(
      [
        change('sub/a.txt', 'modified'), // 工作区内：剥前缀保留
        change('sibling.txt', 'modified'), // 工作区外（兄弟目录）：丢弃
        change('sub', 'untracked'), // 整个工作区目录自身：丢弃
        change('sub/', 'untracked'), // 同上（目录条目形态）：丢弃
      ],
      'sub',
    )
    expect(statusByPath.get('a.txt')).toBe('modified')
    expect(statusByPath.has('sub/a.txt')).toBe(false)
    expect(statusByPath.has('sibling.txt')).toBe(false)
    expect(untrackedDirs.size).toBe(0)
  })
})

describe('effectiveStatus', () => {
  it('未跟踪目录自身与其下文件按前缀命中 untracked', () => {
    const index = buildStatusByPath([change('tmp/', 'untracked')], '')
    expect(effectiveStatus('tmp', index)).toBe('untracked')
    expect(effectiveStatus('tmp/x.py', index)).toBe('untracked')
    expect(effectiveStatus('tmp/deep/y.py', index)).toBe('untracked')
    // 同名前缀但非子路径（tmp2/）不误命中
    expect(effectiveStatus('tmp2/x.py', index)).toBeNull()
  })

  it('直接命中优先于前缀继承', () => {
    const index = buildStatusByPath(
      [change('tmp/', 'untracked'), change('tmp/a.txt', 'modified')],
      '',
    )
    expect(effectiveStatus('tmp/a.txt', index)).toBe('modified')
  })
})

describe('descendantStatuses + aggregateDirectoryStatus', () => {
  it('目录聚合：modified 优先，无 modified 按 untracked>added>deleted>renamed 取最高', () => {
    const index = buildStatusByPath(
      [
        change('src/a.ts', 'untracked'),
        change('src/b.ts', 'modified'),
        change('src/c.ts', 'added'),
      ],
      '',
    )
    const agg = aggregateDirectoryStatus(descendantStatuses('src', index))
    expect(agg.primary).toBe('modified')
    expect(agg.ordered).toEqual<GitFileStatus[]>(['modified', 'untracked', 'added'])
  })

  it('未跟踪目录折叠条目计入祖先目录聚合', () => {
    const index = buildStatusByPath(
      [change('tmp/', 'untracked'), change('src/deep/a.ts', 'modified')],
      '',
    )
    expect(aggregateDirectoryStatus(descendantStatuses('tmp', index)).primary).toBe('untracked')
    expect(aggregateDirectoryStatus(descendantStatuses('src', index)).primary).toBe('modified')
    expect(aggregateDirectoryStatus(descendantStatuses('src/deep', index)).primary).toBe('modified')
    // 未跟踪目录下的子目录同样继承未跟踪
    const nested = buildStatusByPath([change('tmp/', 'untracked')], '')
    expect(aggregateDirectoryStatus(descendantStatuses('tmp/sub', nested)).primary).toBe('untracked')
  })

  it('去重且无子孙状态时 primary 为 null', () => {
    const agg = aggregateDirectoryStatus(['modified', 'modified', 'untracked'])
    expect(agg.ordered).toEqual<GitFileStatus[]>(['modified', 'untracked'])
    expect(aggregateDirectoryStatus([]).primary).toBeNull()
  })
})
