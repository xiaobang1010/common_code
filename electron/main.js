const { app, BrowserWindow, dialog, ipcMain, Menu, shell } = require('electron')
const path = require('path')
const fs = require('fs')
const pty = require('node-pty')

// 主窗口引用
let win = null
// 项目根目录，作为终端默认工作目录
let projectRoot = path.join(__dirname, '..')
// 终端实例表，id -> pty 进程
const terminals = new Map()
// 终端 id 自增计数器
let terminalIdCounter = 0

// 应用菜单策略（分平台）：
// - macOS：保留符合系统规范的最小菜单（App/Edit/Window/Help），Edit 含剪贴板 role
// - Windows/Linux：保留带 edit role 的菜单但隐藏（setMenuBarVisibility(false)），
//   编辑快捷键（Ctrl+C/V/A/Z）天然可用，不丢能力
function setupMenu() {
  if (process.platform === 'darwin') {
    const template = [
      {
        label: app.name,
        submenu: [
          { role: 'about' },
          { type: 'separator' },
          { role: 'services' },
          { type: 'separator' },
          { role: 'hide' },
          { role: 'hideOthers' },
          { role: 'unhide' },
          { type: 'separator' },
          { role: 'quit' },
        ],
      },
      {
        label: 'Edit',
        submenu: [
          { role: 'undo' },
          { role: 'redo' },
          { type: 'separator' },
          { role: 'cut' },
          { role: 'copy' },
          { role: 'paste' },
          { role: 'selectAll' },
        ],
      },
      { role: 'windowMenu' },
      { role: 'help', submenu: [] },
    ]
    Menu.setApplicationMenu(Menu.buildFromTemplate(template))
  } else {
    Menu.setApplicationMenu(
      Menu.buildFromTemplate([
        {
          label: 'Edit',
          submenu: [
            { role: 'undo' },
            { role: 'redo' },
            { type: 'separator' },
            { role: 'cut' },
            { role: 'copy' },
            { role: 'paste' },
            { role: 'selectAll' },
          ],
        },
      ])
    )
  }
}

// 根据后端端口创建主窗口（端口由 launch.py 经 COMMON_CODE_BACKEND_PORT 传入）
function createWindow(port) {
  const isWindows = process.platform === 'win32'
  const isMac = process.platform === 'darwin'

  const windowOptions = {
    width: 1400,
    height: 900,
    // 深色背景，避免加载时白闪（对齐前端 --bg-base）
    backgroundColor: '#0f1115',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
    },
  }

  if (isWindows) {
    // Windows：隐藏原生标题栏，窗口按钮用系统 overlay（深色配色），
    // 前端自绘标题栏负责拖拽与业务内容，前端不重复绘制窗口按钮
    windowOptions.titleBarStyle = 'hidden'
    windowOptions.titleBarOverlay = {
      color: '#0f1115',
      symbolColor: '#8b919e',
      height: 38,
    }
  } else if (isMac) {
    // macOS：隐藏标题栏但保留左上角交通灯按钮
    windowOptions.titleBarStyle = 'hiddenInset'
  }
  // Linux：保持默认 frame（Wayland/X11 差异大，titleBarOverlay 行为需单独验证）

  win = new BrowserWindow(windowOptions)

  // 外链一律交给系统默认浏览器：AI 回复里的外链带 target=_blank（streamdown
  // rehype-harden 加固产物），不拦会在应用内开新的 Electron 窗口；
  // 仅放行 http/https，其余协议（file: 等）直接拒绝，避免任意协议唤起
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//i.test(url)) {
      shell.openExternal(url)
    }
    return { action: 'deny' }
  })

  // Windows/Linux 隐藏菜单栏（菜单仍在，快捷键不丢）
  if (!isMac) {
    win.setMenuBarVisibility(false)
  }

  // 开发模式加载 Vite dev server（后端走 vite proxy），生产模式加载后端提供的静态文件
  if (process.env.ELECTRON_DEV === '1') {
    win.loadURL('http://localhost:5173')
  } else {
    win.loadURL('http://127.0.0.1:' + port)
  }

  // 加载失败兜底：窗口内显示可读错误，避免白屏
  // errorCode -3 是请求被中止（常见于导航竞争），不算真失败
  let showingLoadError = false
  win.webContents.on('did-fail-load', (_event, errorCode, errorDescription, validatedURL) => {
    if (errorCode === -3 || showingLoadError) return
    showingLoadError = true
    const html =
      '<head><meta charset="utf-8"><title>页面加载失败</title></head>' +
      '<body style="margin:0;background:#0f1115;color:#e6e9ef;' +
      'font-family:system-ui,sans-serif;display:flex;align-items:center;justify-content:center;height:100vh">' +
      '<div style="text-align:center;max-width:520px">' +
      '<h2 style="color:#f0f2f5">页面加载失败</h2>' +
      `<p style="color:#8b919e;line-height:1.6">地址：${validatedURL}<br>错误码：${errorCode}（${errorDescription}）</p>` +
      '<p style="color:#8b919e">后端服务可能未启动或已退出，请回到终端查看 launch.py 的日志。</p>' +
      '</div></body>'
    win.loadURL('data:text/html;charset=utf-8,' + encodeURIComponent(html))
      .then(() => { showingLoadError = false })
  })

  win.on('closed', () => {
    win = null
  })
}

// 创建一个伪终端，返回 { id, shell }：shell 名供前端终端标签标题展示
function createTerminal(cwd) {
  const id = `term-${++terminalIdCounter}`
  // 工作区目录可能已被删除，此时回退到项目根，避免 pty 启动直接抛错
  const workDir = cwd && fs.existsSync(cwd) ? cwd : projectRoot
  // Windows 优先 PowerShell 7，未安装则回退到 Windows PowerShell 5
  // 其他平台默认 bash
  let shell
  if (process.platform === 'win32') {
    // 先检查 pwsh.exe（PowerShell 7）是否存在
    const { execSync } = require('child_process')
    try {
      execSync('where pwsh.exe', { stdio: 'ignore' })
      shell = 'pwsh.exe'
    } catch {
      // 兜底：使用系统自带的 Windows PowerShell
      shell = 'powershell.exe'
    }
  } else {
    shell = 'bash'
  }
  const ptyProcess = pty.spawn(shell, [], {
    name: 'xterm-color',
    cols: 80,
    rows: 24,
    cwd: workDir,
    env: process.env
  })
  // pty 输出转发给渲染进程
  ptyProcess.onData((data) => {
    if (win && !win.isDestroyed()) {
      win.webContents.send('terminal:output', { id, data })
    }
  })
  // pty 退出时从表里移除
  ptyProcess.onExit(() => {
    terminals.delete(id)
  })
  terminals.set(id, ptyProcess)
  return { id, shell }
}

// 应用就绪后开窗：后端由 launch.py 托管，端口经 COMMON_CODE_BACKEND_PORT 递进来
app.whenReady().then(() => {
  const port = process.env.COMMON_CODE_BACKEND_PORT
  // dev 模式走 vite dev server，不需要后端端口
  if (process.env.ELECTRON_DEV !== '1' && !port) {
    dialog.showErrorBox(
      '缺少后端端口',
      '未检测到 COMMON_CODE_BACKEND_PORT 环境变量。\n\n' +
      '请使用 python launch.py 启动（后端由它托管）；\n' +
      '如需单独运行 Electron，请先设置该变量指向后端端口。'
    )
    app.exit(1)
    return
  }
  createWindow(port)

  // 终端 IPC
  ipcMain.handle('terminal:create', (_event, cwd) => createTerminal(cwd))
  ipcMain.on('terminal:input', (_event, { id, data }) => {
    const t = terminals.get(id)
    if (t) t.write(data)
  })
  ipcMain.handle('terminal:resize', (_event, { id, cols, rows }) => {
    const t = terminals.get(id)
    if (t) t.resize(cols, rows)
  })
  ipcMain.handle('terminal:dispose', (_event, id) => {
    const t = terminals.get(id)
    if (t) { t.kill(); terminals.delete(id) }
  })

  // 目录选择对话框，返回用户选中的目录路径，取消则返回 null
  ipcMain.handle('dialog:selectDirectory', async () => {
    const result = await dialog.showOpenDialog(win, {
      properties: ['openDirectory']
    })
    if (result.canceled || result.filePaths.length === 0) {
      return null
    }
    return result.filePaths[0]
  })

  // 在系统文件管理器中定位并选中指定路径（文件树右键「在资源管理器中打开」）
  ipcMain.handle('shell:revealInFolder', (_event, fullPath) => {
    if (typeof fullPath !== 'string' || !fullPath) return
    shell.showItemInFolder(fullPath)
  })
})

// 所有窗口关闭时的处理：终端控制台模式下生命周期由 launch.py 托管，
// 各平台（含 macOS）直接退出，后端进程树由 launch.py 统一清理
app.on('window-all-closed', () => {
  app.quit()
})

// 应用退出前清理终端进程，避免残留 pwsh
app.on('before-quit', () => {
  for (const [, t] of terminals) { t.kill() }
  terminals.clear()
})
