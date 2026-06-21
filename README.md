# Common Code

一个 AI 驱动的智能编程工作站。不是插件，不是终端脚本——是一个独立的桌面应用，AI 在你的代码库里自主工作：读文件、改代码、跑命令，你只需要说目标。

## 核心能力

- **自主编码**：AI 能读你的代码、改文件、执行命令，完成完整的开发任务，不是补全片段
- **权限守护**：AI 每次动文件或跑命令前都要你点头，危险操作绝不静默执行
- **全屏工作区**：左侧文件树、中间代码编辑器、右侧 AI 对话——一个窗口搞定一切
- **流式交互**：AI 回复逐字呈现，工具调用过程实时可见，不用等整段吐完
- **成本透明**：状态栏实时显示 token 用量和费用花了多少，心里有数

## 技术栈

- **后端**：Python + FastAPI，agentic 循环引擎，SSE 流式推送
- **前端**：React + TypeScript，Monaco Editor 代码查看，xterm.js 终端
- **桌面壳**：Electron，一条命令拉起整个应用

## 快速开始

### 1. 配置 LLM

在 `~/.agent/config.json` 里填你的 API 配置：

```json
{
  "llm_base_url": "https://api-inference.modelscope.cn/v1",
  "llm_api_key": "你的 API Key",
  "llm_model": "stepfun-ai/Step-3.7-Flash"
}
```

支持任何 OpenAI 兼容的 API。

### 2. 安装依赖

```bash
# Python 依赖
uv sync

# 前端依赖
cd frontend && npm install

# Electron 依赖
cd ../electron && npm install
```

### 3. 启动应用

```bash
# 构建前端
cd frontend && npm run build

# 启动 Electron（会自动拉起 Python 后端）
cd ../electron && npx electron .
```

### 开发模式（热更新）

三个终端分别跑：

```bash
# 终端 1：前端 dev server
cd frontend && npm run dev

# 终端 2：Python 后端（固定 8000 端口）
uv run python -m server

# 终端 3：Electron 加载前端 dev server
cd electron && npm run dev
```

## 项目结构

```
common_code/
├── frontend/        React 前端（三栏布局：文件树 + 编辑器 + AI 面板）
├── electron/        Electron 主进程壳
├── server/          FastAPI 后端（HTTP + SSE 接口）
├── query/           AI 引擎核心（agentic 循环、流式调用、压缩管线）
├── tools/           工具系统（Bash/文件读写/Glob/Grep + 权限 + 命令）
├── startup/         启动初始化（配置加载、工作目录、hooks）
└── ink/             终端 CLI 时代的历史遗留（可忽略）
```
