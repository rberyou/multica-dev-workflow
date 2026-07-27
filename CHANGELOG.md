# Changelog

## Unreleased

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
