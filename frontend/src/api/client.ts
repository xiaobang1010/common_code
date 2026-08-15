// 统一 API 客户端 — 所有后端调用都走这里
// 封装 res.ok 检查、错误信息提取、TypeScript 泛型类型

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

/** LLM 基础配置（GET/POST /api/config 用的字段） */
export interface LLMConfig {
  llm_base_url: string
  llm_api_key: string
  llm_model: string
  llm_providers?: CustomLLMProviderInfo[]
  active_provider?: string | null
  active_model?: string | null
}

/** API 格式 */
export type ApiFormat = 'openai' | 'anthropic'

/** 自定义 LLM 模型 */
export interface CustomLLMModelInfo {
  model_id: string
  context_window: number
}

/** 自定义 LLM 供应商 */
export interface CustomLLMProviderInfo {
  id: string
  name: string
  base_url: string
  api_key: string
  api_format: ApiFormat
  models: CustomLLMModelInfo[]
}

/** LLM 供应商信息（旧的插件供应商格式，保留兼容） */
export interface LLMProviderInfo {
  name: string
  base_url: string
  model: string
  models?: string[]
}

/** 插件信息（GET /api/plugins 返回） */
export interface PluginInfo {
  name: string
  version: string
  kind: string
  enabled: boolean
  description: string
  source?: string
  skills_count?: number
  hooks_count?: number
  commands_count?: number
  mcp_servers_count?: number
}

/** 记忆后端信息 */
export interface MemoryProviderInfo {
  name: string
}

/** 子智能体信息（GET /api/agents 返回的只读字段） */
export interface AgentInfo {
  agent_type: string
  when_to_use: string
  tools: string[] | null
  disallowed_tools: string[]
  model: string | null
  max_turns: number | null
  background: boolean
  source: string
}

/** 技能信息（GET /api/skills 返回的完整字段） */
export interface SkillInfo {
  name: string
  description: string
  when_to_use: string
  source: string                  // file / plugin / bundled
  source_label: string            // workspace / personal / plugin / bundled
  allowed_tools: string[] | null
  disable_model_invocation: boolean
  user_invocable: boolean
  skill_root: string | null
}

// ---------------------------------------------------------------------------
// 通用请求方法
// ---------------------------------------------------------------------------

/** GET 请求，返回 JSON 并带类型 */
export async function apiGet<T>(path: string): Promise<T> {
  const res = await fetch(path)
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`GET ${path} 失败：${res.status} ${text || res.statusText}`)
  }
  return res.json() as Promise<T>
}

/** POST 请求，发送 JSON body，返回 JSON 并带类型 */
export async function apiPost<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  const json = await res.json().catch(() => ({}))
  if (!res.ok || json.ok === false) {
    throw new Error(json.error || `POST ${path} 失败：${res.status}`)
  }
  return json as T
}

/** PUT 请求，发送 JSON body，返回 JSON 并带类型 */
export async function apiPut<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  const json = await res.json().catch(() => ({}))
  if (!res.ok || json.ok === false) {
    throw new Error(json.error || `PUT ${path} 失败：${res.status}`)
  }
  return json as T
}

/** DELETE 请求，返回 JSON 并带类型 */
export async function apiDelete<T>(path: string): Promise<T> {
  const res = await fetch(path, { method: 'DELETE' })
  const json = await res.json().catch(() => ({}))
  if (!res.ok || json.ok === false) {
    throw new Error(json.error || `DELETE ${path} 失败：${res.status}`)
  }
  return json as T
}

/** PATCH 请求，发送 JSON body，返回 JSON 并带类型 */
export async function apiPatch<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  const json = await res.json().catch(() => ({}))
  if (!res.ok || json.ok === false) {
    throw new Error(json.error || `PATCH ${path} 失败：${res.status}`)
  }
  return json as T
}

// ---------------------------------------------------------------------------
// 具体 endpoint 封装
// ---------------------------------------------------------------------------

/** LLM 配置和供应商管理 */
export const llmApi = {
  // 基础配置
  getConfig: () => apiGet<LLMConfig>('/api/config'),
  saveConfig: (config: Partial<LLMConfig>) =>
    apiPost<{ ok: boolean }>('/api/config', config),

  // 自定义供应商 CRUD
  listCustomProviders: () =>
    apiGet<{ providers: CustomLLMProviderInfo[]; active_provider: string | null; active_model: string | null }>(
      '/api/llm-providers'
    ),
  createProvider: (data: Omit<CustomLLMProviderInfo, 'id'>) =>
    apiPost<{ ok: boolean; provider: CustomLLMProviderInfo }>('/api/llm-providers', data),
  updateProvider: (id: string, data: Omit<CustomLLMProviderInfo, 'id'>) =>
    apiPut<{ ok: boolean; provider: CustomLLMProviderInfo }>(`/api/llm-providers/${id}`, data),
  deleteProvider: (id: string) =>
    apiDelete<{ ok: boolean }>(`/api/llm-providers/${id}`),
  testProvider: (id: string) =>
    apiPost<{ ok: boolean; message?: string; error?: string }>(`/api/llm-providers/${id}/test`),
  activateProvider: (provider_id: string, model_id: string) =>
    apiPost<{ ok: boolean; provider_id: string; model_id: string }>(
      '/api/llm-providers/activate',
      { provider_id, model_id }
    ),

  // 旧版插件供应商（保留兼容）
  listProviders: () =>
    apiGet<{ providers: LLMProviderInfo[]; active: string | null }>(
      '/api/plugins/llm-providers'
    ),
  switchProvider: (provider: string) =>
    apiPost<{ ok: boolean; active_provider: string }>(
      '/api/plugins/llm-provider/switch',
      { provider }
    ),
}

/** 插件管理 */
export const pluginsApi = {
  list: () => apiGet<{ plugins: PluginInfo[] }>('/api/plugins'),
  enable: (name: string) =>
    apiPost<{ ok: boolean }>('/api/plugins/enable', { name }),
  disable: (name: string) =>
    apiPost<{ ok: boolean }>('/api/plugins/disable', { name }),
}

/** 记忆管理 */
export const memoryApi = {
  listProviders: () =>
    apiGet<{ providers: MemoryProviderInfo[]; active: string | null }>(
      '/api/memory/providers'
    ),
  switch: (name: string) =>
    apiPost<{ ok: boolean }>('/api/memory/switch', { name }),
  clear: (session_id: string) =>
    apiPost<{ ok: boolean }>('/api/memory/clear', { session_id }),
  search: (query: string, wing?: string, room?: string, limit?: number) =>
    apiPost<{ ok: boolean; results: any[] }>('/api/memory/search', {
      query,
      wing,
      room,
      limit,
    }),
  add: (
    wing: string,
    room: string,
    content: string,
    source_file?: string,
    importance?: number
  ) =>
    apiPost<{ ok: boolean; id: string }>('/api/memory/add', {
      wing,
      room,
      content,
      source_file,
      importance,
    }),
  status: () =>
    apiGet<{ ok: boolean; status: any }>('/api/memory/status'),
  wings: () =>
    apiGet<{ ok: boolean; wings: any[] }>('/api/memory/wings'),
  rooms: (wing: string) =>
    apiPost<{ ok: boolean; rooms: any[] }>('/api/memory/rooms', { wing }),
  kgAdd: (
    subject: string,
    predicate: string,
    object: string,
    valid_from?: string,
    drawer_refs?: any[]
  ) =>
    apiPost<{ ok: boolean; id: string }>('/api/memory/kg/add', {
      subject,
      predicate,
      object,
      valid_from,
      drawer_refs,
    }),
  kgQuery: (entity: string, as_of?: string) =>
    apiPost<{ ok: boolean; triples: any[] }>('/api/memory/kg/query', {
      entity,
      as_of,
    }),
  kgTimeline: (entity: string) =>
    apiPost<{ ok: boolean; timeline: any[] }>('/api/memory/kg/timeline', {
      entity,
    }),
  kgInvalidate: (
    subject: string,
    predicate: string,
    object: string,
    ended?: string
  ) =>
    apiPost<{ ok: boolean }>('/api/memory/kg/invalidate', {
      subject,
      predicate,
      object,
      ended,
    }),
  kgEntities: () =>
    apiGet<{ ok: boolean; entities: string[] }>('/api/memory/kg/entities'),
}

/** 子智能体 */
export const agentsApi = {
  list: () => apiGet<{ agents: AgentInfo[] }>('/api/agents'),
}

/** 技能管理 */
export const skillsApi = {
  list: () => apiGet<{ skills: SkillInfo[] }>('/api/skills'),
  create: (data: {
    name: string
    description: string
    when_to_use?: string
    allowed_tools?: string[] | null
  }) => apiPost<{ ok: boolean; name: string }>('/api/skills/create', data),
  import: (name: string, content: string) =>
    apiPost<{ ok: boolean; name: string }>('/api/skills/import', { name, content }),
  refresh: () => apiPost<{ ok: boolean }>('/api/skills/refresh'),
  delete: (name: string) =>
    apiPost<{ ok: boolean }>('/api/skills/delete', { name }),
}

/** 权限模式 */
export type PermissionMode = 'default' | 'full_access'

/** 权限管理 */
export const permissionsApi = {
  setMode: (mode: PermissionMode) =>
    apiPost<{ ok: boolean; mode: PermissionMode }>('/api/permission/mode', { mode }),
}

/** AskUserQuestion 提问回答 */
export const questionApi = {
  answer: (requestId: string, answer: string) =>
    apiPost<{ ok: boolean }>('/api/question', { request_id: requestId, answer }),
}

// ---------------------------------------------------------------------------
// 会话 / 工作区 / Git 分支
// ---------------------------------------------------------------------------

/** 会话信息 */
export interface SessionInfo {
  id: string
  title: string
  workspace_path: string
  branch: string
  created_at: string
  updated_at: string
  message_count: number
  pinned: boolean
}

/** 会话详情（含消息） */
export interface SessionDetail {
  session: SessionInfo
  messages: Record<string, unknown>[]
}

/** 工作区信息 */
export interface WorkspaceInfo {
  path: string
  name: string
  last_used_at: string
  session_count: number
  pinned: boolean
  alias: string
}

/** 工作区分组（含会话列表） */
export interface SessionGroup {
  workspace: WorkspaceInfo
  sessions: SessionInfo[]
}

/** 会话管理 */
export const sessionsApi = {
  create: (workspace_path: string, title?: string) =>
    apiPost<{ session_id: string; workspace_path: string; title: string }>('/api/sessions', { workspace_path, title }),
  list: (workspace_path: string) =>
    apiGet<{ sessions: SessionInfo[] }>(`/api/sessions?workspace_path=${encodeURIComponent(workspace_path)}`),
  get: (session_id: string) =>
    apiGet<SessionDetail>(`/api/sessions/${session_id}`),
  delete: (session_id: string) =>
    apiDelete<{ ok: boolean }>(`/api/sessions/${session_id}`),
  rename: (session_id: string, title: string) =>
    apiPatch<{ ok: boolean }>(`/api/sessions/${session_id}`, { title }),
  pin: (session_id: string, pinned: boolean) =>
    apiPatch<{ ok: boolean }>(`/api/sessions/${session_id}`, { pinned }),
  switch: (session_id: string) =>
    apiPost<{ ok: boolean; messages: Record<string, unknown>[]; workspace_path: string }>(`/api/sessions/${session_id}/switch`),
  grouped: () =>
    apiGet<{ groups: SessionGroup[]; current_tasks: Array<{ session_id: string; state: string }> }>('/api/sessions/grouped'),
}

/** 工作区管理 */
export const workspacesApi = {
  list: () =>
    apiGet<{ workspaces: WorkspaceInfo[] }>('/api/workspaces'),
  add: (path: string) =>
    apiPost<{ ok: boolean; workspace: WorkspaceInfo }>('/api/workspaces', { path }),
  switch: (path: string) =>
    apiPost<{ ok: boolean; workspace: WorkspaceInfo; current_branch: string }>('/api/workspaces/switch', { path }),
  remove: (path: string) =>
    apiPost<{ ok: boolean; workspaces: WorkspaceInfo[] }>('/api/workspaces/delete', { path }),
  update: (path: string, data: { alias?: string; pinned?: boolean }) =>
    apiPost<{ ok: boolean }>('/api/workspaces/update', { path, ...data }),
}

/** Git 分支管理 */
export const gitApi = {
  branches: (path: string) =>
    apiGet<{ branches: string[]; current: string }>(`/api/git/branches?path=${encodeURIComponent(path)}`),
  checkout: (branch: string) =>
    apiPost<{ ok: boolean; branch: string }>('/api/git/checkout', { branch }),
}

// ---------------------------------------------------------------------------
// 文件读写
// ---------------------------------------------------------------------------

/** 读文件返回（含一致性基线） */
export interface FileReadResult {
  content: string
  language: string
  mtime: number
  size: number
  editable: boolean
}

/** 写文件成功返回 */
export interface FileWriteResult {
  path: string
  mtime: number
  size: number
}

/** 写文件冲突详情（409） */
export interface FileConflict {
  error: 'file_modified'
  current_mtime: number
  current_size: number
}

/** 写文件错误：带 HTTP 状态码与冲突详情 */
export interface FileWriteError extends Error {
  status: number
  conflict?: FileConflict
}

/** 文件读写接口 */
export const filesApi = {
  read: (path: string) =>
    apiGet<FileReadResult>(`/api/files/read?path=${encodeURIComponent(path)}`),
  create: (path: string, type: 'file' | 'dir') =>
    apiPost<{ path: string; type: string }>('/api/files/create', { path, type }),
  write: async (body: {
    path: string
    content: string
    base_mtime?: number
    base_size?: number
  }): Promise<FileWriteResult> => {
    const res = await fetch('/api/files/write', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    })
    const json = await res.json().catch(() => ({}))
    if (!res.ok) {
      const err = new Error(json.error || `写文件失败：${res.status}`) as FileWriteError
      err.status = res.status
      if (res.status === 409) err.conflict = json as FileConflict
      throw err
    }
    return json as FileWriteResult
  },
}
