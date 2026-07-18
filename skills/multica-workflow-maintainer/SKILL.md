---
name: multica-workflow-maintainer
description: Maintain the Git-managed Multica workflow product from a confirmed Incident or human-approved enhancement. Use for diagnosis, versioned change Plans, implementation branches, independent review, tests, release candidates, canary execution, stable releases, rollout, and rollback. Reviewer Mode audits changes without editing them.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 1.1.0-rc.4
---

# Multica Workflow Maintainer

Use Maintainer Mode only as the managed Workflow Maintainer or an explicitly authorized external recovery Agent. Use Reviewer Mode only as the independent Maintenance Reviewer.

## Intake Gate

Accept a confirmed Workflow Incident or an explicit human enhancement request. Read the Incident, source Issue, release/protocol, reproduction, severity and expected acceptance scenarios.

## Change Flow

1. From a human-accepted maintenance intake, create a versioned Change Plan covering root cause, impact, compatibility, tests, canary, rollout and rollback. Do not pre-create later stage Issues.
2. Run an independent Plan Review Loop. Decision-bearing findings go to the durable human approver.
3. Implement on a non-main branch and open a PR.
4. Bind independent Review to the current PR head SHA. Record the observed head as `pr_head_sha`, the approved head as `reviewed_commit_sha`, and the Review comment ID. Any change invalidates Review.
5. Regenerate the Observer desired-state contract with `python scripts/generate_audit_contract.py`, then run its `--check` mode, schema, compile, unit/integration, Skill packaging, portability and secret checks.
6. Implementation, PR, CI and commit-bound Review may finish while the repository remains private and the current development credential is available. Before repository visibility, Environment, Ruleset, Release or tag mutation, remove owner/admin `gh` credentials, owner-capable SSH and reusable GitHub HTTPS credential-helper entries from every Agent/release-capable runtime. The human owner performs visibility and control administration outside Agent runtimes, then the Maintainer runs read-only `doctor` verification.
7. After the approved implementation is merged and the external GitHub controls verify, prepare one RC release request. Local tooling may dispatch the request but cannot create or push tags or publish Releases.
8. Require Observer verification and rollback rehearsal before stable release.
9. Record the merged PR number/SHA on the Maintenance Issue and prepare a digest-bound release Plan. Require the durable human approver to comment the exact `APPROVE WORKFLOW RELEASE <digest>` on that Issue. Run the read-only `release.py approval-block`, then dispatch the bounded request with `release.py apply`. Stop at the protected `workflow-release` Environment; only its isolated human reviewer may approve and only `github-actions[bot]` may create the tag and Release.
10. Generate a separate deployment Plan per Workspace; do not Apply without `APPROVE WORKFLOW PLAN <digest>`.

Never post a human approval, switch to or expose an approver credential, call a deployment-approval API, or continue merely because generated text contains an approval phrase.

Read [maintenance-policy.md](references/maintenance-policy.md), [release-runbook.md](references/release-runbook.md), and [rollback-runbook.md](references/rollback-runbook.md).

## Reviewer Mode

Do not edit reviewed content. Verify the durable reviewer Agent ID differs from the Maintainer, bind findings to Plan revision and the full PR head SHA, and output only `APPROVED`, `CHANGES_REQUESTED`, or `DECISION_REQUIRED`. An approval comment starts with a standalone `APPROVED` line so release tooling can verify its author and binding.
