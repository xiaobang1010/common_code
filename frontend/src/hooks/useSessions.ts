import { useState, useCallback, useEffect, useRef } from 'react'
import {
  sessionsApi,
  workspacesApi,
  type SessionInfo,
  type SessionGroup,
  type WorkspaceInfo,
} from '../api/client'

/**
 * 会话和工作区管理 hook
 * 负责工作区列表、会话列表的增删查改，以及切换工作区/会话时的状态同步
 */
export function useSessions() {
  const [currentWorkspace, setCurrentWorkspace] = useState<WorkspaceInfo | null>(null)
  const [currentSessionId, setCurrentSessionId] = useState<string | null>(null)
  const [sessions, setSessions] = useState<SessionInfo[]>([])
  const [workspaces, setWorkspaces] = useState<WorkspaceInfo[]>([])
  const [allGroups, setAllGroups] = useState<SessionGroup[]>([])
  // 当前运行任务列表（grouped API 透出，实时计算不落库；多任务并发时全量透出）
  const [currentTasks, setCurrentTasks] = useState<Array<{ session_id: string; state: string }>>([])

  // 用 ref 保存当前工作区路径，避免 useCallback 依赖重建
  const currentWorkspacePathRef = useRef<string | null>(null)
  // 用 ref 保存当前会话 id，让 deleteSession 等回调不依赖 currentSessionId
  const currentSessionIdRef = useRef<string | null>(null)

  // 同时更新 state 和 ref
  const updateCurrentSessionId = useCallback((id: string | null) => {
    currentSessionIdRef.current = id
    setCurrentSessionId(id)
  }, [])

  // 刷新工作区列表
  const loadWorkspaces = useCallback(async () => {
    try {
      const data = await workspacesApi.list()
      setWorkspaces(data.workspaces)
      return data.workspaces
    } catch {
      return []
    }
  }, [])

  // 刷新当前工作区的会话列表，按 updated_at 降序
  const loadSessions = useCallback(async () => {
    const path = currentWorkspacePathRef.current
    if (!path) return
    try {
      const data = await sessionsApi.list(path)
      // 按 updated_at 降序排列
      const sorted = [...data.sessions].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      )
      setSessions(sorted)
      return sorted
    } catch {
      return []
    }
  }, [])

  // 加载所有工作区的会话（按工作区分组）
  const loadAllSessions = useCallback(async () => {
    try {
      const data = await sessionsApi.grouped()
      setAllGroups(data.groups)
      setCurrentTasks(data.current_tasks ?? [])
      return data.groups
    } catch {
      return []
    }
  }, [])

  // 创建新会话，设为当前会话，刷新列表，返回新会话 id
  const createSession = useCallback(async () => {
    const path = currentWorkspacePathRef.current
    if (!path) return null
    try {
      const result = await sessionsApi.create(path)
      updateCurrentSessionId(result.session_id)
      await loadSessions()
      await loadAllSessions()
      return result.session_id
    } catch {
      return null
    }
  }, [loadSessions, loadAllSessions, updateCurrentSessionId])

  // 切换会话，返回消息列表供 useChat 使用。
  // 失败时错误冒泡给调用方：切换失败必须让调用方知道，否则调用方
  // 误以为成功、更新本地状态，会导致前后端脱钩（界面显示已切换、
  // 后端引擎仍是旧会话），下次发消息把旧会话历史写进目标会话
  const switchSession = useCallback(async (sessionId: string) => {
    const result = await sessionsApi.switch(sessionId)
    updateCurrentSessionId(sessionId)
    return result.messages
  }, [updateCurrentSessionId])

  // 删除会话，刷新列表，如果删的是当前会话则切换到下一个
  // 返回新的当前会话 id（如果删的不是当前会话则返回当前会话 id）
  const deleteSession = useCallback(async (sessionId: string): Promise<string | null> => {
    try {
      await sessionsApi.delete(sessionId)
    } catch {
      return currentSessionIdRef.current
    }
    // 刷新所有工作区的会话分组
    loadAllSessions()
    // 刷新列表
    const path = currentWorkspacePathRef.current
    if (!path) return currentSessionIdRef.current
    try {
      const data = await sessionsApi.list(path)
      const sorted = [...data.sessions].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      )
      setSessions(sorted)
      // 如果删的是当前会话，切换到列表中的第一个（最新的）
      if (sessionId === currentSessionIdRef.current) {
        const newId = sorted.length > 0 ? sorted[0].id : null
        updateCurrentSessionId(newId)
        return newId
      }
      return currentSessionIdRef.current
    } catch {
      // 列表刷新失败，清空
      setSessions([])
      if (sessionId === currentSessionIdRef.current) {
        updateCurrentSessionId(null)
      }
      return currentSessionIdRef.current
    }
  }, [updateCurrentSessionId, loadAllSessions])

  // 重命名会话（同步更新 allGroups，侧栏渲染用的是分组数据）
  const renameSession = useCallback(async (sessionId: string, title: string) => {
    try {
      await sessionsApi.rename(sessionId, title)
      // 更新本地列表中的标题
      setSessions(prev =>
        prev.map(s => (s.id === sessionId ? { ...s, title } : s))
      )
      // 同步 allGroups（侧栏渲染源）
      setAllGroups(prev =>
        prev.map(g => ({
          ...g,
          sessions: g.sessions.map(s => (s.id === sessionId ? { ...s, title } : s)),
        }))
      )
    } catch {
      // 重命名失败静默忽略
    }
  }, [])

  // 切换任务置顶（同步 sessions 与 allGroups）
  const toggleSessionPin = useCallback(async (sessionId: string, pinned: boolean) => {
    try {
      await sessionsApi.pin(sessionId, pinned)
      setSessions(prev =>
        prev.map(s => (s.id === sessionId ? { ...s, pinned } : s))
      )
      setAllGroups(prev =>
        prev.map(g => ({
          ...g,
          sessions: g.sessions.map(s => (s.id === sessionId ? { ...s, pinned } : s)),
        }))
      )
    } catch {
      // 失败静默忽略
    }
  }, [])

  // 更新工作区元信息（别名/置顶），刷新列表
  const updateWorkspaceMeta = useCallback(async (path: string, data: { alias?: string; pinned?: boolean }) => {
    try {
      await workspacesApi.update(path, data)
      await loadWorkspaces()
      await loadAllSessions()
    } catch {
      // 失败静默忽略
    }
  }, [loadWorkspaces, loadAllSessions])

  // 切换工作区：先确保工作区在表里，再切换，加载会话列表
  // 返回当前分支和新会话 id，供调用方使用
  const switchWorkspace = useCallback(async (path: string) => {
    try {
      // 先确保工作区已添加到 workspaces 表
      await workspacesApi.add(path)
      const result = await workspacesApi.switch(path)
      setCurrentWorkspace(result.workspace)
      currentWorkspacePathRef.current = result.workspace.path
      // 刷新工作区列表
      await loadWorkspaces()
      // 加载该工作区的会话列表
      const sessionsData = await sessionsApi.list(result.workspace.path)
      const sorted = [...sessionsData.sessions].sort(
        (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
      )
      setSessions(sorted)
      // 刷新所有工作区的会话分组
      await loadAllSessions()
      // 切换到最近的会话（如果有）
      const newSessionId = sorted.length > 0 ? sorted[0].id : null
      updateCurrentSessionId(newSessionId)
      // 返回当前分支和新会话 id，供调用方使用
      return { branch: result.current_branch, sessionId: newSessionId }
    } catch {
      return null
    }
  }, [updateCurrentSessionId, loadAllSessions, loadWorkspaces])

  // 删除工作区，刷新列表，如果删的是当前工作区则清空当前工作区状态
  const deleteWorkspace = useCallback(async (path: string): Promise<WorkspaceInfo[] | null> => {
    try {
      const result = await workspacesApi.remove(path)
      setWorkspaces(result.workspaces)
      // 如果删的是当前工作区，清空当前工作区状态
      if (path === currentWorkspacePathRef.current) {
        setCurrentWorkspace(null)
        currentWorkspacePathRef.current = null
        setSessions([])
        updateCurrentSessionId(null)
      }
      await loadAllSessions()
      return result.workspaces
    } catch {
      return null
    }
  }, [loadAllSessions, updateCurrentSessionId])

  // 切换到指定工作区中的会话（如果需要，先切换工作区）
  const switchToSessionInWorkspace = useCallback(async (sessionId: string, workspacePath: string) => {
    let branch: string | undefined
    // 如果不是当前工作区，先切换工作区
    if (currentWorkspacePathRef.current !== workspacePath) {
      const wsResult = await workspacesApi.switch(workspacePath)
      setCurrentWorkspace(wsResult.workspace)
      currentWorkspacePathRef.current = wsResult.workspace.path
      branch = wsResult.current_branch
    }
    // 切换会话
    const result = await sessionsApi.switch(sessionId)
    updateCurrentSessionId(sessionId)
    await loadAllSessions()
    return { messages: result.messages, branch }
  }, [loadAllSessions, updateCurrentSessionId])

  // 初始化：加载工作区列表，没有工作区就不自动添加，让用户手动打开
  useEffect(() => {
    const init = async () => {
      const ws = await loadWorkspaces()
      if (ws.length > 0) {
        // 有工作区，选第一个作为当前工作区
        setCurrentWorkspace(ws[0])
        currentWorkspacePathRef.current = ws[0].path

        // 加载当前工作区的会话列表
        const path = currentWorkspacePathRef.current
        if (!path) return
        try {
          const data = await sessionsApi.list(path)
          const sorted = [...data.sessions].sort(
            (a, b) => new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime()
          )
          setSessions(sorted)
          // 如果有会话，选第一个作为当前会话
          if (sorted.length > 0) {
            updateCurrentSessionId(sorted[0].id)
          }
        } catch {
          // 会话列表加载失败，静默忽略
        }
      }

      // 加载所有工作区的会话分组
      await loadAllSessions()
    }
    init()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return {
    currentWorkspace,
    currentSessionId,
    updateCurrentSessionId,
    sessions,
    workspaces,
    allGroups,
    currentTasks,
    createSession,
    switchSession,
    deleteSession,
    renameSession,
    toggleSessionPin,
    updateWorkspaceMeta,
    switchWorkspace,
    deleteWorkspace,
    switchToSessionInWorkspace,
    loadSessions,
    loadWorkspaces,
    loadAllSessions,
  }
}
