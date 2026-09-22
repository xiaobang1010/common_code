import type { ReactNode } from 'react'

// 工具行展示口径的单一来源：消息流时间线（WorkBlock）与产物面板执行轨迹（AgentTraceCard）
// 共用同一套短分类标签、对象名提取与图标，保证两处视觉与语义一致。

// 工具行分类标签（toolName 小写归一后匹配，未知工具兜底「已执行」+ 原名）
export const VERB_BY_TOOL: Record<string, string> = {
  bash: '终端',
  read: '读取',
  write: '写入',
  edit: '修改',
  grep: '搜索',
  glob: '查找',
  askuserquestion: '提问',
  skill: '技能',
  agent: '子任务',
  sendmessage: '发送',
  teamcreate: '建团队',
  taskcreate: '建任务',
  taskupdate: '改任务',
  tasklist: '查任务',
  taskget: '查任务',
  summarizeteam: '汇总',
  error: '出错',
}

// 从 args JSON 提取首个可读参数（路径/命令/问题等）作为事件行对象名
export function extractObject(args: string): string | null {
  if (!args) return null
  try {
    const parsed = JSON.parse(args)
    if (parsed && typeof parsed === 'object') {
      for (const value of Object.values(parsed)) {
        if (typeof value === 'string' && value.trim()) return value.trim()
        if (typeof value === 'number') return String(value)
      }
    }
    return null
  } catch {
    return null
  }
}

// 事件行图标按语义分组复用，单色（外层 currentColor 决定：错误红、其余中性灰）
export const ICON_PATHS: Record<string, ReactNode> = {
  file: (<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z" /><path d="M14 2v6h6" /></>),
  search: (<><circle cx="11" cy="11" r="8" /><path d="M21 21l-4.35-4.35" /></>),
  pencil: (<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />),
  filePlus: (<><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8l-6-6z" /><path d="M14 2v6h6M12 18v-6M9 15h6" /></>),
  terminal: (<><path d="M4 17l6-6-6-6" /><path d="M12 19h8" /></>),
  zap: (<path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z" />),
  help: (<><circle cx="12" cy="12" r="10" /><path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" /><path d="M12 17h.01" /></>),
  bot: (<><rect x="4" y="8" width="16" height="12" rx="2" /><path d="M12 8V4" /><path d="M8 13h.01M16 13h.01" /></>),
  send: (<><path d="M22 2L11 13" /><path d="M22 2l-7 20-4-9-9-4z" /></>),
  users: (<><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2" /><circle cx="9" cy="7" r="4" /><path d="M23 21v-2a4 4 0 0 0-3-3.87" /><path d="M16 3.13a4 4 0 0 1 0 7.75" /></>),
  list: (<><path d="M8 6h13M8 12h13M8 18h13" /><path d="M3 6h.01M3 12h.01M3 18h.01" /></>),
  alert: (<><circle cx="12" cy="12" r="10" /><path d="M12 8v4M12 16h.01" /></>),
  brain: (
    <>
      <path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z" />
      <path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z" />
      <path d="M15 13a4.5 4.5 0 0 1-3-4 4.5 4.5 0 0 1-3 4" />
    </>
  ),
  dot: (<circle cx="12" cy="12" r="3" fill="currentColor" stroke="none" />),
}

export function iconKind(toolName: string): string {
  const n = toolName.toLowerCase()
  if (n === 'bash') return 'terminal'
  if (n === 'read') return 'file'
  if (n === 'grep' || n === 'glob') return 'search'
  if (n === 'write') return 'filePlus'
  if (n === 'edit') return 'pencil'
  if (n === 'skill') return 'zap'
  if (n === 'askuserquestion') return 'help'
  if (n === 'agent') return 'bot'
  if (n === 'sendmessage') return 'send'
  if (n === 'teamcreate') return 'users'
  if (n === 'taskcreate' || n === 'taskupdate' || n === 'tasklist' || n === 'taskget' || n === 'summarizeteam') return 'list'
  if (n === 'error') return 'alert'
  return 'dot'
}

export function StepIcon({ kind }: { kind: string }) {
  return (
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ flexShrink: 0 }}>
      {ICON_PATHS[kind] ?? ICON_PATHS.dot}
    </svg>
  )
}
