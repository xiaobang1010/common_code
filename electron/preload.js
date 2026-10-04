const { contextBridge, ipcRenderer } = require('electron')

// 暴露终端相关的 IPC 接口给渲染进程
contextBridge.exposeInMainWorld('electronAPI', {
  terminal: {
    create: (cwd) => ipcRenderer.invoke('terminal:create', cwd),
    input: (id, data) => ipcRenderer.send('terminal:input', { id, data }),
    resize: (id, cols, rows) => ipcRenderer.invoke('terminal:resize', { id, cols, rows }),
    dispose: (id) => ipcRenderer.invoke('terminal:dispose', id),
    // onOutput 返回清理函数，方便组件卸载时精确移除监听
    onOutput: (callback) => {
      const handler = (_event, { id, data }) => callback(id, data)
      ipcRenderer.on('terminal:output', handler)
      return () => ipcRenderer.removeListener('terminal:output', handler)
    }
  },
  // 选择目录对话框，返回选中的目录路径或 null
  selectDirectory: () => ipcRenderer.invoke('dialog:selectDirectory'),
  // 在系统文件管理器中定位指定路径（文件树右键菜单使用）
  revealInFolder: (fullPath) => ipcRenderer.invoke('shell:revealInFolder', fullPath),
  // 内置浏览器：渲染侧只经该桥与主进程标签注册表通信，不直连 ipcRenderer
  browser: {
    // 回报类（渲染 → 主进程）
    tabCreated: (info) => ipcRenderer.send('browser:tab-created', info),
    tabState: (info) => ipcRenderer.send('browser:tab-state', info),
    tabActive: (tabId) => ipcRenderer.send('browser:tab-active', { tabId }),
    tabClosed: (tabId) => ipcRenderer.send('browser:tab-closed', { tabId }),
    teardown: () => ipcRenderer.send('browser:teardown'),
    visibility: (visible) => ipcRenderer.send('browser:visibility', { visible }),
    // 命令订阅（主进程 → 渲染）：返回清理函数，沿用 terminal.onOutput 写法
    onCommand: (callback) => {
      const entries = [
        ['browser:tab-add', (_e, d) => callback({ type: 'tab-add', ...d })],
        ['browser:tab-remove', (_e, d) => callback({ type: 'tab-remove', ...d })],
        ['browser:tab-activate', (_e, d) => callback({ type: 'tab-activate', ...d })],
        ['browser:pane-visibility', (_e, d) => callback({ type: 'pane-visibility', ...d })],
      ]
      for (const [ch, h] of entries) ipcRenderer.on(ch, h)
      return () => {
        for (const [ch, h] of entries) ipcRenderer.removeListener(ch, h)
      }
    },
  },
})
