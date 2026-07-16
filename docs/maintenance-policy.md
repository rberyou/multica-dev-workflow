# Workflow Maintenance Policy

## Source of Truth

Git is desired state. Multica Agents, Skills, Project, Autopilot and Squad are deployed runtime state. Never repair the product by directly editing managed runtime objects.

## Intake

Workflow defects enter through a standardized Incident in the managed `工作流运维` Project. The Observer deduplicates and classifies reports. Only confirmed workflow defects or human-approved enhancements enter Maintenance Change.

## Change Gates

1. Versioned Change Plan.
2. Independent Maintenance Review comment bound to Plan revision and the full PR head SHA, authored by the managed Reviewer Agent.
3. Human decision for behavior, compatibility, security, privacy, cost or irreversible change.
4. Green cross-platform CI.
5. RC and isolated canary for protocol/control-plane changes.
6. Observer verification and rollback rehearsal.
7. Digest-bound release approval comment authored by the durable human approver on the Maintenance Issue.
8. Separate digest-bound deployment approval per Workspace.

Any desired Agent, instruction, Runtime profile, Skill, Squad, Project or Autopilot change must regenerate `skills/multica-workflow-observer/references/control-plane-contract.json`. CI rejects a stale contract.

## Versioning

- Patch: backward-compatible defect correction.
- Minor: backward-compatible capability or protocol extension.
- Major: incompatible workflow contract.
- RC: required for protocol, reconciler, release-control or runtime-control-plane changes.

## Residual Controls

Agent role boundaries are governance, not shell RBAC. The private repository currently lacks protected-branch enforcement, so released tags additionally require exact merged-PR and green-CI provenance. Owner bypass remains auditable but not impossible.
