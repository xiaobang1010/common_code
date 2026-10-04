// 文件树 git 状态聚合：把 /api/git/status 的变更清单归一为「路径→展示状态」索引，
// 供着色、徽标、目录聚合与「仅显示变更」过滤共用同一份口径。
// 入参用结构化最小类型而非引 useGitStatus 的 GitChange，避免类型级循环依赖。

// 文件树展示状态词表（冲突等词表外状态在归一时并入 modified）
export type GitFileStatus = 'untracked' | 'added' | 'modified' | 'deleted' | 'renamed'

// 同一路径多条状态的展示档位：未跟踪最高（新出现最显眼），修改最低
const STATUS_RANK: Record<GitFileStatus, number> = {
  untracked: 5,
  added: 4,
  deleted: 3,
  renamed: 2,
  modified: 1,
}

// 后端词表 → 展示词表；unknown（冲突码等）归并 modified，保证变更文件不丢失
const KNOWN_STATUSES: Record<string, GitFileStatus> = {
  untracked: 'untracked',
  added: 'added',
  modified: 'modified',
  deleted: 'deleted',
  renamed: 'renamed',
}

export interface GitStatusIndex {
  // 工作区相对路径 → 展示状态（未跟踪折叠目录条目不在其中，见 untrackedDirs）
  statusByPath: Map<string, GitFileStatus>
  // 未跟踪目录折叠条目（porcelain `dir/`，已剥尾斜杠）：目录自身与其中文件按前缀命中未跟踪
  untrackedDirs: Set<string>
}

// 后端 changes[].path 是仓库根口径：剥 repoPrefix 归一为工作区相对口径。
// 前缀外条目与剥后空串条目（整个工作区目录自身未跟踪/被删）不属于本工作区，丢弃
function normalizePath(rawPath: string, repoPrefix: string): string | null {
  if (!repoPrefix) return rawPath
  if (rawPath === repoPrefix || rawPath.startsWith(repoPrefix + '/')) {
    const out = rawPath.slice(repoPrefix.length + 1)
    return out || null
  }
  return null
}

export function buildStatusByPath(
  changes: Array<{ path: string; status: string }>,
  repoPrefix: string,
): GitStatusIndex {
  const statusByPath = new Map<string, GitFileStatus>()
  const untrackedDirs = new Set<string>()
  for (const change of changes) {
    const isDirEntry = change.path.endsWith('/')
    const normalized = normalizePath(isDirEntry ? change.path.slice(0, -1) : change.path, repoPrefix)
    if (!normalized) continue
    const status: GitFileStatus = KNOWN_STATUSES[change.status] ?? 'modified'
    // 折叠目录条目只进目录集合：与同名普通文件区分开，目录着色/前缀命中由消费方处理
    if (isDirEntry) {
      untrackedDirs.add(normalized)
      continue
    }
    const existing = statusByPath.get(normalized)
    if (!existing || STATUS_RANK[status] > STATUS_RANK[existing]) {
      statusByPath.set(normalized, status)
    }
  }
  return { statusByPath, untrackedDirs }
}

// 单条路径的展示状态：直接命中优先，未跟踪目录自身与其下文件按前缀继承未跟踪
export function effectiveStatus(
  path: string,
  index: GitStatusIndex,
): GitFileStatus | null {
  const direct = index.statusByPath.get(path)
  if (direct) return direct
  if (isUnderUntrackedDir(path, index)) return 'untracked'
  return null
}

// 路径是否落在某个未跟踪目录内（含目录自身）
function isUnderUntrackedDir(path: string, index: GitStatusIndex): boolean {
  for (const dir of index.untrackedDirs) {
    if (path === dir || path.startsWith(dir + '/')) return true
  }
  return false
}

// 目录前缀下全部子孙（含未跟踪目录折叠条目覆盖的部分）的展示状态集合
export function descendantStatuses(dirPath: string, index: GitStatusIndex): GitFileStatus[] {
  const found = new Set<GitFileStatus>()
  const prefix = dirPath + '/'
  for (const [path, status] of index.statusByPath) {
    if (path.startsWith(prefix)) found.add(status)
  }
  for (const dir of index.untrackedDirs) {
    // 未跟踪目录在自身之下或与该目录有祖先关系，都让本目录呈现未跟踪
    if (dir === dirPath || dir.startsWith(prefix) || dirPath.startsWith(dir + '/')) {
      found.add('untracked')
    }
  }
  return [...found]
}

// 目录聚合优先序：修改最高（目录要突出「有实质改动」），其余按未跟踪>新增>删除>重命名；
// 与文件级档位（未跟踪最高）方向相反，属刻意设计
const DIRECTORY_RANK: Record<GitFileStatus, number> = {
  modified: 0,
  untracked: 1,
  added: 2,
  deleted: 3,
  renamed: 4,
}

export interface DirectoryAggregation {
  // 着色与圆点用的聚合状态；无子孙状态时为 null
  primary: GitFileStatus | null
  // 去重后的全部子孙状态，按聚合优先序排列（悬停提示用）
  ordered: GitFileStatus[]
}

export function aggregateDirectoryStatus(statuses: GitFileStatus[]): DirectoryAggregation {
  const unique = [...new Set(statuses)].sort(
    (a, b) => DIRECTORY_RANK[a] - DIRECTORY_RANK[b],
  )
  return { primary: unique[0] ?? null, ordered: unique }
}
