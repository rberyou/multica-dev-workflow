# Changelog

## Unreleased

- Require a versioned, explicitly confirmed requirement draft before `multica-requirement-intake` creates or starts a top-level Issue.
- Make create-and-start continue following the complete Requirement chain through human gates and terminal delivery, with an explicit capability fallback when the host cannot keep a monitor active.
- Require a final canonical-ID readback before reporting successful creation, update, or start, so an unverified Issue key cannot be presented as completed work.

## 1.1.0-rc.4

- Align desired state, default installation, CI and release assets with Phase 1: ordinary development Agents plus Observer, without activating Maintainer, Maintenance Reviewer or enforced Secure Runtime isolation.
- Add durable Observation intake with full-fingerprint idempotency, registered reporter checks, retry isolation, bounded quarantine and recovery of records left in processing.
- Harden Incident deduplication, evidence retention, source blocking and restoration, human maintenance decisions, ordinary-development progress stages and Observer-only final verification.
- Preserve failed verification attempts while allowing a corrected Requirement, PR, release and deployment evidence set to enter the next repair cycle.
- Record immutable plan-digest deployment evidence per Workspace, keep a monotonic latest pointer, and recover evidence safely from completed Apply journals without changing the original actor.
- Bind deployment evidence to the released source commit, Workspace, Plan digest, completed journal, deployment record and actor before Observer verification can close an Incident.
- Run release Request validation on trusted `main`, require the exact Phase 1 asset set and bind the annotated tag to the complete digest-bound Request and protected publish gate.
- Add durable release recovery from an existing tag or Request file, reject divergent local tags and unexpected assets, and safely replace only approved assets after interrupted publication.
- Verify release versions from the tag snapshot and use the stable `validate.yml` workflow when binding green CI provenance.
- Keep local release apply limited to dispatching the reviewed Request; tag and GitHub Release mutation remain behind the protected `workflow-release` Environment and Publisher App.
- Make the default audit path read-only while retaining explicit report mode, operation-record auditing and out-of-band Observer health checks.
- Update the workflow console, Incident runbook, release policy and role guidance to describe the Phase 1 maintenance and deployment closure path.

## 1.1.0-rc.3

- Normalize Autopilot schedule trigger aliases from real CLI responses, including `cron_expression`, while preserving `cron` and `schedule` compatibility.
- Keep Observer drift detection strict for wrong cron values, disabled triggers, timezone drift, missing triggers and duplicate managed trigger labels.
- Validate workflow maintenance Plan approvals with `APPROVE WORKFLOW PLAN <revision>` and resolve recorded approval evidence only from the associated Maintenance Change.
- Scope review-owner metadata checks by workflow object type so maintenance reviews use `maintainer_id` and `maintenance_reviewer_id` without weakening development review checks.

## 1.1.0-rc.2

- Remove the deleted Multica v0.4.2 Autopilot priority field from desired state, CLI mutations, schema, audit contracts and verification.
- Normalize real Autopilot response aliases and typed member subscribers, and use mutually exclusive subscriber replacement commands.
- Recover safely from the partially applied rc.1 Canary state with idempotent real-response regression coverage.
- Add one-time, hash-bound pending-Incident release evidence for the reviewed rc.2 recovery path.
- Validate runtime instruction `workflow_version` literals against the release version.

## 1.1.0-rc.1

- Add workflow incident reporting, scheduled observation and a dedicated maintenance control plane.
- Add declarative Project and Autopilot reconciliation with explicit operations-disable rollback support.
- Add protocol v3 metadata, audit, release provenance and canary gates.
- Bind releases to verified Multica Maintenance Review and human approval comments, with a one-time v6 RC bootstrap exception.
- Bind maintenance tag provenance to an exact fixed-approver GitHub block, merged-main PR and CI run, without publishing raw Multica UUIDs.
- Add parent-authoritative protocol inheritance, full desired-state drift auditing, recoverable Incident payloads, notification cooldown and active-v3 rollback gates.
- Revalidate bootstrap and pre-merge Review evidence at release time, preserve maximum Incident severity, enforce the complete workflow schema, preserve local-only Skill attachments, model reviewed disabled operations state and bind the exact release asset set.

## 1.0.0

- Promote the workflow after adopting the existing squad and verifying zero drift.

## 1.0.0-rc.2

- Add Windows directory-junction fallback for live Git-linked Skill installs.

## 1.0.0-rc.1

- Add declarative development squad and seven agent roles.
- Add portable Runtime deployment profiles.
- Add requirement intake and workflow manager skills.
- Add idempotent doctor, export, plan, apply, verify, drift and install-skills commands.
