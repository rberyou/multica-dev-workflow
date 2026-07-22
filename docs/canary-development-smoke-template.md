# Canary Development Smoke Run Record

Use this template to record one development squad end-to-end Canary run. Keep
the record concise, auditable, and safe to share in project history.

## Purpose

- Requirement:
- Canary objective:
- Expected low-risk change:
- Out of scope:

## Workflow Trace

Record issue, stage, and handoff identifiers. Do not rely on display names as
identity proof.

| Workflow item | Identifier | Status | Evidence |
| --- | --- | --- | --- |
| Requirement |  |  |  |
| Plan |  |  |  |
| Plan review |  |  |  |
| Task split |  |  |  |
| Development task |  |  |  |
| Code/document review |  |  |  |
| Integration validation |  |  |  |
| Final approval |  |  |  |

## Plan Revision

- Plan revision:
- Approved plan revision:
- Dependency contract:
- Scope changes:
- Decision records:

If implementation cannot satisfy the approved plan, stop and request a new plan
revision instead of expanding scope in this record.

## Branch/PR/SHA

Record repository identifiers that future reviewers can verify.

| Item | Value |
| --- | --- |
| Default branch |  |
| Requirement branch |  |
| Task branch |  |
| Task PR |  |
| Requirement PR |  |
| Commit SHA |  |
| PR head SHA |  |
| Reviewed SHA |  |
| Review conclusion |  |
| CI/check status |  |

No PR targeting `main` may be merged without valid `APPROVE REQUIREMENT v1`
from `human_approver_id`.

## Validation

Record command names, exit codes, and short summaries only.

| Check | Command or method | Exit code/result | Summary |
| --- | --- | --- | --- |
| Whitespace diff check | `git diff --check` |  |  |
| File boundary check |  |  |  |
| Required section check |  |  |  |
| Safety constraint check |  |  |  |
| Optional index link check |  |  |  |
| PR target/status check |  |  |  |

Do not paste raw sensitive logs. Summarize the relevant result and include only
necessary issue, PR, branch, and SHA identifiers.

## Observer Incidents

Record Observer findings as issue identifiers and conclusions only.

| Incident | Severity | Status | Conclusion |
| --- | --- | --- | --- |
|  |  |  |  |

Do not copy sensitive incident payloads, raw daemon logs, or machine-private
runtime details into this record.

## Human Approvals

Approval evidence must be based on platform identity fields and exact phrases.
Display names are not approval credentials.

| Approval gate | Required phrase | author_type | author_id | Comment ID | Valid |
| --- | --- | --- | --- | --- | --- |
| Plan approval | `APPROVE PLAN v1` | `member` | `human_approver_id` |  |  |
| Requirement approval | `APPROVE REQUIREMENT v1` | `member` | `human_approver_id` |  |  |

Treat approval as valid only when `author_type=member`,
`author_id=human_approver_id`, and the comment contains the exact approval
phrase for the current revision.

## Rollback

- Rollback unit:
- Revert PR or commit:
- Preconditions before rollback:
- Validation after rollback:
- Residual follow-up:

For this Canary template requirement, rollback should be limited to reverting
the documentation change and any approved documentation index link.

## Safety Constraints

Do not record credentials, tokens, API keys, cookies, private keys, or other
secrets.

Do not record local absolute paths, `file://` URLs, or machine-private paths.

Do not paste raw sensitive logs. Record summaries, command names, exit codes,
and necessary issue, PR, branch, and SHA identifiers only.

Do not treat display names as approval credentials. Approval evidence must use
`author_type=member`, `author_id=human_approver_id`, and the exact approval
phrase for the current revision.
