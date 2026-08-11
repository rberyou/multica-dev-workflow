# Changelog

## Unreleased

## 2.0.0-dev.7

- Enforce one active Integrator and one independent Code Reviewer for integration validation while preserving the Integrator as assignee and original owner.
- Add zero-write integration Review prepare/start/handoff/approve/recover validation with confirmed trigger outcomes, bounded retry history, recovery-bound Review epochs, and stale-comment rejection.
- Add retryable Incident blocker and workspace lease state machines that preserve successor blockers, isolate lease writes, recover every partial-write prefix, and retain completed checkpoints without deadlocking later transitions.
- Revalidate integration roster, role/recovery evidence, handoff/run identity, timestamps, dependency/lease bindings, and metadata capacity at every final approval action.

## 2.0.0-dev.6

- Add auditable Delivery Policy Resolver provenance, a stable versioned semantic digest schema, and full snapshot integrity digests.
- Add explicit cross-version policy-digest pinning and supersession records so compatible upgrades or rollbacks preserve the approved Plan digest without weakening Review or approval gates.
- Prevent non-semantic Resolver fields such as the `direct_target_push` alias from creating false delivery-policy drift while retaining hard failures for real policy, selection, and remote changes.
- Clarify that Requirement Intake policy prechecks are diagnostic inputs with full provenance, while the independently reviewed Plan snapshot is the first authoritative policy freeze.

## 2.0.0-dev.5

- Bind final approval to an explicit top-level Requirement gate after Implementation is `done`.
- Make Leader the sole automated writer of top-level Requirement status and require verified Integrator delivery handoff before terminal convergence.
- Add deterministic final-gate validation for early/child approval rejection, stale evidence, Requirement PR, direct-push, local-only, non-default targets, duplicate approval recovery, and done idempotency.
- Store delivery and handoff evidence as two versioned scalar records so terminal Requirements remain within Multica's 50-key metadata limit.

## 2.0.0-dev.4

- Restrict the deployable workflow archive to the manifest, schema, deployment profiles, runtime instructions, required deployment scripts, Skills, version, and dependency declaration.
- Exclude GitHub metadata, tests, top-level development documentation, and release-only tooling from the deployable archive.

## 2.0.0-dev.3

- Allow a verified formal Release Bundle to be planned, applied, and verified without a Git checkout.
- Add an internal `release-manifest.json` that binds the release tag, source commit, and SHA-256 hash of every published workflow file.
- Bind deployment Plans, journals, and deployment evidence to a portable source identity while retaining compatibility with Git-based deployment Plans.

## 2.0.0-dev.2

- Add configurable `branch_only`, `lightweight`, and `isolated` development checkout modes.
- Make Task PR and Requirement PR independent project/Plan selections while preserving Review, test, approval, and merge evidence in local-only delivery.
- Add the portable `multica-delivery-policy` Skill for project policy validation, redacted remote-capability snapshots, Plan digests, and clean-workspace guards.
- Change unconfigured PR-capable projects to `lightweight`, Task PR disabled, and Requirement PR enabled; projects without a remote disable both PRs.
- Accept the Multica CLI's text-only success responses when retiring Autopilots and Skills.
- Scope local Runtime UUID maps by Workspace ID while retaining explicit path overrides.
- Store Runtime maps as Home-level Workflow/Workspace configuration while keeping Plans, journals, deployment evidence, release Plans, and worktrees repository-local.
- Reorganize the workflow-manager Skill for progressive disclosure, with separate command and Runtime-map references.
- Reorganize Requirement Intake so drafting, portable setup, submission, following, approvals, and user guidance load only for the active mode.

## 2.0.0-dev.1

- Replace protocol v3 maintenance automation with protocol v4 event-driven workflow Incidents.
- Remove the dedicated observation and maintenance roles, scheduled scans, maintenance cases, and secure execution environment.
- Route every workflow fix through the ordinary Requirement, Plan Review, implementation, Code Review, and final approval path.
- Allow a clean reviewed Git checkout to deploy directly to a workspace without a formal release.
- Simplify formal publishing to digest-bound packaging followed by a human-hosted GitHub Release command.
- Remove compatibility paths for older workspace protocols and release-control evidence.

## 1.0.0

- Add the declarative seven-Agent development squad, portable Runtime profiles, requirement intake, Skill packaging, and digest-approved workspace reconciliation.
