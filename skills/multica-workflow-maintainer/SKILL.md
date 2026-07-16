---
name: multica-workflow-maintainer
description: Maintain the Git-managed Multica workflow product from a confirmed Incident or human-approved enhancement. Use for diagnosis, versioned change Plans, implementation branches, independent review, tests, release candidates, canary execution, stable releases, rollout, and rollback. Reviewer Mode audits changes without editing them.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 1.1.0-rc.2
---

# Multica Workflow Maintainer

Use Maintainer Mode only as the managed Workflow Maintainer or an explicitly authorized external recovery Agent. Use Reviewer Mode only as the independent Maintenance Reviewer.

## Intake Gate

Accept a confirmed Workflow Incident or an explicit human enhancement request. Read the Incident, source Issue, release/protocol, reproduction, severity and expected acceptance scenarios.

## Change Flow

1. Create a versioned Change Plan covering root cause, impact, compatibility, tests, canary, rollout and rollback.
2. Run an independent Plan Review Loop. Decision-bearing findings go to the durable human approver.
3. Implement on a non-main branch and open a PR.
4. Bind independent Review to the current PR head SHA. Record the observed head as `pr_head_sha`, the approved head as `reviewed_commit_sha`, and the Review comment ID. Any change invalidates Review.
5. Regenerate the Observer desired-state contract with `python scripts/generate_audit_contract.py`, then run its `--check` mode, schema, compile, unit/integration, Skill packaging, portability and secret checks.
6. Prepare an RC and a digest-bound canary execution Plan.
7. Require Observer verification and rollback rehearsal before stable release.
8. Record the merged PR number/SHA on the Maintenance Issue and prepare a digest-bound release Plan. Require the durable human approver to comment the exact `APPROVE WORKFLOW RELEASE <digest>` on that Issue, run the read-only `release.py approval-block`, and require the fixed GitHub approver to post its exact output on the merged PR before tagging.
9. Generate a separate deployment Plan per Workspace; do not Apply without `APPROVE WORKFLOW PLAN <digest>`.

Read [maintenance-policy.md](references/maintenance-policy.md), [release-runbook.md](references/release-runbook.md), and [rollback-runbook.md](references/rollback-runbook.md).

## Reviewer Mode

Do not edit reviewed content. Verify the durable reviewer Agent ID differs from the Maintainer, bind findings to Plan revision and the full PR head SHA, and output only `APPROVED`, `CHANGES_REQUESTED`, or `DECISION_REQUIRED`. An approval comment starts with a standalone `APPROVED` line so release tooling can verify its author and binding.
