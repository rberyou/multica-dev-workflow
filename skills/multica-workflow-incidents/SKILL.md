---
name: multica-workflow-incidents
description: Bind protocol-v4 development Issues, persist workflow-only Incidents, administratively retract misclassified non-workflow records, and create, link, or verify external Incident fix Requirements. Use when a managed Issue needs protocol metadata, a durable workflow rule or integrity defect is found, an Incident was incorrectly created for a Runtime or environment failure, or an external or legacy fix must be managed.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.8
---

# Multica Workflow Incidents

Use this event-driven capability only during real work. Do not create an Observer, scheduled scan, or background patrol.

## Bind Workflow Issues

Immediately after creating an ordinary managed development Issue, bind its protocol metadata:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> bind-workflow-issue \
  --issue <issue-id> \
  --object-type <requirement|plan|design|task_split|implementation|development_task|integration_validation> \
  --created-by-role <role>
```

The command derives the root Requirement and rejects conflicting metadata. Do not bind Incidents or external `incident_fix_requirement` records.

## Classify Before Reporting

Report only a defect owned by this workflow: rule or protocol conflict, state-machine or metadata-integrity error, approval or Review gate failure, workflow source/deployment drift, or a missing managed Agent, Skill, or configuration.

Do not report OS, Codex/model Runtime, Multica daemon, network, Shell, sandbox, external-tool, or host-environment failures. Keep the original Issue fail-closed with `status=blocked`, a precise `waiting_on=runtime|environment|platform`, and concise redacted evidence. Repair and smoke-test it in place; do not replace the task or migrate its lease.

## Report a Workflow Incident

Persist a workflow Incident only when it must outlive the current task:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> report \
  --source-issue <issue-id> --condition-class workflow \
  --rule-id <stable-rule-id> --severity <low|medium|high|urgent> \
  --summary <summary> --expected <expected> --actual <actual> \
  --evidence <redacted-evidence>
```

The explicit condition class is mandatory and non-workflow classes fail before any write. Use `--block-source` only when continuing risks correctness, approval integrity, privacy, security, or Git history. `report` creates or reuses only the Incident; it never creates a fix Requirement.

## Create or Link a Fix

Create one fix record only in an explicitly selected external Project:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> create-fix-requirement \
  --incident <incident-id> --project <external-project> [--assignee-id <external-owner>]
python <this-skill>/scripts/incidents.py --workspace <workspace> link-fix \
  --incident <incident-id> --requirement <requirement-id>
```

The external record stays outside the managed development Squad and protocol-v4 development tree. `link-fix` also preserves compatibility with an already linked ordinary Requirement.

## Retract a Misclassified Record

If no fix Requirement exists and the Incident actually describes a non-workflow condition, retract it administratively:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> retract \
  --incident <incident-id> \
  --reason <misclassified_non_workflow_runtime_condition|misclassified_non_workflow_environment_condition|misclassified_non_workflow_platform_condition> \
  --evidence <redacted-classification-evidence>
```

Retraction is idempotent, preserves the Incident audit, cancels and closes it as `not_applicable`, clears only source relations still owned by that Incident, and leaves an affected source blocked on the mapped non-workflow condition. It never invents fix or deployment references.

## Close a Verified Fix

Close an external fix only after it is `done`, deployed, and verified, using non-empty redacted evidence plus typed immutable fix and deployment-verification references. Standard legacy ordinary fixes retain the full source-commit and deployment-Plan-digest gate. Use `--closure-mode independent_remediation` only for the documented cancelled legacy-fix exception.

Never persist credentials, cookies, private keys, authorization headers, raw environment values, or mutable identity references.

Repository host wrappers are `workflow.py bind-workflow-issue`, `report-incident`, `create-incident-fix-requirement`, `link-incident-fix`, `retract-incident`, and `close-incident`. Managed Agents use this Skill's direct commands because product repositories do not contain the workflow repository.

Read [incident-contract.md](references/incident-contract.md) when validating metadata, deduplication, blocking, retraction, or closure behavior.
