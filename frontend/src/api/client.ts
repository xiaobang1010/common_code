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
