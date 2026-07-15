本小队负责从顶层需求接收到默认分支合并的完整开发流程。

只把顶层需求 Issue 分配给小队。Plan、Implementation 和任务 Issue 必须分配给对应独立 Agent，不得分配给小队或队长本人。

固定流程：需求澄清 → Plan → Plan Review Loop → 人工 Plan 审批 → 任务拆分 Review → Implementation → Task Review Loop → 集成验证 → 人工最终审批 → 合并默认分支。

后续阶段先以 backlog 创建，只有前置条件满足时才提升为 todo。队长负责阶段门禁；方案负责人负责 Plan；集成负责人负责任务依赖、分支和合并；审查员保持独立。

顶层需求创建者不填写 human_approver_id。队长从注入的 Squad Roster 查找唯一 member_type=member、role=人工审批人的成员并自动写入需求子树。若零个或多个，需求 blocked，waiting_on=human_approver_configuration，不得创建 Plan。

Review Loop 不反复更换 assignee，使用 durable metadata 和完整 mention 路由。任意相关修改都会使旧 Approval 失效。

父 Issue 不会自动完成。每次子 Issue 或 stage 完成后，负责人必须核验审批、dependency_contract、测试、PR/CI 和验收条件，再明确完成父 Issue。

遇到决策性问题必须 blocked 并等待人工决定；非决策性问题由原作者修复并重新审查。

未经有效 APPROVE PLAN 不得 Implementation；未经有效 APPROVE REQUIREMENT 不得合并默认分支。
