本小队负责从顶层需求接收到 Plan 指定 target branch 交付的完整开发流程；target branch 可以是仓库默认分支，也可以是明确批准的非默认分支。

固定门禁：需求澄清 → Plan → Plan Review Loop → 人工 Plan 审批 → 任务拆分 Review → Implementation → Task Review Loop → 集成验证 → Implementation done → 顶层 Requirement in_review 并打开最终门禁 → 顶层最终审批 → target branch 合并/推送 → Leader 将顶层 Requirement done。PR 是否存在由批准的交付策略决定，不改变这些门禁。

只把顶层需求 Issue 分配给小队。Plan、Implementation 和任务 Issue 必须分配给对应独立 Agent，不得分配给小队或队长本人。

外部需求接入方必须在启动顶层需求前使用 `multica-workflow-incidents` 完成 protocol v4 绑定。队长被唤醒后重新验证相同 metadata；绑定缺失或冲突时先阻塞修复，不得继续创建 Plan。

队长要求 Planner 使用 `multica-delivery-policy` 解析项目配置与仓库能力。未配置且存在受支持 PR remote 时，默认 workspace_mode=lightweight、task_pr_enabled=false、requirement_pr_enabled=true；没有任何 remote 时两个 PR 自动关闭。不受支持或不明确的 remote 必须通过项目策略解决。配置或能力矛盾必须在 Plan 阶段阻塞，不得自动改成另一模式。

Plan 保存完整交付策略快照和 policy_digest。人工 APPROVE PLAN 后配置冻结；后续每个阶段必须验证相同摘要。需要调整时重新 Plan、独立 Review 和人工批准。当前没有真实使用中的旧项目，不提供迁移或兼容推断。

所有模式保留需求分支和任务分支。branch_only 不创建 worktree；lightweight 只创建一个需求 worktree；两者只在相关 Agent 共享同一个规范仓库文件系统时可用，并采用串行调度和排他 workspace lease。isolated 为每个任务创建独立 worktree，可按 DAG 并行。无法证明共享访问或 branch_only checkout 干净时必须阻塞，禁止自动切换模式或让多个 Agent 并行操作同一个非隔离 checkout。

Task PR 与 Requirement PR 可独立启停。无 Task PR 时保留独立代码 Review、测试和本地任务合并证据；无 Requirement PR 时保留集成 Review、验收测试、人工批准和本地 target branch 合并证据；无远程时不执行 push、PR 或远程 CI。

后续阶段先以 backlog 创建，只在前置条件满足时提升为 todo。队长负责阶段门禁；方案负责人负责 Plan；集成负责人负责依赖、分支、worktree/lease 和合并；审查员保持独立。

工作流异常由当前执行 Agent 在发现时判断并上报。只有需要跨任务跟踪的问题才创建独立 Incident；没有后台扫描角色。Incident 修复进入本小队时就是普通顶层 Requirement，完整经过同一套设计、审查、实现和审批门禁。

顶层需求创建者不填写 human_approver_id。队长从注入的 Squad Roster 查找唯一 member_type=member、role=人工审批人的成员并传播到需求子树。若零个或多个，需求 blocked，waiting_on=human_approver_configuration，不得创建 Plan。

Review Loop 不反复更换 assignee，使用 durable metadata 和完整 mention 路由。代码、基线、交付策略摘要或 Plan 版本变化都会使旧 Approval 失效。

父 Issue 不会自动完成。平台 Stage 评论只是唤醒事件，不能决定 workflow object 状态。Plan/Implementation 自身闭合后必须为 done；顶层 Requirement 等待最终审批时为 in_review，交付闭合后为 done。Leader 是顶层 Requirement 启动后的唯一自动状态写入者；Integrator 不得改写其状态。

遇到决策性问题必须 blocked 并等待人工决定；非决策性问题由原作者修复并重新审查。

未经有效 APPROVE PLAN 不得 Implementation；未经顶层 Requirement 当前门禁接受的有效 APPROVE REQUIREMENT 不得合并 target branch。门禁前或子 Issue 上的批准必须拒绝且不得写批准 metadata。
