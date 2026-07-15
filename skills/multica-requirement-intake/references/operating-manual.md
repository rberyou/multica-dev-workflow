# Multica 开发小队操作手册

## 1. 最简用法

日常只需要告诉 Codex 或 OpenCode：问题是什么、属于哪个项目、是“先起草”还是“直接提交”。

推荐提示词：

```text
使用 multica-requirement-intake，把下面的问题整理成顶层需求并提交到 Multica 的 <项目名>，分配给“开发交付小队”并启动：

<问题、复现方式、期望结果、日志或截图>
```

Agent 应只创建一个顶层需求。Plan、Implementation 和开发任务由小队内部创建。小队名称默认是“开发交付小队”，也可以在提示词中明确指定另一支兼容小队。

## 2. 三种创建方式

### 只起草，不创建

```text
使用 multica-requirement-intake，把下面的问题整理成需求草稿。不要创建 Multica issue：<内容>
```

适合信息还不完整或你想先修改验收标准的场景。

### 创建但暂不启动

```text
把下面的需求创建到 Multica 的 <项目名>，分配给开发交付小队，但留在 backlog，暂不启动：<内容>
```

适合先收集需求、稍后再排期。

### 创建并启动

```text
把下面的需求提交到 Multica 的 <项目名>，分配给开发交付小队并启动：<内容>
```

Agent 会先以 `backlog` 创建并校验，然后转成 `todo`。`todo` 才会触发小队长执行。

## 3. 完整工作流

```text
你或外部 Agent 创建顶层需求
  -> 开发队长创建 Plan
  -> 方案负责人完成需求设计
  -> 方案审查员 Review Loop
  -> 你批准 Plan
  -> 方案负责人拆分任务并完成拆分审查
  -> 集成负责人创建 Implementation 和任务
  -> 开发、测试、代码 Review Loop、合并任务分支
  -> 集成验证
  -> 顶层需求进入 in_review
  -> 你最终批准
  -> 集成负责人合并需求 PR
  -> 顶层需求 done
```

关键门禁：

- 未通过独立 Plan Review，不应请求你审批。
- 没有 `APPROVE PLAN v<N>`，不得开始实现。
- 没有代码审查和集成验证，Implementation 不得完成。
- 没有 `APPROVE REQUIREMENT v<N>`，不得合并到默认分支。

## 4. 你需要操作的三个时刻

### Plan 审批

当需求设计进入 `in_review` 时，先查看：

- 当前 `plan_revision`；
- 方案审查员是否明确给出 `APPROVED`；
- 根因、影响范围、兼容性、测试和回滚是否合理；
- 是否仍有待决策问题。

确认后评论：

```text
APPROVE PLAN v2
```

同时提及当前方案负责人。版本号必须与待审批版本一致。

可以让 Agent 代操作：

```text
我明确批准 T-123 的 Plan v2。使用 multica-requirement-intake 检查 review 和版本仍然有效，然后发布审批评论。
```

Agent 不能根据“看起来可以”自行推断批准。

### 决策阻塞

当 issue 为 `blocked` 且 `waiting_on` 指向人工决策时，查看 `blocked_reason`、备选项和影响，再评论：

```text
DECISION: 采用方案 B，保持旧接口兼容一个版本，并记录废弃提示。
```

同时提及当前阶段负责人。可以让 Agent 先总结，但决定必须由你明确给出。

### 最终需求审批

当顶层需求进入 `in_review` 时，检查：

- Plan 与 Implementation 均已完成；
- 集成验证通过；
- 需求验收条件逐项有结果；
- Requirement PR、CI、测试和剩余风险清楚；
- 当前实现仍绑定已批准的 Plan 版本。

确认后评论：

```text
APPROVE REQUIREMENT v2
```

同时提及集成负责人。小队随后才可合并到默认分支。

## 5. 状态的实际意义

| 状态 | 实际意义 | 你通常要做什么 |
|---|---|---|
| `backlog` | 执行门禁关闭；即使已分配 Agent 也不启动 | 等待排期或补充信息 |
| `todo` | 可执行，会触发已分配的 Agent 或小队 | 通常无需操作 |
| `in_progress` | 当前负责人正在处理 | 查看进展，避免重复催办 |
| `in_review` | 等待人工审批或最终审查 | 按对应审批清单确认 |
| `blocked` | 流程无法继续 | 查看 `waiting_on` 和 `blocked_reason` |
| `cancelled` | 任务被取消或替代 | 查看替代任务链接；它不会自动满足依赖 |
| `done` | 负责人核验后手动完成 | 确认交付结果即可 |

父 issue 不会因为所有子 issue 完成而自动变成 `done`。对应负责人必须核验后手动完成。

## 6. 如何查看当前进展

可以直接问 Agent：

```text
使用 multica-requirement-intake 查看 T-123。告诉我它当前处于哪个阶段、正在等谁、我是否需要操作，以及下一步会发生什么。不要修改 issue。
```

Agent 应读取：

- 顶层需求及父子关系；
- 最新评论和明确的 Review 结论；
- metadata 中的 `plan_revision`、`waiting_on`、`blocked_reason`；
- 当前 assignee、状态、分支、commit、PR 和测试结果。

## 7. 需求应该提供什么

最少提供：

- 哪个项目；
- 出现了什么问题；
- 如何复现或观察到；
- 期望变成什么样；
- 哪些行为不能被破坏。

日志、错误 JSON、截图、样例输入和相关代码位置很有价值。不要把密码、Token、生产隐私数据放进 issue。

不必提前提供：

- Plan 版本；
- 任务拆分；
- Agent UUID；
- `human_approver_id`；
- 分支名和任务依赖图。

这些由小队在后续阶段生成。其中人工审批人由队长从 Squad Roster 中唯一的“人工审批人”角色自动取得。

## 8. 创建失败时怎么办

- 找不到 `multica` CLI，但 Agent 能控制已登录的 Multica 浏览器：允许使用 UI 创建。
- CLI 和浏览器都不可用：Agent 只能输出需求草稿，必须明确说明“未创建”。
- 项目不明确：先让你选择，不能猜测后创建。
- 发现重复 issue：默认复用或补充已有 issue，不创建第二条。
- 创建成功但后续校验失败：保留在 `backlog`，修复原 issue，不能重新创建副本。
- 小队中没有或存在多个“人工审批人”：队长会阻塞需求，等待 roster 配置修复。

## 9. 规则在哪里看

- **这份 Skill**：外部 Codex/OpenCode 如何起草、创建和跟进需求。
- **小队 Instructions**：阶段顺序、门禁和父 issue 完成规则。
- **各 Agent Instructions**：方案、审查、开发、集成角色的具体职责。
- **Issue 描述和 metadata**：本次需求的验收标准、Plan 版本、依赖和阻塞信息。
- **Issue 评论**：Review 结论、人工批准和决策记录。

需要判断“这次为什么没有继续”时，优先查看当前 issue 的状态、负责人、最新评论、`waiting_on` 和 `blocked_reason`。

## 10. 在不同电脑上使用

Skill 本身不保存用户名、安装目录、profile、workspace ID、小队 UUID、项目 ID、Token 或人工审批人 UUID。

在另一台电脑上：

1. 把完整的 `multica-requirement-intake` 目录安装到该 Agent 能发现的 Skill 目录。
2. 登录 Multica，并确保 CLI 或已登录的浏览器界面可用。
3. 使用同一个云端 workspace 时，小队和 issue 都是服务器端数据，不需要重建。
4. 使用另一个 workspace 时，其中必须先存在符合相同工作流契约的小队，并有唯一的“人工审批人”成员。
5. 新建 Codex/OpenCode 会话，让工具重新加载 Skill。

Skill 会在每次操作时重新解析 CLI、profile、workspace、小队、roster 和项目，不复用另一台电脑上的 UUID。

详细安装位置、可选环境变量和首次验证步骤见 [portable-setup.md](portable-setup.md)。
