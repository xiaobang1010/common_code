// 面板桥接：store 层（SSE 事件处理）与 App 层（ArtifactPanel 句柄）解耦。
// store 不能持有 React ref，App 挂载时注册打开函数，卸载时注销。

export type PanelOpener = (files: string[]) => void

let opener: PanelOpener | null = null

export function registerPanelOpener(fn: PanelOpener | null): void {
  opener = fn
}

export function openFilesInPanel(files: string[]): void {
  opener?.(files)
}
