# Workflow Maintenance Policy

## Source of Truth

Git is desired state. Multica Agents, Skills, Project, Autopilot and Squad are deployed runtime state. Never repair the product by directly editing managed runtime objects.

## Intake

Workflow defects enter through a durable Observation in the managed `工作流运维` Project. The Observer classifies and deduplicates valid reports into Incidents. Only a digest-bound decision by the registered human approver may start maintenance. During Phase 1, approval creates one minimal Maintenance Case for the ordinary development workflow; Maintainer/Reviewer automation and maintenance child trees remain disabled.

## Phase 2+ Change Gates

1. Versioned Change Plan.
2. Independent Maintenance Review comment bound to Plan revision and the full PR head SHA, authored by the managed Reviewer Agent.
3. Human decision for behavior, compatibility, security, privacy, cost or irreversible change.
4. Green cross-platform CI.
5. RC and isolated canary for protocol/control-plane changes.
6. Observer verification and rollback rehearsal.
7. Digest-bound release approval comment authored by the durable human approver on the Maintenance Issue.
8. Separate digest-bound deployment approval per Workspace.

Automatic maintenance-tree expansion is disabled. A stabilization freeze blocks Issue creation, stage promotion, Release, Canary, rollout and Observer resume until each named gate is explicitly reopened.

Any desired Agent, instruction, Runtime profile, Skill, Squad, Project or Autopilot change must regenerate `skills/multica-workflow-observer/references/control-plane-contract.json`. CI rejects a stale contract.

While Review is active, the Maintenance Issue records both `reviewed_commit_sha` and the observed current `pr_head_sha`. They must match, and the Review comment timestamp must precede the PR merge timestamp.

## Versioning

- Patch: backward-compatible defect correction.
- Minor: backward-compatible capability or protocol extension.
- Major: incompatible workflow contract.
- RC: required for protocol, reconciler, release-control or runtime-control-plane changes.

## Residual Controls

Phase 1 runs with ordinary Multica runtimes and does not claim production-grade hard isolation. The Secure Agent Runtime remains a reviewed Phase 3 target. When `secure_runtime.phase=enforced`, Maintainer GitHub mutation is limited to Broker RPCs backed by a short-lived repository-scoped App token, while Reviewer and Observer are tokenless.

Release mutation runs only inside the protected `workflow-release` GitHub Environment after approval by a human reviewer whose credential is unavailable to Agent runtimes. An active `refs/tags/v*` ruleset rejects ordinary tag creation/update/deletion and grants the sole bypass to the dedicated Publisher App. The built-in workflow token stays read-only. Managed Maintainer runtimes prepare evidence and Plans but receive no Dispatcher credential. Only the human host context may validate the reviewed Dispatcher installation and dispatch a request; it still cannot create tags or Releases.

See `secure-runtime.md` and `bootstrap-rc4.md` for the threat model, Bootstrap exception, acceptance matrix and rollback.
