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

- the repository default branch and `default_base_sha`;
- the Plan-defined target branch and `target_base_sha`, which may be non-default;
- the Requirement branch and `reviewed_commit_sha`;
- the delivery-policy digest, Plan revision, acceptance results, tests, reviewer, and Review comment;
- Requirement PR/CI evidence only when enabled.

After integration validation, Integrator completes the Implementation as `done`. Leader then moves only the top-level Requirement to `in_review` and opens the revision/head/policy-bound final approval gate. The existing human command remains `APPROVE REQUIREMENT vN`, but it is valid only on that top-level Requirement while the gate is open. When accepting that comment, Integrator records `approval_comment_id`, `approval_revision`, `approved_requirement_head_sha`, and the approved policy digest from the already-reviewed integration evidence. The human does not manually type a SHA. A child or early approval writes no approval metadata.

Before merge, the Integrator requires the current Requirement head to equal both `reviewed_commit_sha` and `approved_requirement_head_sha`. If the recorded default or target baseline changed, synchronize the Requirement branch and repeat integration tests, Code Review, gate opening, and human approval.

With Requirement PR, merge through the reviewed PR into `target_branch` and collect its merge commit as canonical delivery input. Without Requirement PR, merge locally with the Plan-defined method, collect `merge_method` and `merged_commit_sha`, then push `target_branch` only when a remote exists and the project explicitly permits direct push. Without a remote, perform no push, PR, or remote-CI operation. These fields are persisted inside `delivery_evidence_record`, not expanded as separate top-level metadata keys.

Integrator records delivery on the top-level Requirement as the validator-returned scalar `delivery_evidence_record` and does not change its status. The compact versioned record avoids the platform's 50-key metadata limit while retaining the complete merge, branch, PR/remote, tree, revision, head, and policy evidence. Integrator posts a delivery-complete comment there, mentions Leader or Squad, verifies `trigger_outcomes` is `queued`, `coalesced`, or `deferred`, and stores the returned scalar `delivery_handoff_record`. Leader alone performs the final `done` transition after both records match current evidence. Repeated approval never repeats a merge; if delivery is complete and the root is still non-terminal, it triggers a new Leader handoff.
