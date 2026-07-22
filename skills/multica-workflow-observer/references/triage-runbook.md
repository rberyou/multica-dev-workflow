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

Observer automation consumes durable Observations and creates or reuses only one Incident per dedupe key. A confirmed workflow defect moves to `awaiting_maintenance_decision` and receives a digest-bound approval request.

The registered human approver may use `APPROVE WORKFLOW MAINTENANCE <digest>` or `DEFER WORKFLOW MAINTENANCE <digest>`. Approval creates one minimal Maintenance Case for the ordinary development workflow. Phase 1 does not create a Maintenance Change tree or activate Maintainer/Reviewer automation.

Product defects are routed to the product Project. Multica product defects require a redacted draft and human approval before external publication.

An Incident closes only after the fixed release is deployed to the affected Workspace and Observer verification passes. Observer resume is a separate human-approved operation.
