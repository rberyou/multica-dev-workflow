---
name: multica-workflow-observer
description: Report, detect, deduplicate, triage, and verify workflow incidents in a Git-managed Multica development workflow. Use in Reporter Mode when a managed development Agent finds a workflow rule conflict, gate failure, missing role/runtime/metadata, platform-assumption mismatch, duplicate/orphan Issue, or managed drift. Use in Observer Mode only for the managed Workflow Observer Agent or its Autopilot.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 1.1.0-rc.4
---

# Multica Workflow Observer

This Skill has two modes. It never fixes workflow source, publishes a release, or applies a deployment Plan.

When a managed Agent creates a workflow Issue, immediately run `bind-workflow-issue` so the Issue inherits the enabled Project Registration, workflow instance, root requirement, object type, creator role, and protocol revision.

## Reporter Mode

Reporter Mode is the default for development Agents.

1. Confirm the problem is about workflow behavior, not ordinary product code, tests, requirements, or a Plan defect already handled by Plan revision.
2. Read the source Issue, top-level requirement, relevant metadata and latest comments.
3. Choose severity using [incident-contract.md](references/incident-contract.md).
4. Run the packaged deterministic Reporter entry point:

```text
python <this-skill>/scripts/observer.py report-anomaly \
  --source-issue <T-ID> \
  --rule-id <rule> \
  --severity <level> \
  --summary <summary> \
  --expected <expected> \
  --actual <actual>
```

The command creates or reuses a durable Observation Inbox record. It does not create an Incident directly. High and urgent reports wake the Observer after the Observation is durable. Use `--block-source` only for urgent/high correctness, approval, security, privacy or Git-history risk.

`report-incident` remains a compatibility alias for `report-anomaly`. If source metadata cannot be updated, the Observation remains authoritative and records the marker failure. Never retry without a bound.

## Observer Mode

Observer Mode is authorized only by the managed Workflow Observer instructions or its Autopilot.

- Run `scripts/observer.py scan --mode incremental` hourly and `scan --mode full` daily. Scans process pending/processing/failed Observations, inspect only enabled Project Registrations, maintain per-project cursors, and serialize each run with an expiring lease. One failed Observation is recorded without aborting the rest of the scan; after five attempts it is quarantined for operator review. `audit --scope issues --report` remains an incremental-scan compatibility alias, while `audit` without `--report` is strictly read-only.
- Use `scripts/observer.py health` from an external operator context to check Autopilot freshness.
- Triage Incidents into only the verdicts in [triage-runbook.md](references/triage-runbook.md), prepare a digest-bound maintenance decision, and record only an exact human approval or defer comment.
- The ordinary development workflow or human host records `in-development`, `fix-ready`, `release-recorded`, and `deployment-recorded` evidence through `record-maintenance-progress`. Deployment evidence must bind the release source, Workspace Plan digest, completed apply journal and its immutable plan-digest Workspace deployment record.
- After ordinary development deploys a fix, the assigned Observer Agent uses `verify-fix` to record independent evidence and close the Maintenance Case and Incident. Host calls without the assigned Observer identity are rejected.
- Do not modify workflow Git source or managed Multica configuration.
- Create or reuse only the Incident until an exact digest-bound human approval is recorded. Approval may create one minimal Maintenance Case for the ordinary development workflow; do not create Change Plan, Implementation, Canary, or Rollout child trees in Phase 1.
- Honor `maintenance_intake_mode=human_gated`, `automatic_expansion=false`, and stabilization freezes. Stage expansion and Observer resume require separate human-approved operations.
- Keep Incident evidence redacted and bounded.
- Preserve one redacted pending payload, its stable scan index, original Reporter identity and the source pending marker until Incident metadata and source linkage are complete. Reuse the stable fingerprint on retry, preserve evidence in the bounded Incident log, and apply cooldown to human-facing comments and escalation notification.

## Portability

Resolve the script relative to this Skill directory. Do not assume a workflow repository checkout, fixed current directory, profile name, workspace UUID or user path. The script uses `MULTICA_BIN`, `MULTICA_WORKSPACE_ID` and portable CLI discovery.
