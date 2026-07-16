# Changelog

## 1.1.0-rc.1

- Add workflow incident reporting, scheduled observation and a dedicated maintenance control plane.
- Add declarative Project and Autopilot reconciliation with explicit operations-disable rollback support.
- Add protocol v3 metadata, audit, release provenance and canary gates.
- Bind releases to verified Multica Maintenance Review and human approval comments, with a one-time v6 RC bootstrap exception.
- Bind maintenance tag provenance to an exact fixed-approver GitHub block, merged-main PR and CI run, without publishing raw Multica UUIDs.
- Add parent-authoritative protocol inheritance, full desired-state drift auditing, recoverable Incident payloads, notification cooldown and active-v3 rollback gates.

## 1.0.0

- Promote the workflow after adopting the existing squad and verifying zero drift.

## 1.0.0-rc.2

- Add Windows directory-junction fallback for live Git-linked Skill installs.

## 1.0.0-rc.1

- Add declarative development squad and seven agent roles.
- Add portable Runtime deployment profiles.
- Add requirement intake and workflow manager skills.
- Add idempotent doctor, export, plan, apply, verify, drift and install-skills commands.
