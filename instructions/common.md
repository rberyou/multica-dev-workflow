工作流协议版本：v4。所有工作流 Issue 必须显式绑定 v4；缺失或冲突的协议 metadata 不做旧版本推断，直接阻塞并修复当前对象。

每次行动前读取当前 issue、完整父 issue 链、最新评论、验收条件、metadata、子 issue stage、当前 plan_revision、delivery_policy_digest 和本次有效交付配置。

不得绕过 Plan Review、人工 Plan 审批、代码 Review、集成验证或最终人工审批。Task PR 和 Requirement PR 是可选交付机制，不是 Review 或审批门禁的替代品。

Review Loop 默认保持 original owner 为 assignee，不通过反复更换 assignee 路由。交接使用有效完整 mention，并依据 metadata 中的 original_owner_id、reviewer_id、integrator_id 和 human_approver_id，不得依据显示名或记忆猜测身份。

创建工作流 Issue 后必须立即使用已附加的 `multica-workflow-incidents` Skill 执行直接命令 `bind-workflow-issue`，写入 managed_by、workflow_instance_id、workflow_object_type、root_requirement_id、created_by_role、workflow_version、protocol_revision 和 top_protocol_revision；随后写入 workflow_stage。不要假设当前产品仓库包含工作流仓库的 `scripts/workflow.py`。子 issue 的协议和 root_requirement_id 必须与父链一致。development_task 和 integration_validation 还必须维护 dependencies_satisfied。

Plan 必须使用已附加的 `multica-delivery-policy` Skill 解析项目根目录的 `multica.delivery.json`、Git remote 和本次选择。完整策略快照及 policy_digest 写入 Plan；workspace_mode、task_pr_enabled、requirement_pr_enabled、delivery_policy_digest 和 plan_revision 传播到 Implementation、任务、Review、验证、批准和合并证据。没有项目配置时直接使用当前协议默认值，不迁移或推断旧项目行为。

Plan 批准后交付配置冻结。每次 Implementation 启动、任务开始、Review、合并和最终批准前重新验证 policy_digest。任何项目策略、选定 remote、provider、能力或实际选择变化都必须重新打开 Plan、递增 plan_revision、重新独立 Review 并取得新的 APPROVE PLAN；不得由 Agent 自动升级、降级或原地切换模式。

所有模式都保留需求分支和任务分支。branch_only 使用现有 checkout 串行工作；lightweight 使用一个需求 worktree 串行切换任务分支；isolated 使用需求 worktree和每任务独立 worktree，可按依赖 DAG 并行。branch_only 和 lightweight 在任一时刻只能由一个 Developer、Code Reviewer 或 Integrator 持有 workspace lease。

进入本地或 PR Review 前记录 base_commit_sha 和 reviewed_commit_sha。Task PR 启用时还记录 pr_head_sha、PR 和 CI；未启用时直接审查两个不可变 SHA 的 Git diff。任务合并后记录 merge_method 和 merged_commit_sha。集成验证记录 default_base_sha 和需求 reviewed_commit_sha；接受最终人工批准时自动把已审查的需求 head 写入 approved_requirement_head_sha。用户仍只需评论 `APPROVE REQUIREMENT vN`。

发现工作流规则冲突、门禁失效、平台能力与指令假设不一致、必需角色、Runtime、Skill 或 metadata 缺失、重复或孤立 issue、错误状态流转时，先判断能否在当前任务内立即、安全、完整地修复。只有问题需要跨任务保留、可能复发、需要其他负责人或人工决定、阻塞正确性，或需要部署后验证时，才使用 Incident Skill 的直接命令 `report` 创建或复用持久 Incident。普通代码缺陷、需求澄清和当前任务内已经修复的问题不创建 Incident。

只有继续执行会危及正确性、审批完整性、安全、隐私或 Git 历史时，才使用 `--block-source`。Incident 修复必须创建普通 Requirement，并用直接命令 `link-fix` 关联；不存在 Maintenance Case 或专用维护角色。修复部署并验证后，用直接命令 `close` 记录结果。不得把凭据、Cookie、私钥、Authorization header 或原始环境变量写入 Incident。

人工批准只有同时满足以下条件才有效：评论 author_type=member；author_id 精确等于 human_approver_id；评论包含当前版本的 APPROVE PLAN vN、DECISION: ... 或 APPROVE REQUIREMENT vN。Squad roster role 只用于发现审批人和生成有效 mention，不是审批凭证。

接受有效人工批准后记录 approval_author_type、approval_author_id、approval_comment_id 和 approval_revision。最终批准还必须从当前已通过集成 Review 的证据写入 approved_requirement_head_sha；不得让人工手工猜测或输入 SHA。无效或过期评论不得写入这些字段。

Stage 终态只表示平台屏障关闭，不自动表示业务依赖满足。父负责人被唤醒后仍需核验审批、dependency_contract、交付策略、workspace lease、测试、Review、可选 PR/CI 和验收条件，并手动完成父 issue。

非决策性问题由原作者修复，再由独立审查员重新审查。审查员不得审查自己修改的方案或代码。

决策性问题包括：用户可见行为或验收标准变化、公共接口或数据格式变化、不可逆迁移、安全或隐私风险、重大兼容性取舍、成本或性能取舍。遇到决策性问题设为 blocked，记录 blocked_reason 和 waiting_on，并请求人工审批人。

每次交接评论包含：结果状态、Plan 版本、交付策略摘要、分支/commit/可选 PR、测试、剩余风险或阻塞原因。

统一失败规则：无法继续时记录 blocked_reason/waiting_on，不得 done；Runtime 离线时 waiting_on=runtime，不静默换 Agent；同因连续失败两次时 blocked 并交阶段负责人；不清理、stash、reset、覆盖或提交来源不明的用户改动；任何代码变化、冲突解决、相关分支同步或 policy_digest 变化都会使旧 Review Approval 失效。

不得将 issue 标记为 done，除非当前阶段的验收、测试、Review、审批、交付证据和依赖合同均已满足。
