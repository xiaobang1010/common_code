// 设置面板的全局状态 store
// 持有五区需要的数据，提供刷新 action 和"设置变更广播"
// useChat/StatusBar 订阅 modelVersion，变化时触发 fetchState 刷新显示

import { create } from 'zustand'
import {
  llmApi,
  pluginsApi,
  memoryApi,
  agentsApi,
  type LLMConfig,
  type PluginInfo,
  type LLMProviderInfo,
  type MemoryProviderInfo,
  type AgentInfo,
} from '../api/client'

interface SettingsState {
  // LLM 配置
  llmConfig: LLMConfig | null
  providers: LLMProviderInfo[]
  activeProvider: string | null

  // 插件
  plugins: PluginInfo[]

  // 记忆
  memoryProviders: MemoryProviderInfo[]
  activeMemory: string | null

  // 子智能体
  agents: AgentInfo[]

  // 加载态
  loading: boolean
  error: string

  // 设置变更版本号：每次 LLM 配置或供应商变更后 +1
  // useChat/StatusBar 订阅它，变化时触发 fetchState 刷新 model 显示
  modelVersion: number

  // 刷新各分区
  refreshLlmConfig: () => Promise<void>
  refreshProviders: () => Promise<void>
  refreshPlugins: () => Promise<void>
  refreshMemoryProviders: () => Promise<void>
  refreshAgents: () => Promise<void>
  refreshAll: () => Promise<void>

  // 设置变更后调用，触发 modelVersion +1
  notifyModelChanged: () => void

  // 错误处理
  setError: (err: string) => void
}

export const useSettingsStore = create<SettingsState>((set, get) => ({
  llmConfig: null,
  providers: [],
  activeProvider: null,
  plugins: [],
  memoryProviders: [],
  activeMemory: null,
  agents: [],

  loading: false,
  error: '',
  modelVersion: 0,

  refreshLlmConfig: async () => {
    try {
      const config = await llmApi.getConfig()
      set({
        llmConfig: config,
        providers: config.llm_providers || [],
        activeProvider: config.active_provider || null,
      })
    } catch (e) {
      set({ error: `加载 LLM 配置失败：${e instanceof Error ? e.message : String(e)}` })
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

  refreshAll: async () => {
    set({ loading: true, error: '' })
    await Promise.all([
      get().refreshLlmConfig(),
      get().refreshPlugins(),
      get().refreshMemoryProviders(),
      get().refreshAgents(),
    ])
    set({ loading: false })
  },

  notifyModelChanged: () => {
    set((state) => ({ modelVersion: state.modelVersion + 1 }))
  },

  setError: (err: string) => set({ error: err }),
}))
