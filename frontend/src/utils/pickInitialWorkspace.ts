import type { WorkspaceInfo } from '../api/client'

// 初始化选中「最近使用」的工作区（last_used_at 最大），与后端启动恢复的
// get_last_used_workspace_path 同口径。显式计算而非取列表首位：列表排序是
// 「置顶优先 + 添加时间」，选中语义与其解耦后互不影响。
export function pickInitialWorkspace(workspaces: WorkspaceInfo[]): WorkspaceInfo | null {
  if (workspaces.length === 0) return null
  return workspaces.reduce((latest, ws) =>
    new Date(ws.last_used_at).getTime() > new Date(latest.last_used_at).getTime() ? ws : latest
  )
}
