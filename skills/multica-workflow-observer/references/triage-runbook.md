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

Observer automation creates or reuses only the Incident. A confirmed workflow defect records `waiting_on=maintenance_intake` and requests a durable human or explicitly authorized external Maintainer to choose an existing batch or start maintenance.

Observer must not create Maintenance Change, Plan, Implementation, Canary, or Rollout Issues. Maintenance stages are created lazily by the Maintainer after the preceding gate. During a stabilization freeze, no maintenance-tree expansion or status promotion is allowed.

Product defects are routed to the product Project. Multica product defects require a redacted draft and human approval before external publication.

An Incident closes only after the fixed release is deployed to the affected Workspace and Observer verification passes. Observer resume is a separate human-approved operation.
