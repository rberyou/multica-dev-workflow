---
name: multica-workflow-incidents
description: Bind development workflow Issues to protocol v4, persist workflow Incidents, and create, link, or verify external Incident fix Requirements. Use when a managed development Agent creates a workflow Issue, finds a durable workflow rule conflict or platform mismatch, needs an explicit external incident fix record, or closes either an external or legacy fix after deployment verification.
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

## Create or Link the Fix

`report` creates or reuses only an Incident. When a durable fix is needed, create one external fix Requirement in an explicitly selected external Project:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> create-fix-requirement \
  --incident <incident-id> --project <external-project-id-or-ref> \
  [--assignee-id <external-owner-id>]
```

The Project is mandatory. The command rejects the managed Incident Project, other managed workflow Projects, and any assignee in the managed development Squad. Without `--assignee-id`, the new `backlog` object remains unassigned. It is an external `incident_fix_requirement`, not a protocol-v4 development-tree Requirement, and it does not enter Plan, implementation, Review, integration, or final approval.

Use `link-fix` to recover or explicitly associate an external object, or to retain a previously existing ordinary protocol-v4 Requirement link:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> link-fix \
  --incident <incident-id> --requirement <requirement-id>
```

## Close

After the external fix Requirement is `done`, close with redacted evidence, a typed immutable fix reference, and a typed deployment verification reference:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> close \
  --incident <incident-id> --result passed --evidence <verification-evidence> \
  --fix-reference-type <git_commit|artifact_version|other-stable-type> \
  --fix-reference <immutable-reference> \
  --deployment-verification-reference-type <stable-type> \
  --deployment-verification-reference <immutable-reference>
```
Legacy ordinary Requirement links keep the existing source commit and deployment Plan digest gate:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> close \
  --incident <incident-id> --result passed --evidence <verification-evidence> \
  --source-commit <commit> --deployment-plan-digest <digest>
```
If and only if an already linked legacy ordinary Requirement was explicitly `cancelled`, the Incident is still `in_fix`, and the correction was completed independently, use the narrow closure mode below. It preserves the original `fix_requirement_id` as audit evidence and requires the same typed immutable identity bindings as an external close:

```text
python <this-skill>/scripts/incidents.py --workspace <workspace> close \
  --incident <incident-id> --result passed --closure-mode independent_remediation \
  --evidence <redacted-verification-evidence> \
  --fix-reference-type <stable-type> --fix-reference <immutable-reference> \
  --deployment-verification-reference-type <stable-type> \
  --deployment-verification-reference <immutable-reference>
```

This mode is not fix replacement or supersession. It is rejected for active, done, external, or unlinked fixes and never restores anything except source relationships still owned by the Incident.

A failed check keeps the Incident open with mode-appropriate `waiting_on`. Never copy credentials, cookies, private keys, authorization headers, or raw environment values into Incident evidence or identity references.

When operating from the workflow repository, the equivalent host wrappers are `workflow.py bind-workflow-issue`, `report-incident`, `create-incident-fix-requirement`, `link-incident-fix`, and `close-incident`. Managed Agents should use this Skill's direct script commands shown above because product repositories do not contain `scripts/workflow.py`.

Read [incident-contract.md](references/incident-contract.md) when validating exact metadata, deduplication, blocking, or closure behavior.
