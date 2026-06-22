const { app, BrowserWindow, dialog, ipcMain } = require('electron')
const { spawn } = require('child_process')
const path = require('path')
const pty = require('node-pty')

// 保存 Python 子进程的引用，方便退出时清理
let pythonProcess = null
// 主窗口引用
let win = null
// 项目根目录，作为终端默认工作目录
let projectRoot = path.join(__dirname, '..')
// 终端实例表，id -> pty 进程
const terminals = new Map()
// 终端 id 自增计数器
let terminalIdCounter = 0

// 拉起 Python 后端服务
function spawnPython() {
  // cwd 设为 electron 的上级目录（项目根），保证 python -m server 能找到 server 包
  const projectRoot = path.join(__dirname, '..')
  // Windows 下用 shell: true 让系统自己找 uv.exe
  // 强制 Python UTF-8 输出，避免中文乱码
  const env = { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' }
  const child = spawn('uv', ['run', 'python', '-m', 'server'], {
    cwd: projectRoot,
    shell: true,
    env
  })

  // 累积 stdout 数据的缓冲区，按行解析
  let stdoutBuffer = ''

  child.stdout.on('data', (data) => {
    stdoutBuffer += data.toString()
    // 按换行符切分，最后一段可能是不完整的行，留在缓冲区里等下次拼接
    const lines = stdoutBuffer.split('\n')
    stdoutBuffer = lines.pop()

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) continue

      // 容错：某行可能不是 JSON（比如普通日志），解析失败就跳过，继续往下读
      let parsed
      try {
        parsed = JSON.parse(trimmed)
      } catch (e) {
        // 不是 JSON，忽略这行
        continue
      }

      // 读到带 port 字段的 JSON，说明后端服务已经就绪，可以开窗口了
      if (parsed && parsed.port) {
        createWindow(parsed.port)
      }
    }
  })

  child.stderr.on('data', (data) => {
    // 不打印到控制台，避免干扰用户
  })

  child.on('exit', (code) => {
    // code 非 0 且非 null 表示非正常退出，弹窗提示用户
    if (code !== 0 && code !== null) {
      dialog.showErrorBox('后端服务异常退出', `Python 后端进程退出，退出码：${code}`)
    }
  })

  return child
}

// 根据后端端口创建主窗口
function createWindow(port) {
  win = new BrowserWindow({
    width: 1400,
    height: 900,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js')
    }
  })

  // 开发模式加载 Vite dev server，生产模式加载后端提供的静态文件
  if (process.env.ELECTRON_DEV === '1') {
    win.loadURL('http://localhost:5173')
  } else {
    win.loadURL('http://localhost:' + port)
  }

  win.on('closed', () => {
    win = null
  })
}

// 创建一个伪终端，返回终端 id
function createTerminal(cwd) {
  const id = `term-${++terminalIdCounter}`
  // Windows 用 pwsh，其他平台用 bash
  const shell = process.platform === 'win32' ? 'pwsh.exe' : 'bash'
  const ptyProcess = pty.spawn(shell, [], {
    name: 'xterm-color',
    cols: 80,
    rows: 24,
    cwd: cwd || projectRoot,
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
  return id
}

// 应用就绪后拉起后端
app.whenReady().then(() => {
  pythonProcess = spawnPython()

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
})

// 所有窗口关闭时的处理
app.on('window-all-closed', () => {
  // macOS 上一般保留进程，等用户从 Dock 重新激活
  if (process.platform !== 'darwin') {
    if (pythonProcess) {
      pythonProcess.kill()
    }
    app.quit()
  }
})

// 应用退出前确保杀掉 Python 子进程
app.on('before-quit', () => {
  // 清理所有终端进程，避免残留 pwsh
  for (const [, t] of terminals) { t.kill() }
  terminals.clear()

  if (pythonProcess) {
    pythonProcess.kill()
    pythonProcess = null
  }
})

// macOS 点击 Dock 图标重新创建窗口
app.on('activate', () => {
  if (process.platform === 'darwin' && win === null) {
    // 窗口没了就重新拉起后端，后端就绪后会自动建窗口
    if (!pythonProcess) {
      pythonProcess = spawnPython()
    }
  }
})
