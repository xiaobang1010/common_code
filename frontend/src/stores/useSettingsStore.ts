// 设置面板的全局状态 store
// 持有五区需要的数据，提供刷新 action 和"设置变更广播"
// 订阅 modelVersion，变化时触发 fetchState 刷新显示

import { create } from 'zustand'
import {
  llmApi,
  pluginsApi,
  memoryApi,
  agentsApi,
  skillsApi,
  type LLMConfig,
  type PluginInfo,
  type CustomLLMProviderInfo,
  type LLMProviderInfo,
  type MemoryProviderInfo,
  type AgentInfo,
  type SkillInfo,
} from '../api/client'

interface SettingsState {
  // LLM 配置
  llmConfig: LLMConfig | null
  customProviders: CustomLLMProviderInfo[]
  activeProvider: string | null
  activeModel: string | null
  // 旧版插件供应商（保留兼容）
  providers: LLMProviderInfo[]

  // 插件
  plugins: PluginInfo[]

  // 记忆
  memoryProviders: MemoryProviderInfo[]
  activeMemory: string | null
  // 记忆功能开关（后端 memoryEnabled 的前端镜像，大脑图标与设置开关共享）
  memoryEnabled: boolean

  // 子智能体
  agents: AgentInfo[]

  // 技能
  skills: SkillInfo[]

  // 加载态
  loading: boolean
  error: string

  // 设置变更版本号：每次 LLM 配置或供应商变更后 +1
  // 订阅它，变化时触发 fetchState 刷新 model 显示
  modelVersion: number

  // 刷新各分区
  refreshLlmConfig: () => Promise<void>
  refreshCustomProviders: () => Promise<void>
  refreshProviders: () => Promise<void>
  refreshPlugins: () => Promise<void>
  refreshMemoryProviders: () => Promise<void>
  refreshAgents: () => Promise<void>
  refreshSkills: () => Promise<void>
  refreshAll: () => Promise<void>

  // 设置变更后调用，触发 modelVersion +1
  notifyModelChanged: () => void

  // 记忆功能开关状态同步
  setMemoryEnabled: (enabled: boolean) => void

  // 错误处理
  setError: (err: string) => void
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  llmConfig: null,
  customProviders: [],
  activeProvider: null,
  activeModel: null,
  providers: [],
  plugins: [],
  memoryProviders: [],
  activeMemory: null,
  memoryEnabled: false,
  agents: [],
  skills: [],

  loading: false,
  error: '',
  modelVersion: 0,

  refreshLlmConfig: async () => {
    try {
      const config = await llmApi.getConfig()
      set({
        llmConfig: config,
        customProviders: config.llm_providers || [],
        activeProvider: config.active_provider || null,
        activeModel: config.active_model || null,
      })
    } catch (e) {
      set({ error: `加载 LLM 配置失败：${e instanceof Error ? e.message : String(e)}` })
    }
  },

  refreshCustomProviders: async () => {
    try {
      const data = await llmApi.listCustomProviders()
      set({
        customProviders: data.providers,
        activeProvider: data.active_provider,
        activeModel: data.active_model,
      })
    } catch (e) {
      set({ error: `加载供应商失败：${e instanceof Error ? e.message : String(e)}` })
    }
  },

  refreshProviders: async () => {
    try {
      const data = await llmApi.listProviders()
      set({ providers: data.providers, activeProvider: data.active })
    } catch (e) {
      set({ error: `加载供应商失败：${e instanceof Error ? e.message : String(e)}` })
    }
  },

  refreshPlugins: async () => {
    try {
      const data = await pluginsApi.list()
      set({ plugins: data.plugins })
    } catch (e) {
      set({ error: `加载插件失败：${e instanceof Error ? e.message : String(e)}` })
    }
  },

  refreshMemoryProviders: async () => {
    try {
      const data = await memoryApi.listProviders()
      set({ memoryProviders: data.providers, activeMemory: data.active })
    } catch (e) {
      set({ error: `加载记忆后端失败：${e instanceof Error ? e.message : String(e)}` })
    }
  },

  refreshAgents: async () => {
    try {
      const data = await agentsApi.list()
      set({ agents: data.agents })
    } catch (e) {
      set({ error: `加载子智能体失败：${e instanceof Error ? e.message : String(e)}` })
    }
  },

  refreshSkills: async () => {
    try {
      const data = await skillsApi.list()
      set({ skills: data.skills })
    } catch (e) {
      set({ error: `加载技能失败：${e instanceof Error ? e.message : String(e)}` })
    }
  },

  refreshAll: async () => {
    set({ loading: true, error: '' })
    await Promise.all([
      get().refreshLlmConfig(),
      get().refreshPlugins(),
      get().refreshMemoryProviders(),
      get().refreshAgents(),
      get().refreshSkills(),
    ])
    set({ loading: false })
  },

  notifyModelChanged: () => {
    set((state) => ({ modelVersion: state.modelVersion + 1 }))
  },

  setMemoryEnabled: (enabled: boolean) => set({ memoryEnabled: enabled }),

  setError: (err: string) => set({ error: err }),
}))
