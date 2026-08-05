# Changelog

## Unreleased

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
