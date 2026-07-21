工作流协议版本：v3。顶层需求 metadata 缺少 protocol_revision 时按 v2 核心语义处理；子 issue 缺失时继承顶层值，显式冲突必须 blocked 并上报。

每次行动前读取当前 issue、完整父 issue 链、最新评论、验收条件、metadata、子 issue stage 和当前 plan_revision。

不得绕过 Plan Review、人工 Plan 审批、代码 Review、集成验证或最终人工审批。

Review Loop 默认保持 original owner 为 assignee，不通过反复更换 assignee 路由。交接使用有效完整 mention，并依据 metadata 中的 original_owner_id、reviewer_id、integrator_id 和 human_approver_id，不得依据显示名或记忆猜测身份。

创建 issue 后必须立即运行 `multica-workflow-observer` Skill 随附的 `observer.py bind-workflow-issue`，从当前项目的 Project Registration 写入 `managed_by`、`workflow_instance_id`、`workflow_object_type`、`root_requirement_id`、`created_by_role` 和协议版本；随后写入 workflow_stage。创建可审查 issue 时写入适用的 durable metadata：original_owner_id、reviewer_id、integrator_id、human_approver_id、plan_revision，并继承顶层 workflow_id、workflow_version、protocol_revision，同时写入 top_protocol_revision 作为继承证据。不得在子 issue 写入与顶层冲突的协议值。development_task 和 integration_validation 还必须维护 dependencies_satisfied；进入代码审查前写入 pr_head_sha 和 reviewed_commit_sha。

发现工作流规则冲突、门禁失效、平台能力与指令假设不一致、必需角色/Runtime/Skill/metadata 缺失、重复或孤立 issue、错误状态流转时，不得静默绕过。调用 multica-workflow-observer 的 Reporter Mode 创建或复用 Observation；只有 Observer 可以把 Observation 转换为去重 Incident。只有影响正确性、审批完整性、安全、隐私或 Git 历史时才阻塞当前 issue；低风险效率/文档问题可继续但仍须上报。普通代码缺陷、业务需求不清和已由 Plan revision 正常处理的问题不属于工作流 Incident。

人工批准只有同时满足以下条件才有效：评论 author_type=member；author_id 精确等于 human_approver_id；评论包含当前版本的 APPROVE PLAN vN、DECISION: ... 或 APPROVE REQUIREMENT vN。Squad roster role 只用于发现审批人和生成有效 mention，不是审批凭证。

接受有效人工批准后记录 approval_author_type、approval_author_id、approval_comment_id、approval_revision；无效评论不得写入这些字段。

Stage 终态只表示平台屏障关闭，不自动表示业务依赖满足。父负责人被唤醒后仍需核验审批、dependency_contract、测试和验收条件，并手动完成父 issue。

非决策性问题由原作者修复，再由独立审查员重新审查。审查员不得审查自己修改的方案或代码。

决策性问题包括：用户可见行为或验收标准变化、公共接口或数据格式变化、不可逆迁移、安全或隐私风险、重大兼容性取舍、成本或性能取舍。遇到决策性问题设为 blocked，记录 blocked_reason 和 waiting_on，并请求人工审批人。

每次交接评论包含：结果状态、Plan 版本、分支/commit/PR、测试、剩余风险或阻塞原因。

统一失败规则：无法继续时记录 blocked_reason/waiting_on，不得 done；Runtime 离线时 waiting_on=runtime，不静默换 Agent；同因连续失败两次时 blocked 并交阶段负责人；不清理、stash、reset、覆盖或提交来源不明的用户改动；任何代码变化、冲突解决或相关分支同步都会使旧 Review Approval 失效。

不得将 issue 标记为 done，除非当前阶段的验收、测试、Review、审批和依赖合同均已满足。
