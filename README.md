# Common Code

一个基于 agent loop 的 coding agent。独立桌面应用，AI 在你的代码库里自主干活：读文件、改代码、跑命令，你只管说目标。

## 核心能力

### 自主编码

不是补全片段，是完整干活。AI 拿到目标后自己决定读哪些文件、调哪些工具、怎么改，一轮轮循环直到搞定。每轮都是"思考→行动→观察"的闭环。

### 权限模式

两种模式，对话框左下角随时切：

- **自动编辑**：只读放行、改代码放行、安全命令放行。只有删文件、读敏感文件、危险命令才回来问你。
- **完全访问**：全部放行，AI 自己跑到底，除非它主动找你决策。

目标就是让你越用越放心地放权，少打断，让 AI 自己跑。

### 工作区

全屏三栏：文件树 + 代码编辑器 + AI 对话。底部内嵌终端，ripgrep 全局搜索，Git 状态一览。一个窗口搞定一切，不用切来切去。

### 流式交互

AI 回复逐字呈现，工具调用过程实时可见。状态栏显示 token 用量和花费，心里有数。

## 扩展能力

### 技能

可热插拔的 prompt 能力包。放一个 SKILL.md 进去，AI 就知道什么时候该用。斜杠命令也能直接触发。四种来源：工作区、个人、插件、内置，高优先级覆盖低优先级。自带 `skill-creator`（造新技能）和 `spec-mode`（复杂任务自动生成大纲、任务清单、验收清单）两个内置技能。

### 子代理

主代理派生隔离上下文的子代理干活，结果只回传最终文本，中间过程不污染主循环。两种内置类型：通用型（全工具）和探索型（只读搜索）。

### 多代理协作

多个 teammate 组团，文件邮箱双向通信，共享任务列表协调分工。和子代理的区别：子代理是单线汇报，多代理是对等协作。

### 插件系统

约定优先的插件容器。三类插件：标准（技能/命令/钩子/MCP）、模型供应商（可切换 LLM）、记忆后端（跨会话保留摘要）。三个加载来源，高优先级覆盖低优先级，用户能用自己的覆盖内置的。

## 技术栈

- **后端**：Python + FastAPI，agentic 循环引擎，SSE 流式推送
- **前端**：React + TypeScript，Monaco Editor，xterm.js 终端
- **桌面壳**：Electron，一条命令拉起整个应用

## 快速开始

### 1. 配置 LLM

在 `~/.agent/config.json` 里填 API 配置：

```json
{
  "llm_base_url": "https://api-inference.modelscope.cn/v1",
  "llm_api_key": "你的 API Key",
  "llm_model": "stepfun-ai/Step-3.7-Flash"
}
```

支持任何 OpenAI 兼容的 API。启动后在设置面板里也能改。

### 2. 安装依赖

```bash
uv sync
cd frontend && npm install
cd ../electron && npm install
```

### 3. 启动

```bash
cd frontend && npm run build
cd ../electron && npx electron .
```

或双击 `launch.bat` 一键启动。

### 开发模式

三个终端分别跑：

```bash
cd frontend && npm run dev          # 前端 dev server
uv run python -m server            # Python 后端（8000 端口）
cd electron && npm run dev          # Electron
```

## 项目结构

```
common_code/
├── frontend/        React 前端（三栏布局 + 终端）
├── electron/        Electron 主进程壳
├── server/          FastAPI 后端（HTTP + SSE + 权限桥接）
├── query/           循环引擎（agentic 循环、流式、压缩管线）
├── tools/           工具系统（动态工具池 + 权限 + 命令）
│   ├── implementations/  内置 6 工具
│   ├── skills/           技能机制
│   ├── subagent/         子代理机制
│   └── team/             多代理协作
├── startup/         启动初始化（配置 + 工作目录 + 钩子）
│   └── plugins/         插件系统
└── ink/             历史 CLI 遗留（可忽略）
```
