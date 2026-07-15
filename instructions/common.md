工作流协议版本：v2。

每次行动前读取当前 issue、完整父 issue 链、最新评论、验收条件、metadata、子 issue stage 和当前 plan_revision。

不得绕过 Plan Review、人工 Plan 审批、代码 Review、集成验证或最终人工审批。

Review Loop 默认保持 original owner 为 assignee，不通过反复更换 assignee 路由。交接使用有效完整 mention，并依据 metadata 中的 original_owner_id、reviewer_id、integrator_id 和 human_approver_id，不得依据显示名或记忆猜测身份。

创建可审查 issue 时写入适用的 durable metadata：original_owner_id、reviewer_id、integrator_id、human_approver_id、plan_revision。

人工批准只有同时满足以下条件才有效：评论 author_type=member；author_id 精确等于 human_approver_id；评论包含当前版本的 APPROVE PLAN vN、DECISION: ... 或 APPROVE REQUIREMENT vN。Squad roster role 只用于发现审批人和生成有效 mention，不是审批凭证。

Stage 终态只表示平台屏障关闭，不自动表示业务依赖满足。父负责人被唤醒后仍需核验审批、dependency_contract、测试和验收条件，并手动完成父 issue。

非决策性问题由原作者修复，再由独立审查员重新审查。审查员不得审查自己修改的方案或代码。

决策性问题包括：用户可见行为或验收标准变化、公共接口或数据格式变化、不可逆迁移、安全或隐私风险、重大兼容性取舍、成本或性能取舍。遇到决策性问题设为 blocked，记录 blocked_reason 和 waiting_on，并请求人工审批人。

每次交接评论包含：结果状态、Plan 版本、分支/commit/PR、测试、剩余风险或阻塞原因。

统一失败规则：无法继续时记录 blocked_reason/waiting_on，不得 done；Runtime 离线时 waiting_on=runtime，不静默换 Agent；同因连续失败两次时 blocked 并交阶段负责人；不清理、stash、reset、覆盖或提交来源不明的用户改动；任何代码变化、冲突解决或相关分支同步都会使旧 Review Approval 失效。

不得将 issue 标记为 done，除非当前阶段的验收、测试、Review、审批和依赖合同均已满足。
