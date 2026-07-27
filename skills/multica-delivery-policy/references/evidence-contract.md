# Delivery Evidence Contract

PRs are optional transport and collaboration objects. Review, tests, human approval, and merge traceability are mandatory in every mode.

## Task Review and Merge

Every development or Revert Task records:

- `delivery_policy_digest`, `workspace_mode`, and `plan_revision`;
- `base_branch` and `base_commit_sha` used for the reviewed diff;
- `head_branch` and `reviewed_commit_sha`;
- `review_comment_id`, `reviewer_id`, test command/result, and remaining risks;
- `task_pr_enabled`, plus PR URL/number/head SHA/CI only when enabled;
- `merge_method` and `merged_commit_sha` after integration into the Requirement branch.

Without Task PR, the Code Reviewer compares `base_commit_sha...reviewed_commit_sha` directly from Git. The Integrator rechecks the same immutable commits and performs the Plan-defined local merge. A changed base or head invalidates Review. The local merge record replaces PR merge evidence; it does not remove independent Review or tests.

## Requirement Review, Approval, and Merge

Integration validation records:

- the current default branch and `default_base_sha`;
- the Requirement branch and `reviewed_commit_sha`;
- the delivery-policy digest, Plan revision, acceptance results, tests, reviewer, and Review comment;
- Requirement PR/CI evidence only when enabled.

The existing human command remains `APPROVE REQUIREMENT vN`. When accepting that comment, the workflow records `approval_comment_id`, `approval_revision`, and `approved_requirement_head_sha` from the already-reviewed integration evidence. The human does not manually type a SHA.

Before merge, the Integrator requires the current Requirement head to equal both `reviewed_commit_sha` and `approved_requirement_head_sha`. If the default branch no longer equals `default_base_sha`, synchronize the Requirement branch and repeat integration tests, Code Review, and human approval.

With Requirement PR, merge through the reviewed PR and record its merge commit. Without Requirement PR, merge locally with the Plan-defined method, record `merge_method` and `merged_commit_sha`, then push the default branch only when a remote exists and the project explicitly permits direct default-branch push. Without a remote, perform no push, PR, or remote-CI operation.
