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
  selectDirectory: () => ipcRenderer.invoke('dialog:selectDirectory')
})
