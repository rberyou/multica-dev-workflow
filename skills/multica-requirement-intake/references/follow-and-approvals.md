# Requirement Follow and Human Gates

## Contents

- Continuous follow
- Human identity validation
- Plan approval
- Decision response
- Final approval
- Terminal reporting

## Continuous Follow

After a confirmed submit operation starts a protocol-verified new/backlog Requirement, or reuses a protocol-verified Requirement already active, immediately follow the same top-level Issue.

1. Retain the Issue key as the active Requirement for the current conversation until the user changes it or explicitly stops tracking.
2. Read the top-level Issue, active descendants, recent comments, metadata, assignees, task runs, delivery-policy digest, workspace/PR selection, branch/commit/optional PR bindings, tests, review outcomes, and `waiting_on`/`blocked_reason` changes.
3. Prefer a host event subscription, recurring monitor, wait, or wake-up mechanism. Otherwise use bounded read-only polling with backoff. Never busy-loop.
4. Suppress routine no-change updates. Report meaningful stage transitions, blockers, approval requests, terminal states, and recovery actions.
5. When human action is required, provide the current revision, evidence summary, exact decision or approval, and what follows afterward.
6. After applying an authorized response, resume following automatically. Approval pauses monitoring; it does not complete the task.

If the host cannot remain active or wake later, disclose that before ending, retain the Issue key in the conversation, and give the exact resume command. Never claim continuous tracking without an active mechanism.

## Human Identity Validation

Before posting any approval or decision:

1. Read the top-level Requirement, relevant child Issue, latest comments, metadata, Plan revision, review outcome, gate, and assignee.
2. Reject execution when `MULTICA_AGENT_ID` or `MULTICA_TASK_ID` indicates a daemon-managed Agent identity.
3. Read the authenticated user ID, for example through `user profile get --output json`.
4. Read the current Squad roster and require exactly one `member_type=member`, `role=人工审批人` entry.
5. Require the authenticated user ID to equal that roster member ID.

If identity cannot be verified, do not post the command. Give the user the exact manual comment and target, monitor for it when possible, validate it after it appears, and resume following.

## Plan Approval

Post only after explicit user authorization for the current Plan revision and a valid independent Plan Review:

```text
APPROVE PLAN v<N>
```

Mention the current Plan owner. Reject stale revisions and unreviewed Plans.

## Decision Response

Post only after the user gives an explicit decision for the current blocker:

```text
DECISION: <decision>
```

Mention the current stage owner. Do not convert a request for explanation into a decision.

## Final Approval

Post only after explicit user authorization, completed Implementation, successful integration validation, current acceptance evidence, a valid final Requirement revision, and a reviewed Requirement head SHA:

```text
APPROVE REQUIREMENT v<N>
```

Mention the integration owner. Do not approve when validation is incomplete or the implementation no longer matches the approved Plan.

The human still posts only the command above. After validating the comment, the workflow records the already-reviewed head as `approved_requirement_head_sha`. Do not ask the human to look up or type a Git SHA. A changed Requirement head, default-branch baseline, Plan revision, or delivery-policy digest invalidates the approval.

## Terminal Reporting

On `done`, report the delivered merge commit, optional PR, delivery mode, validation evidence, acceptance results, and residual risks. On `cancelled`, report the reason and replacement link when present. On abnormal failure or actionable stall, report the owner, evidence, waiting condition, and required recovery action while continuing to follow when supported.

Never treat Issue creation, one completed child stage, an approval request, or a posted approval as the end of the Requirement task.
