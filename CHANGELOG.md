# Changelog

## Unreleased

- Scope local Runtime UUID maps by Workspace ID while retaining explicit path overrides.

## 2.0.0-dev.1

- Replace protocol v3 maintenance automation with protocol v4 event-driven workflow Incidents.
- Remove the dedicated observation and maintenance roles, scheduled scans, maintenance cases, and secure execution environment.
- Route every workflow fix through the ordinary Requirement, Plan Review, implementation, Code Review, and final approval path.
- Allow a clean reviewed Git checkout to deploy directly to a workspace without a formal release.
- Simplify formal publishing to digest-bound packaging followed by a human-hosted GitHub Release command.
- Remove compatibility paths for older workspace protocols and release-control evidence.

## 1.0.0

- Add the declarative seven-Agent development squad, portable Runtime profiles, requirement intake, Skill packaging, and digest-approved workspace reconciliation.
