const { app, BrowserWindow, dialog } = require('electron')
const { spawn } = require('child_process')
const path = require('path')

// 保存 Python 子进程的引用，方便退出时清理
let pythonProcess = null
// 主窗口引用
let win = null

// 拉起 Python 后端服务
function spawnPython() {
  // cwd 设为 electron 的上级目录（项目根），保证 python -m server 能找到 server 包
  const projectRoot = path.join(__dirname, '..')
  // Windows 下用 shell: true 让系统自己找 uv.exe
  const child = spawn('uv', ['run', 'python', '-m', 'server'], {
    cwd: projectRoot,
    shell: true
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
    console.error('[python stderr]', data.toString())
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
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js')
    }
  })

  win.loadURL('http://localhost:' + port)

  win.on('closed', () => {
    win = null
  })
}

// 应用就绪后拉起后端
app.whenReady().then(() => {
  pythonProcess = spawnPython()
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
