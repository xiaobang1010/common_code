const { contextBridge } = require('electron')

// 预留 IPC 桥，暂时暴露空对象占位，后续按需补充方法
contextBridge.exposeInMainWorld('electronAPI', {})
