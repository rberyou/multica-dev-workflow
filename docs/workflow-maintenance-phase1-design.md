# 工作流维护第一阶段详细设计

- 设计版本：`0.4.2`
- 状态：实现中
- 对应路线图：`0.3.2`
- 阶段：异常上报与 Observer 闭环

## 1. 阶段目标

第一阶段建立开发 Agent 与 Observer 之间完整、可靠的问题发现闭环：

```text
开发 Agent 主动上报 ──┐
                      ├──> Observer 分类与去重 ──> Incident
Observer 定时巡检 ────┘                              │
                                                     ▼
                                           人工决定是否维护
                                                     │
                                                     ▼
                                           最小 Maintenance Case
                                                     │
                                                     ▼
                                           现有普通开发流程修复
                                                     │
                                                     ▼
                                             Observer 验证并关闭
```

本阶段不启用 Maintainer 和 Reviewer 自动维护，也不向 Agent 开放合并、发布、部署或回滚权限。阶段重点是证明问题可以被准确发现、可靠记录、正确去重、持续跟踪并最终验证。

## 2. 范围与非目标

### 范围

- 登记使用开发小队的项目。
- 为开发工作流创建的 Issue 注入可识别来源。
- 支持开发 Agent 主动上报工作流异常。
- 支持 Observer 增量巡检、每日全量巡检和外部健康检查。
- 支持 finding 分类、Incident 去重、证据更新和复发关联。
- 支持一次人工维护决策和最小 Maintenance Case。
- 支持普通开发流程修复后的 Observer 验证与 Incident 关闭。

### 非目标

- 不由 Observer 修改源码或提出实现方案。
- 不由 Observer 自动启动维护。
- 不实现 Maintainer/Reviewer Review Loop。
- 不实现自动合并、发布、Canary、部署或回滚。
- 不宣称已经具备生产级硬权限隔离。
- 不扫描未登记项目中的普通业务 Issue。

## 3. 参与者与职责

### 开发 Agent

- 在执行开发工作流时识别疑似工作流异常。
- 提交来源 Issue、规则、严重度、预期行为、实际行为和脱敏证据。
- 在 high/urgent 风险下按规则阻塞受影响任务。
- 不创建 Incident，不决定是否维护，也不直接启动普通开发修复。

### Observer

- 接收主动上报并恢复未完成上报。
- 巡检登记项目中的工作流记录和控制面状态。
- 运行确定性规则并生成 finding。
- 分类、去重并维护 Incident。
- 生成维护决策摘要并等待人工决定。
- 跟踪普通开发修复，验证结果并关闭 Incident。

### 人工审批人

- 决定已确认 Incident 是否进入维护。
- 对范围变化或无法自动分类的问题作出决策。
- 第一阶段仍负责现有开发、合并、发布和部署流程中的既有人工操作。

### 普通开发流程

- 人工批准维护后，暂时代行未来 Maintainer 的修复职责。
- 在最小 Maintenance Case 中记录实现任务、PR、合并、发布和部署证据。
- 不改变 Observer 对 Incident 和最终验证的所有权。

## 4. 总体组件

### 项目登记表

每个启用开发小队的项目对应一个登记记录。登记记录位于工作流运维项目，关联目标 Project、开发小队、工作流实例和 Observer 扫描状态。

项目未登记时，Observer 不主动扫描；若开发 Agent 从未登记项目上报异常，Observer 将其分类为工作流接入缺口并请求人工处理。

### Reporter

Reporter 是开发 Agent 使用的确定性上报入口。它先在工作流运维项目中创建或复用轻量的 Observation Inbox 记录，再尽力在来源 Issue 写入 pending 标记，最后通知 Observer。Inbox 记录是上报的权威持久化来源，避免来源 Issue 不可写或 metadata 达到上限时丢失报告。

### Observation Inbox

每个主动报告对应一个可去重的 Observation 记录。它不是 Incident，也不能启动维护。Observer 处理并完成 Incident 来源链接后，将 Observation 标记为已处理；失败记录保持待处理，以便后续扫描恢复。

单条 Observation 失败不得中止其余项目和记录的扫描。失败记录最多自动尝试五次；达到上限后进入 `quarantined`，保留最后错误和尝试次数，由专用运行态巡检生成可追踪 Finding。相同 fingerprint 的新 payload 会重新进入 `pending`，但相同失败 payload 不会无限重试。

### Observer 扫描器

扫描器按项目登记范围读取候选 Issue、相关运行记录和控制面状态，执行版本化规则集，并将 finding 交给 Incident 管理器。

### Incident 管理器

Incident 管理器负责分类、去重、证据合并、来源链接、通知冷却、复发关系和生命周期更新。开发 Agent 与扫描器均不能绕过该组件直接创建 Incident。

### Observer Control

每个工作流实例有一个 Observer Control 记录，保存全局扫描 lease、最近成功时间、全量扫描时间和错误状态。每个项目登记记录保存该项目自己的 committed cursor 和 checkpoint，避免多项目共享游标。

### 最小 Maintenance Case

人工批准维护后创建一个与 Incident 关联的 Maintenance Case。第一阶段只用于跟踪普通开发修复及其证据，不展开 Plan、Implementation、Canary、Rollout 子 Issue。

### 外部健康检查

Observer 无法可靠监控自己的调度器。独立健康检查负责检测 Autopilot 停止、扫描长期失败、游标停滞和全量扫描超期。

### 命令接口

第一阶段提供以下确定性命令面：

| 命令 | 调用者 | 作用 |
|---|---|---|
| `register-project` | 操作员 | 创建或更新项目登记、工作流实例和项目独立游标 |
| `bind-workflow-issue` | 开发 Agent | 为新建 Issue 注入登记来源、实例、对象类型和根需求标记 |
| `report-anomaly` | 开发 Agent | 创建或更新 Observation，并按严重度唤醒 Observer |
| `scan --mode incremental` | Observer Autopilot | 扫描登记项目的增量变化 |
| `scan --mode full` | Observer/操作员 | 执行每日全量校验 |
| `triage` | Observer | 校验分类结论并更新 Incident |
| `prepare-maintenance-decision` | Observer | 生成摘要绑定的人工维护决策请求 |
| `record-maintenance-decision` | 人工宿主上下文 | 验证人工身份、评论和摘要，并创建最小 Maintenance Case |
| `record-maintenance-progress` | 普通开发流程/人工宿主 | 按顺序记录开发启动、修复就绪、发布和部署证据；不得跳过前置状态 |
| `verify-fix` | Observer | 执行复现场景并写入独立验证结论 |
| `health` | 外部操作员或调度器 | 检查 Observer 调度与扫描新鲜度 |

所有命令支持 JSON 输出。无法完整读取、校验或持久化必要状态时必须返回非零结果，不得将部分成功报告为健康。迁移期内，现有 `report-incident` 和 `audit` 分别作为 `report-anomaly` 和 `scan` 的兼容别名。

## 5. 项目登记与 Issue 来源

### 项目登记记录

每个登记记录至少包含：

```text
workflow_instance_id
workspace_id
project_id
project_name
development_squad_id
managed_agent_ids
protocol_revision
enabled
registered_at
committed_cursor
checkpoint_cursor
```

运行时 UUID 只保存在 Multica 运行态或本地映射中，不写入 Git 可移植配置。

### Issue 来源标记

开发工作流创建 Issue 时，由统一创建入口自动写入：

```text
managed_by=multica-dev-workflow
workflow_instance_id=<实例>
workflow_object_type=<类型>
root_requirement_id=<顶层需求>
created_by_role=<开发角色>
protocol_revision=<协议版本>
```

人工创建的顶层需求在进入开发流程时补充相同的工作流实例标记。

Observer 的候选范围包括：

- 已登记项目中带工作流实例标记的 Issue。
- 已登记项目中由受管 Agent 创建或负责、但缺少标记的孤立 Issue。
- 候选 Issue 的有限父子链。
- 带有 pending 上报标记的 Issue。
- 候选 Issue 对应的任务运行摘要。
- Git 管理的工作流控制面对象。

Observer 不通过标题或自然语言内容猜测 Issue 是否属于开发工作流。

### 运行态记录模型

Observation 至少记录：

```text
workflow_object_type=observation
observation_fingerprint
workflow_instance_id
source_issue_id
reporter_agent_id
rule_id
severity
affected_entity
payload_digest
status=pending|processing|processed|failed|quarantined
incident_id
attempt_count
last_error
```

Incident 至少记录：

```text
workflow_object_type=incident
incident_dedupe_key
logical_status
severity
verdict
source_issue_ids
evidence_digest
recurrence_of
maintenance_case_id
waiting_on
first_seen_at
last_seen_at
last_notified_at
```

Maintenance Case 至少记录：

```text
workflow_object_type=maintenance_case
incident_id
maintenance_intake_digest
approval_comment_id
human_approver_id
executor
logical_status
root_requirement_id
review_issue_id
plan_revision
implementation_issue_ids
pr_number
merge_commit_sha
release_version
deployment_target
observer_verification_id
```

Observation 和 Incident 的证据日志必须有数量与长度上限。敏感或超限内容只保存摘要和来源引用，不复制完整评论、运行消息或凭据。

## 6. 主动上报流程

开发 Agent 使用统一命令：

```text
observer.py report-anomaly
  --source-issue <ID>
  --rule-id <RULE>
  --severity <urgent|high|medium|low>
  --entity <TYPE:ID>
  --summary <SUMMARY>
  --expected <EXPECTED>
  --actual <ACTUAL>
```

上报流程：

1. 验证来源 Issue 属于当前工作流实例，或明确记录未登记来源。
2. 对输入内容执行脱敏和长度限制。
3. 生成包含来源 Issue 和 rule/entity 的稳定 observation fingerprint。
4. 创建或复用 Observation Inbox 记录并写入脱敏 payload。
5. 尽力在来源 Issue 写入 pending 标记和 Observation ID；来源写入失败不得删除 Inbox 记录。
6. medium/low 等待下一次增量扫描；high/urgent 额外触发一次 Observer 运行。
7. Observer 成功创建或复用 Incident 并完成来源链接后，将 Observation 标记为已处理，再清除来源 pending 标记。

Observation fingerprint 为：

```text
SHA256(workflow_instance_id + source_issue_id + rule_id
       + affected_entity_type + affected_entity_id)
```

不同来源的 Observation 可以独立保留，但会通过不包含 `source_issue_id` 的 Incident key 汇聚到同一个根问题。

若任一步骤失败，Reporter 保留可恢复信息并返回非零结果。开发 Agent 不进行无界重试。

## 7. 主动巡检设计

### 调度

- 每小时执行一次增量巡检。
- 每天执行一次全量巡检。
- 每 15 分钟由外部上下文执行一次 Observer 健康检查。
- 同一工作流实例同时只允许一个扫描任务运行。

### 增量巡检

增量巡检读取上次成功游标之后发生变化的候选对象，并向前重叠十分钟，防止延迟写入和分页边界造成遗漏。

### 全量巡检

全量巡检重新校验：

- 所有活跃工作流 Issue。
- 最近关闭但仍处于验证窗口的 Issue。
- pending 上报和孤立 Issue。
- Agent、Skill、Squad、Runtime、Autopilot 和触发器等控制面状态。

Registration、Observer Control、Observation、Incident 和 Maintenance Case 等运维控制记录由各自专用处理器校验，不执行普通开发 Issue 规则，避免 Observer 自己的写入形成告警循环。

全量巡检用于发现增量游标之外的漂移，不替代增量游标。控制面漂移必须对比当前阶段批准的 phase-1 contract，而不是尚未实现的最终生产架构。

### 读取原则

扫描器默认只读取 metadata、状态、关系、assignee 和运行摘要。只有确定性规则发现异常或主动报告明确引用时，才读取必要评论或运行消息；读取内容必须脱敏。

## 8. 游标、并发与失败恢复

游标使用 `(updated_at, issue_id)`，避免相同更新时间的多个 Issue 被遗漏。

Observer Control 维护全局运行状态：

```text
scan_mode
scan_started_at
scan_upper_bound
last_success_at
last_full_scan_at
lease_owner
lease_expires_at
scanned_count
finding_count
status
error
```

每个项目登记记录独立维护 `committed_cursor` 和 `checkpoint_cursor`。

扫描流程：

1. 获取工作流实例级、带过期时间的扫描 lease。
2. 记录扫描开始时的服务端 `scan_upper_bound`，本次只处理不晚于该时间的变化。
3. 对每个登记项目从 committed cursor 前十分钟开始稳定排序分页。
4. 每完成一页更新该项目 checkpoint 和全局计数。
5. 先持久化本页所有 finding 和 Incident 更新。
6. 全部项目成功后，将各项目 committed cursor 提交到 `scan_upper_bound`；即使没有候选 Issue 也推进时间水位。
7. 失败时保留各项目旧 committed cursor，下次安全重放。
8. 过期 lease 可由后续扫描接管；有效 lease 存在时新扫描直接退出。

同一扫描内以 `(entity_id, updated_at)` 去除分页重复；跨扫描重复由 Incident dedupe key 和证据 fingerprint 处理。

## 9. 规则与 Finding

规则必须确定性、版本化并具有稳定 `rule_id`。第一阶段至少覆盖：

- 工作流来源和必需 metadata 缺失。
- 非法状态转换、依赖未完成或阶段长期停滞。
- Agent、Squad、assignee 或 Runtime 绑定错误。
- Review 或人工批准缺失、过期或身份不匹配。
- 重复、孤立或错误关联的工作流 Issue。
- 任务失败、超时或调度停止。
- Git desired state 与 Multica 运行态漂移。
- pending 上报未完成或无法恢复。

Finding 统一包含：

```text
rule_id
severity
affected_entity
expected
actual
evidence_references
recommended_disposition
```

规则只报告可复现事实，不直接决定修复方案。

确定性规则负责生成事实 Finding；Observer Agent 可以结合上下文作语义分类，但分类只能通过 `triage` 命令提交。该命令负责验证允许的结论、当前状态、来源证据和合法后续动作，避免模型直接修改生命周期。

## 10. Incident 分类与去重

### Incident 生命周期

Incident 使用独立于 Multica 展示列的逻辑状态：

```text
new -> triaging -> awaiting_maintenance_decision
-> maintenance_approved -> in_fix -> awaiting_verification -> resolved
```

旁路状态包括：

- `routed`：已明确交给普通项目、Runtime 或 Multica 产品责任方。
- `deferred`：人工暂不维护，保留原因和下次复核时间。
- `false_positive`：确认不是有效问题并关闭。
- `blocked`：证据、身份或状态不完整，无法安全继续。

只有 `resolved` 和 `false_positive` 表示 Observer 可以完成对应 Issue；`routed` 和 `deferred` 保持可追踪，并在证据、严重度、影响范围或复核时间变化时重新评估。

### 分类结论

Observer 只允许以下结论：

- `CONFIRMED_WORKFLOW_BUG`
- `WORKFLOW_GAP`
- `USAGE_ERROR`
- `PROJECT_DEFECT`
- `RUNTIME_INCIDENT`
- `MULTICA_PRODUCT_DEFECT`
- `FALSE_POSITIVE`
- `DECISION_REQUIRED`

确认的工作流缺陷和需要补足的工作流能力进入人工维护决策。其他结论应路由到对应责任方；除非分类或证据发生变化，否则不创建 Maintenance Case。

### Incident 去重

Incident key 为：

```text
SHA256(workflow_instance_id + protocol_revision + rule_id
       + affected_entity_type + affected_entity_id)
```

- 存在活跃匹配时更新同一个 Incident。
- 相同 evidence fingerprint 不重复追加。
- 未修复的同版本复发复用原 Incident。
- 已验证修复后再次发生时创建新 Incident，并写入 `recurrence_of`。
- 普通重复证据遵守通知冷却；严重度提升、影响扩大或首次确定性确认立即通知。

## 11. 人工维护决策

Observer 为确认问题生成维护决策摘要，至少绑定：

```text
incident_id
dedupe_key
severity
affected_scope
source_issue_set
workflow_version
evidence_digest
```

人工通过精确摘要批准是否维护。批准只授权摘要覆盖的范围；新增受影响范围、兼容性或安全风险必须生成新摘要并再次决策。

批准评论格式为：

```text
APPROVE WORKFLOW MAINTENANCE <short-digest>
```

暂不维护使用：

```text
DEFER WORKFLOW MAINTENANCE <short-digest>
reason=<简要原因>
next_review_at=<ISO-8601 时间>
```

批准或暂缓评论必须由登记的人工审批人发布，并与当前完整摘要 digest 精确匹配。暂缓必须同时记录原因和 `next_review_at`，Incident 进入 `deferred`，在复核时间到达或风险发生实质变化前不重复请求相同决定。任何 Agent 都不得代写人工决定。

批准后创建一个最小 Maintenance Case，记录 Incident、人工批准证据、执行负责人和普通开发流程产生的任务/PR 链接。拒绝维护时保留 Incident 和理由，不启动修复。

第一阶段 Maintenance Case 使用以下高层状态：

```text
approved -> in_development -> fix_ready -> awaiting_deployment
-> awaiting_observer_verification -> completed
```

无法继续的 Case 进入 `blocked` 并记录责任方和原因，不自动扩大维护范围。

## 12. 修复跟踪与 Observer 验证

普通开发流程完成修复后，Maintenance Case 记录：

- 普通开发顶层 Requirement 和最终 integration-validation Issue。
- Code Reviewer 的精确 review_comment_id、Plan revision 与被审查 PR head SHA。
- 修复 commit 和 PR。
- 测试与 CI 结果。
- 发布或部署版本。
- 原问题复现场景。
- 需要 Observer 验证的环境。

这些证据通过 `record-maintenance-progress` 顺序写入：`in-development` 绑定普通开发 Issue，`fix-ready` 绑定 Requirement、integration-validation、PR 和 merge commit，`release-recorded` 绑定 `v` 前缀 tag、source commit 与 Release Request digest，`deployment-recorded` 绑定 Workspace deployment Plan digest、已完成 apply journal 及其生成的不可变 plan-digest Workspace deployment record。另有每个 Workspace 的最新部署指针供 `workflow.py verify` 展示，但它不会覆盖历史 Case 引用的证据记录。逻辑状态最后写入，使部分持久化失败后可以安全重试。

Observer 验证必须独立执行原规则和复现场景，并确认：

- 原 finding 不再出现。
- 回归测试通过。
- 目标环境运行的是被验证版本。
- 没有产生新的控制面漂移。
- Maintenance Case 与批准范围一致。

代码合并但尚未部署时，Incident 保持等待验证，不得提前关闭。验证失败时追加证据并将 Case 退回普通开发流程；验证成功后完成 Maintenance Case 并关闭 Incident。

## 13. 健康监控

外部健康检查至少验证：

- Observer Autopilot 存在且处于预期状态。
- 最近一次增量扫描未超过 135 分钟。
- 最近一次全量扫描未超过 26 小时。
- 最近一次成功扫描已提交新的时间水位；项目长期存在变化但游标不推进时报告停滞。
- 没有长期未释放的 lease。
- 最近扫描不存在未记录的持久化失败。

健康检查失败时直接通知人工或外部操作员，不依赖故障中的 Observer 自己创建 Incident。

## 14. 阶段安全边界

第一阶段主要通过确定性脚本、角色说明、审计和禁用外部自动操作降低风险，但不将这些措施描述为硬隔离。

- Observer 不获得 GitHub 写凭据。
- Observer 不运行源码修改、合并、发布或部署命令。
- Reporter 不能直接创建 Incident 或 Maintenance Case。
- Maintainer 和 Reviewer Agent 保持停用或不接收维护任务。
- Autopilot 不能自动批准维护。
- 发布和部署继续由现有人工流程执行。
- 所有 Observer 写操作必须能够从审计记录中追踪。

发现越权行为时立即暂停 Observer Autopilot，并按现有普通开发流程修复。

## 15. 测试与验收场景

### 自动化测试

- 项目登记、Issue 来源和孤立记录识别。
- Reporter 脱敏、Observation Inbox 去重、来源 pending 写入、失败恢复和 bounded retry。
- 增量分页、十分钟重叠、游标提交和 lease 接管。
- 全量扫描与增量扫描结果一致性。
- Finding 规则、Incident 去重、证据去重和 recurrence。
- 分类路由和维护决策摘要绑定。
- Observation、Incident 和 Maintenance Case 记录校验及非法状态拒绝。
- `APPROVE`、`DEFER` 身份、digest、原因和复核时间校验。
- 兼容命令别名与新命令产生相同的运行态结果。
- 最小 Maintenance Case 创建与范围校验。
- Observer 验证成功、失败和等待部署路径。
- 外部健康检查对调度停止和游标停滞的检测。

### 端到端验收

1. 开发 Agent 上报一个工作流缺陷，Observer 创建唯一 Incident。
2. 两个 Agent 上报同一缺陷，证据合并且不产生重复 Incident。
3. Reporter 在来源 metadata 写入失败后，Observer 仍能从 Observation Inbox 恢复并完成来源链接。
4. 人工批准维护后，仅创建一个最小 Maintenance Case。
5. 普通开发流程完成修复后，Observer 独立验证并关闭 Incident。
6. 未部署的修复不能被标记为验证成功。
7. 健康巡检不创建 Incident，只记录扫描成功和进度。
8. Observer 调度停止后，在 135 分钟新鲜度 SLO 到期后的下一个 15 分钟健康检查周期内发现异常。
9. 未登记项目的普通业务 Issue 不被主动扫描。
10. Observer 无法自动启动维护或执行源码、发布和部署变更。

## 16. Canary 与退出标准

第一阶段先部署到独立 Workflow Canary 工作区：

1. 登记一个 Canary 开发项目。
2. 手动执行增量和全量扫描，验证范围与隐私边界。
3. 启用开发 Agent Reporter。
4. 注入已知异常、重复报告、pending 失败和孤立记录。
5. 验证人工维护决策和普通开发修复交接。
6. 完成 Observer 独立验证。
7. 启用低成本定时巡检并持续观察。

Canary 使用新的工作流实例和干净的项目登记基线。现有历史 Incident 和维护树不自动导入第一阶段验收；需要保留的历史问题通过单独迁移清单关联，避免旧数据触发告警风暴。

进入第二阶段前必须满足：

- 主动上报和巡检 finding 能稳定汇聚到唯一 Incident。
- 扫描重试不遗漏、不重复创建 Incident。
- 游标、lease 和 pending 恢复通过故障注入。
- Observer 无高风险误报或未受控写操作。
- 健康检查能够发现 Observer 自身停止。
- 至少一个真实维护事件完成从上报到验证关闭的全流程。
- 定时巡检连续运行七天，无重复 Incident、游标停滞或未恢复上报。

回退时暂停 Observer Autopilot 和 Reporter 唤醒能力，保留项目登记、Incident、Maintenance Case、游标和审计证据，等待修复后重新启用。

## 17. 后续阶段接口

第一阶段向第二阶段提供：

- 已确认且获得人工维护批准的 Incident。
- 最小 Maintenance Case 及其批准范围。
- Observer 验证规则和复现场景。
- 项目登记、来源标记、去重、游标和健康监控能力。

第二阶段用 Maintainer 和 Reviewer 替换普通开发流程的临时代行角色，但不改变 Observer 对 Incident 和最终验证的所有权。

## 18. 版本记录

| 版本 | 日期 | 变更摘要 |
|---|---|---|
| `0.1.0` | 2026-07-21 | 定义第一阶段的上报、巡检、去重、游标、Incident、人工维护决策、普通开发交接和 Observer 验证闭环。 |
| `0.2.0` | 2026-07-21 | 增加权威 Observation Inbox，统一多项目游标与全局 lease，补充维护批准协议、阶段状态、兼容迁移和可执行健康 SLO。 |
| `0.3.0` | 2026-07-21 | 明确命令接口、Finding 与语义分类边界，以及 Incident 的批准、延期、路由、验证和关闭状态。 |
| `0.4.0` | 2026-07-21 | 补充 Observation、Incident、Maintenance Case 运行态契约、证据边界和延期决策格式。 |
| `0.4.1` | 2026-07-21 | 明确 Observation fingerprint 与 Incident 聚合关系，并删除重复的兼容迁移说明。 |
| `0.4.2` | 2026-07-21 | 进入实现阶段，补充项目登记和工作流 Issue 来源绑定命令。 |
