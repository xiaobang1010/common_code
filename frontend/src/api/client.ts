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
  llm_providers?: LLMProviderInfo[]
  active_provider?: string | null
}

/** LLM 供应商信息 */
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

// ---------------------------------------------------------------------------
// 具体 endpoint 封装
// ---------------------------------------------------------------------------

/** LLM 配置 */
export const llmApi = {
  getConfig: () => apiGet<LLMConfig>('/api/config'),
  saveConfig: (config: Partial<LLMConfig>) =>
    apiPost<{ ok: boolean }>('/api/config', config),
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
