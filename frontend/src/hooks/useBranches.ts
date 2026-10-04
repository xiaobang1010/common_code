import { useCallback, useEffect, useRef, useState } from 'react'
import { gitApi } from '../api/client'
import { useWorkspaceSignal } from '../stores/useWorkspaceSignal'

// 标题栏分支状态：当前分支名 + 分支列表（最近使用排序由后端 reflog 口径决定）
export interface BranchesState {
  current: string
  branches: string[]
}

// 拉取当前分支与分支列表的 hook：工作区信号变化（切换/删除）清空重取 +
// 10 秒轮询 + 窗口聚焦刷新。终端里的 git 操作不产生文件事件（文件事件只
// 覆盖 AI 写盘工具），轮询与聚焦是外部变更的感知通道。refresh 供应用内
// 切分支与下拉打开时手动刷新
export function useBranches(): BranchesState & { refresh: () => void } {
  const [state, setState] = useState<BranchesState>({ current: '', branches: [] })
  // 请求代号：每次发起递增，响应回来对不上号说明已发出更新的请求（如快速
  // 连续切换工作区），过期响应直接丢弃，避免旧工作区数据覆盖新工作区
  const genRef = useRef(0)

  const refresh = useCallback(async () => {
    // 以信号里的最新路径为参数；无工作区（未加载/已删除）一律不发请求，
    // 空串会退化成服务端全局口径，把已删工作区的旧分支拉回来
    const path = useWorkspaceSignal.getState().currentPath
    if (!path) return
    const gen = ++genRef.current
    try {
      const data = await gitApi.branches(path)
      if (gen !== genRef.current) return
      setState({ current: data.current, branches: data.branches })
    } catch {
      // 后端未就绪时静默忽略
    }
  }, [])

  // 当前工作区路径信号：作为 effect 依赖，切换工作区时重开轮询并立即重取
  const workspacePath = useWorkspaceSignal((s) => s.currentPath)

  useEffect(() => {
    // 工作区身份变化（含删除后变 null）先清状态：重取完成前不闪现上一个
    // 工作区的分支
    setState({ current: '', branches: [] })
    if (!workspacePath) return
    void refresh()
    const timer = setInterval(() => void refresh(), 10000)
    // 终端切完分支回到应用的时刻：聚焦即刷新，不必被动等 10 秒轮询
    const onFocus = () => void refresh()
    window.addEventListener('focus', onFocus)
    return () => {
      clearInterval(timer)
      window.removeEventListener('focus', onFocus)
    }
  }, [refresh, workspacePath])

  return { ...state, refresh: () => void refresh() }
}
