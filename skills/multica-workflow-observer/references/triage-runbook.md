# Triage Runbook

Allowed verdicts:

- `CONFIRMED_WORKFLOW_BUG`
- `WORKFLOW_GAP`
- `USAGE_ERROR`
- `PROJECT_DEFECT`
- `RUNTIME_INCIDENT`
- `MULTICA_PRODUCT_DEFECT`
- `FALSE_POSITIVE`
- `DECISION_REQUIRED`

Confirmed workflow defects create one Maintenance Change child assigned to the managed Workflow Maintainer. Reuse the existing Maintenance tree for an unresolved same-release recurrence. Product defects are routed to the product Project. Multica product defects require a redacted draft and human approval before external publication.

An Incident closes only after the fix release is deployed to the affected Workspace and Observer verification passes.
