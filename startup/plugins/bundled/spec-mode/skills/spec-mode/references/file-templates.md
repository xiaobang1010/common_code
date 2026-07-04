# Spec 三件套文件模板

生成 spec.md / tasks.md / checklist.md 时参考这些模板。按实际需求填充内容，别留占位符。
文档语言跟随用户对话语言（中文对话写中文，英文对话写英文）。

---

## spec.md 模板

需求大纲文档。回答"做什么、为什么、怎么做"，采用 EARS 风格让需求可形式化验收、减少歧义。

```markdown
# <任务名> Spec

## Why
<1-2 句话说明问题或机会，为什么需要这个变更>

## What Changes
- <变更项 1：用要点列出具体改动>
- <变更项 2：如有破坏性变更，行尾标注 **BREAKING**>
- <变更项 3>

## Impact
- Affected specs: <受影响的既有 spec 能力列表，无则填"无">
- Affected code: <受影响的关键文件/系统列表，无则填"无">

## ADDED Requirements

### Requirement: <新增需求名称>
The system SHALL <用 SHALL 句式描述系统应提供的能力>

#### Scenario: <成功场景名称>
- **WHEN** <用户/系统执行某动作>
- **THEN** <期望结果>

#### Scenario: <异常或边界场景名称>
- **WHEN** <触发条件>
- **THEN** <期望结果>

## MODIFIED Requirements

### Requirement: <既有需求名称>
<完整描述修改后的需求，而非仅描述差异>

#### Scenario: <场景名称>
- **WHEN** <条件>
- **THEN** <结果>

## REMOVED Requirements

### Requirement: <被移除的需求名称>
**Reason**: <移除原因>
**Migration**: <迁移/替代方案>
```

**写法要点：**
- 需求用 "The system SHALL ..." 句式，每条需求至少配一个 Scenario，Scenario 用 WHEN/THEN 结构，可直接映射为验收点
- Why 控制在 1-2 句，点明问题或机会；What Changes 用要点列出具体改动，破坏性变更行尾标注 **BREAKING**
- Impact 必须列出受影响的代码/文件，便于评估改动范围
- ADDED / MODIFIED / REMOVED Requirements 按实际变更类型填充，不涉及的章节整节删除（不要保留空占位）
- 需求描述要具体可验收，避免"系统应良好运行"这类空话

---

## tasks.md 模板

执行任务列表。回答"按什么顺序做什么"，并用显式依赖块标注依赖与可并行关系。

```markdown
# <任务名> 任务列表

## 阶段一：<阶段名，如 基础设施 / 数据层 / 接口层>

- [ ] 1.1 <任务描述，动宾结构，如"创建用户模型 User.ts，含字段 id/email/name/createdAt">
- [ ] 1.2 <任务描述>
- [ ] 1.3 <任务描述>

## 阶段二：<阶段名>

- [ ] 2.1 <任务描述>
- [ ] 2.2 <任务描述>

## 阶段三：<阶段名，如 测试 / 文档 / 部署>

- [ ] 3.1 <任务描述>
- [ ] 3.2 <任务描述>

# Task Dependencies
- <Task N> 依赖 <Task M>
- <Task A> / <Task B> 可并行执行（相互独立）
```

**写法要点：**
- 任务要可执行、可验证。写"创建 User 模型，含 id/email 字段"而非"处理用户"
- 按依赖顺序排列（被依赖的放前面）
- 粒度适中：一个任务对应一次有意义的代码改动，别太碎（"加一个字段"）也别太粗（"实现后端"）
- 编号便于引用（执行时说"开始 1.2"）
- 分阶段让大任务有节奏感
- 文末 `# Task Dependencies` 块必须显式标注依赖与可并行关系：用"- Task N 依赖 Task M"标依赖，用"- Task A / Task B 可并行执行"标可并行；无依赖时也保留该块并注明"无外部依赖"

---

## checklist.md 模板

验收清单。回答"怎么算做完了"。

```markdown
# <任务名> 验收清单

## 功能验收
- [ ] <可检验的验收项，如"用户可以用邮箱密码登录">
- [ ] <如"登录失败时显示错误提示">
- [ ] <如"未注册用户登录时提示先注册">

## 代码质量
- [ ] <如"所有新增函数有类型注解">
- [ ] <如"无 console.log 残留">
- [ ] <如"关键逻辑有注释">

## 测试
- [ ] <如"单元测试覆盖核心逻辑">
- [ ] <如"边界情况有测试用例">

## 文档
- [ ] <如"README 更新了使用说明">
- [ ] <如"API 接口文档已更新">
```

**写法要点：**
- 每项必须是可明确判定"通过/未通过"的，别写"代码质量好"这种主观项
- 功能验收对照 spec.md 的目标，确保需求被覆盖
- checklist 项与 tasks 不是一一对应——一个 task 可能关联多个验收项，多个 task 也可能共同满足一个验收项
- 执行 task 时，顺手把能确认的 checklist 项勾上

---

## 状态联动示例

执行 task 1.1（创建用户模型）后：

**tasks.md 变化：**
```markdown
- [x] 1.1 创建用户模型 User.ts，含字段 id/email/name/createdAt
- [ ] 1.2 实现用户注册接口
```

**checklist.md 变化**（如该 task 关联验收项）：
```markdown
- [x] User 模型包含 id/email/name/createdAt 字段
- [ ] 注册接口能创建用户记录
```

注意：不是每个 task 都对应 checklist 项。只勾确实已满足的验收项。
