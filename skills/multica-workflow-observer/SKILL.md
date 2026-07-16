---
name: multica-workflow-observer
description: Report, detect, deduplicate, triage, and verify workflow incidents in a Git-managed Multica development workflow. Use in Reporter Mode when a managed development Agent finds a workflow rule conflict, gate failure, missing role/runtime/metadata, platform-assumption mismatch, duplicate/orphan Issue, or managed drift. Use in Observer Mode only for the managed Workflow Observer Agent or its Autopilot.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 1.1.0-rc.1
---

# Multica Workflow Observer

This Skill has two modes. It never fixes workflow source, publishes a release, or applies a deployment Plan.

## Reporter Mode

Reporter Mode is the default for development Agents.

1. Confirm the problem is about workflow behavior, not ordinary product code, tests, requirements, or a Plan defect already handled by Plan revision.
2. Read the source Issue, top-level requirement, relevant metadata and latest comments.
3. Choose severity using [incident-contract.md](references/incident-contract.md).
4. Run the packaged deterministic writer:

```text
python <this-skill>/scripts/observer.py report-incident \
  --source-issue <T-ID> \
  --rule-id <rule> \
  --severity <level> \
  --summary <summary> \
  --expected <expected> \
  --actual <actual>
```

Use `--block-source` only for urgent/high correctness, approval, security, privacy or Git-history risk. Do not create an Incident as a business requirement child.

If reporting fails, set `workflow_incident_pending=true` on the source Issue when possible, record the compact failure and obey the severity blocking rule. Never retry without a bound.

## Observer Mode

Observer Mode is authorized only by the managed Workflow Observer instructions or its Autopilot.

- Run `scripts/observer.py audit --scope issues --report` for scheduled detection. It scans active workflow Issues plus pending recovery payloads and validates the bundled full desired-state contract. Do not self-check scheduler freshness from the Autopilot; an external operator or scheduler runs repository `scripts/workflow.py health`.
- Use `scripts/observer.py health` from an external operator context to check Autopilot freshness.
- Triage reports into only the verdicts in [triage-runbook.md](references/triage-runbook.md).
- Do not modify workflow Git source or managed Multica configuration.
- Create exactly one Maintenance Change child for a confirmed workflow defect.
- Keep Incident evidence redacted and bounded.
- Preserve one redacted pending payload, its stable scan index, original Reporter identity and the source pending marker until Incident metadata and source linkage are complete. Reuse the stable fingerprint on retry, preserve evidence in the bounded Incident log, and apply cooldown to human-facing comments and escalation notification.

## Portability

Resolve the script relative to this Skill directory. Do not assume a workflow repository checkout, fixed current directory, profile name, workspace UUID or user path. The script uses `MULTICA_BIN`, `MULTICA_WORKSPACE_ID` and portable CLI discovery.
