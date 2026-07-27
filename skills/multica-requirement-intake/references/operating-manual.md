# Multica 开发小队操作手册

## 目录

- 最简用法
- 确认后创建
- 三种准备方式
- 完整工作流
- 人工门禁
- 状态含义
- 持续跟踪
- 需求输入
- 创建失败恢复
- 规则来源
- 工作流异常
- 跨电脑使用

## 1. 最简用法

日常只需要告诉 Codex 或 OpenCode：问题是什么、属于哪个项目、希望创建后启动还是留在 backlog。即使你说“创建并启动”，Agent 也必须先与你确认完整需求草案，不能直接创建 Issue。

推荐提示词：

```text
使用 multica-requirement-intake，把下面的问题整理成顶层需求草案，先和我确认内容；我确认当前草案版本后，再提交到 Multica 的 <项目名>，分配给“开发交付小队”并启动，并持续跟踪到完成或需要我操作：

<问题、复现方式、期望结果、日志或截图>
```

Agent 应先通过只读检查解析 Workspace、项目、小队、v4 协议绑定能力和可能的重复 Issue，再给出完整的 `Requirement Draft v<N>`。你确认当前版本后，Agent 才能创建一个顶层需求。创建后必须先通过 `multica-workflow-incidents` 绑定并读回 protocol v4 metadata，验证成功后才能启动。Plan、Implementation 和开发任务由小队内部创建。小队名称默认是“开发交付小队”，也可以在提示词中明确指定另一支兼容小队。

## 2. 确认后创建

所有创建方式都使用同一个确认门禁：

1. Agent 澄清用户可见行为、验收条件、范围、非目标、兼容性和风险。
2. Agent 展示完整标题、描述、Workspace、项目、小队、启动状态、v4 绑定动作和重复 Issue 处理方式，并明确说明“尚未创建”。
3. 草案标记为 `Requirement Draft v<N>`。推荐使用以下回复确认：

```text
CONFIRM REQUIREMENT v1
```

4. 只有当前版本获得明确确认后才能创建。任何实质内容或目标变化都会生成新版本并要求重新确认。

这项确认只授权草案中显示的顶层操作：新建、原样复用或按显示字段更新已有 Issue，以及对应的启动方式。它不代表批准 Plan、最终交付、发布或部署。

## 3. 三种准备方式

### 只起草，不创建

```text
使用 multica-requirement-intake，把下面的问题整理成需求草稿。不要创建 Multica issue：<内容>
```

适合信息还不完整或你想先修改验收标准的场景。

### 确认后创建但暂不启动

```text
把下面的需求创建到 Multica 的 <项目名>，分配给开发交付小队，但留在 backlog，暂不启动：<内容>
```

适合先收集需求、稍后再排期。

### 确认后创建并启动

```text
把下面的需求提交到 Multica 的 <项目名>，分配给开发交付小队并启动：<内容>
```

Agent 获得当前草案确认后，会先以 `backlog` 创建并校验，执行确定性的 v4 metadata 绑定并读回验证，然后才转成 `todo`。切换完成后还必须通过服务端返回的 canonical ID 再次读回最终状态和协议 metadata；读回失败时不能声称创建成功。`todo` 才会触发小队长执行。启动后，Agent 默认继续跟踪，不应只返回 Issue 编号就结束。

## 4. 完整工作流

```text
Agent 与你确认 Requirement Draft
  -> Agent 创建并启动顶层需求，同时进入持续跟踪
  -> 开发队长创建 Plan
  -> 方案负责人完成需求设计
  -> 方案审查员 Review Loop
  -> 你批准 Plan
  -> 方案负责人拆分任务并完成拆分审查
  -> 集成负责人创建 Implementation 和任务
  -> 按批准的 workspace/PR 策略开发、测试、代码 Review Loop、合并任务分支
  -> 集成验证
  -> 顶层需求进入 in_review
  -> 你最终批准
  -> 集成负责人通过 Requirement PR 或本地方式合并需求分支
  -> 顶层需求 done
```

关键门禁：

- 未通过独立 Plan Review，不应请求你审批。
- 没有 `APPROVE PLAN v<N>`，不得开始实现。
- 没有代码审查和集成验证，Implementation 不得完成；关闭 PR 不会取消这些门禁。
- 没有 `APPROVE REQUIREMENT v<N>`，不得合并到默认分支。
- 创建需求的 `CONFIRM REQUIREMENT v<N>` 不能替代 `APPROVE PLAN v<N>` 或 `APPROVE REQUIREMENT v<N>`。
- 每次人工审批或决策完成后，Agent 应继续跟踪原需求，而不是把审批当作任务终点。

### 项目交付策略

Planner 会从产品仓库的 `multica.delivery.json` 和 Git remote 解析本次交付策略，并把完整快照和 `policy_digest` 写入 Plan。没有配置且存在受支持 GitHub remote 时，默认使用 `lightweight`、关闭 Task PR、启用 Requirement PR；没有任何 remote 时两个 PR 都关闭。不受支持或不明确的 remote 会阻塞 Plan，直到项目显式配置交付能力。

- `branch_only`：使用现有 checkout，开发、审查和集成串行执行。
- `lightweight`：只创建一个需求 worktree，任务串行执行。
- `isolated`：每个任务使用独立 worktree，可按任务依赖并行。

所有模式都保留需求分支、任务分支、独立 Review、测试、人工批准和合并记录。关闭 PR 只改变交付载体。Plan 批准后 Agent 不得自行切换模式；项目配置或 remote 能力变化时必须重新 Plan 和审批。

## 5. 开发执行中你需要操作的三个时刻

创建前的草案确认见第 2 节。需求启动后，Agent 只在以下三类人工门禁暂停并请求你操作，完成后继续跟踪。

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
- 交付策略摘要、需求 head、可选 Requirement PR/CI、测试和剩余风险清楚；
- 当前实现仍绑定已批准的 Plan 版本。

确认后评论：

```text
APPROVE REQUIREMENT v2
```

同时提及集成负责人。小队会自动把已审查需求 head 绑定到该批准；你不需要填写 SHA。随后才可通过 PR 或本地方式合并到默认分支。

## 6. 状态的实际意义

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

## 7. 持续跟踪与查看当前进展

创建并启动后，Agent 应主动持续读取完整 Issue 链。优先使用宿主提供的订阅、监控、等待或唤醒能力；否则使用有退避的只读轮询，不能忙等。

Agent 通常只在以下情况打扰你：

- 阶段发生实质变化；
- 需要人工 Plan 审批、产品决策或最终审批；
- 出现异常失败、长期停滞或配置问题；
- 需求进入 `done` 或 `cancelled`。

当需要你操作时，Agent 应给出当前版本、证据摘要、准确操作以及操作后的下一步。你回复并完成该门禁后，Agent 必须继续跟踪同一个需求。

如果当前 Codex/OpenCode 宿主无法保持会话或创建唤醒监控，Agent 必须在结束前明确说明能力限制并给出恢复跟踪的准确命令，不能声称正在持续跟踪。

可以直接问 Agent：

```text
使用 multica-requirement-intake 查看 T-123。告诉我它当前处于哪个阶段、正在等谁、我是否需要操作，以及下一步会发生什么。不要修改 issue。
```

Agent 应读取：

- 顶层需求及父子关系；
- 最新评论和明确的 Review 结论；
- metadata 中的 `plan_revision`、`waiting_on`、`blocked_reason`；
- 当前 assignee、状态、交付策略摘要、分支、commit、可选 PR 和测试结果。

## 8. 需求应该提供什么

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

## 9. 创建失败时怎么办

- 找不到 `multica` CLI：浏览器只能用于只读发现和跟踪；因为无法执行确定性的 v4 绑定，不能通过 UI 创建或实质更新受管需求。
- CLI 不可用：Agent 只能输出需求草稿或执行只读跟踪，必须明确说明“未创建”。
- 项目不明确：先让你选择，不能猜测后创建。
- 发现重复 issue：Agent 必须在草案中显示是原样复用、补充已有 Issue 还是经你明确允许后创建重复项；确认后才能执行。
- 创建成功但内容或协议绑定校验失败：保留在 `backlog`，修复原 issue，不能重新创建副本。
- 最终 canonical ID 读回失败，或 Workspace、内容、状态、protocol v4 metadata 不匹配：只能报告“未验证”或“部分成功”，不能把 Issue 编号当成创建成功证明。
- 小队中没有或存在多个“人工审批人”：队长会阻塞需求，等待 roster 配置修复。

## 10. 规则在哪里看

- **这份 Skill**：外部 Codex/OpenCode 如何起草、创建和跟进需求。
- **小队 Instructions**：阶段顺序、门禁和父 issue 完成规则。
- **各 Agent Instructions**：方案、审查、开发、集成角色的具体职责。
- **Issue 描述和 metadata**：本次需求的验收标准、Plan 版本、依赖和阻塞信息。
- **Issue 评论**：Review 结论、人工批准和决策记录。

需要判断“这次为什么没有继续”时，优先查看当前 issue 的状态、负责人、最新评论、`waiting_on` 和 `blocked_reason`。

## 11. 工作流异常

当 Agent 发现审批、Review、依赖、状态、角色、Runtime、Skill 或平台能力与工作流合同不一致时，先尝试在当前任务内安全修复。只有问题需要跨任务保留、可能复发、需要其他负责人或人工决定，或需要部署后验证时，才通过 `multica-workflow-incidents` 创建或复用独立 Incident。不存在后台扫描或中间 Observation。

- 只有继续执行会影响正确性、审批完整性、安全、隐私或 Git 历史时，Incident 才阻塞来源 issue 并写入 `waiting_on=workflow_fix`。
- Incident 修复直接创建普通 Requirement，完整经过 Plan、独立 Review、Implementation、集成验证和人工批准。
- 修复部署到受影响 workspace 并验证后关闭 Incident；验证失败则保持 `in_fix`。

## 12. 在不同电脑上使用

Skill 本身不保存用户名、安装目录、profile、workspace ID、小队 UUID、项目 ID、Token 或人工审批人 UUID。

在另一台电脑上：

1. 把完整的 `multica-requirement-intake` 目录安装到该 Agent 能发现的 Skill 目录。
2. 登录 Multica，并确保 CLI 可用，同时安装同版本的 `multica-workflow-incidents` Skill。浏览器只能作为只读发现和跟踪回退。
3. 使用同一个云端 workspace 时，小队和 issue 都是服务器端数据，不需要重建。
4. 使用另一个 workspace 时，其中必须先存在符合相同工作流契约的小队，并有唯一的“人工审批人”成员。
5. 新建 Codex/OpenCode 会话，让工具重新加载 Skill。

Skill 会在每次操作时重新解析 CLI、profile、workspace、小队、roster 和项目，不复用另一台电脑上的 UUID。

详细安装位置、可选环境变量和首次验证步骤见 [portable-setup.md](portable-setup.md)。
