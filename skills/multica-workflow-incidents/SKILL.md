---
name: multica-workflow-incidents
description: Bind development workflow Issues to protocol v4 and persist workflow Incidents when a problem must survive the current task. Use when a managed development Agent creates a workflow Issue, finds a workflow rule conflict, missing role or metadata, invalid gate, platform mismatch, or another cross-task workflow defect.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.6
---

# Multica Workflow Incidents

This Skill is an event-driven workflow support capability. It is not an Observer role and does not run scheduled scans.

## Bind Workflow Issues

Immediately after creating a managed development Issue, bind its protocol metadata:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> bind-workflow-issue \
  --issue <issue-id> \
  --object-type <requirement|plan|design|task_split|implementation|development_task|integration_validation> \
  --created-by-role <role>
```

The command derives the root Requirement from the parent chain and rejects conflicting existing metadata.

## Report an Incident

Persist an Incident only when the problem must survive the current task: it cannot be resolved immediately, affects correctness or approval integrity, may recur, needs another task or human decision, or requires later deployment verification.

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> report \
  --source-issue <issue-id> \
  --rule-id <stable-rule-id> \
  --severity <low|medium|high|urgent> \
  --summary <summary> \
  --expected <expected-behavior> \
  --actual <actual-behavior> \
  --evidence <redacted-evidence>
```

Use `--block-source` only when continuing risks correctness, approval integrity, privacy, security, or Git history. The script creates or reuses a deduplicated Incident in the managed workflow Incidents project and assigns it to the development leader.

## Link and Close

Link an ordinary Requirement that implements the fix:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> link-fix \
  --incident <incident-id> --requirement <requirement-id>
```

After the ordinary fix Requirement is `done` and the fix is deployed and checked, record the result. Passed verification requires the full source commit and deployment Plan digest:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> close \
  --incident <incident-id> --result passed --evidence <verification-evidence> \
  --source-commit <commit> --deployment-plan-digest <digest>
```

A failed check keeps the Incident open. Never copy credentials, cookies, private keys, authorization headers, or raw environment values into Incident evidence.

When operating from the workflow repository, the equivalent host wrappers are `workflow.py bind-workflow-issue`, `report-incident`, `link-incident-fix`, and `close-incident`. Managed Agents should use this Skill's direct script commands shown above because product repositories do not contain `scripts/workflow.py`.

Read [incident-contract.md](references/incident-contract.md) when validating exact metadata, deduplication, blocking, or closure behavior.
