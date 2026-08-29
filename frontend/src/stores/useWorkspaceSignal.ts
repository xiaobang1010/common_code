// 工作区身份信号 store（广播模式对齐 useSettingsStore 的 modelVersion）
// 服务端的「当前工作区」是全局指针：/api/git/status、/api/spec/progress 等
// 接口按它返回数据，请求本身不携带工作区身份。前端切换工作区后，数据钩子
// 无法从响应得知口径已变，会一直展示上一个工作区的旧数据。这里维护当前
// 工作区路径：useSessions 在其变化时写入，数据钩子把它纳入依赖，切换即重取。

import { create } from 'zustand'

interface WorkspaceSignalState {
  // 当前工作区路径（null = 尚未加载完成），仅作数据钩子的刷新信号
  currentPath: string | null
  // 工作区切换时写入新路径；同路径重复写入不触发订阅方重渲
  setCurrentPath: (path: string | null) => void
}

export const useWorkspaceSignal = create<WorkspaceSignalState>((set) => ({
  currentPath: null,
  setCurrentPath: (path) => set({ currentPath: path }),
}))
