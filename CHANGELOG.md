# Changelog

## 1.1.0-rc.4

- Add a Windows Secure Agent Runtime with a hash-verified Launcher, tokenless Reviewer, task/Runtime attestation, pre-start Job Object binding, isolated homes, strict Codex permission profiles, and allowlisted network proxy.
- Add a repository-scoped Maintainer Token Broker that keeps the App private key and raw installation tokens outside Agent environments and exposes only bounded branch push, PR upsert, and CI read operations.
- Move release tag and GitHub Release mutation behind the protected `workflow-release` Environment, a digest-bound publish gate, and a dedicated Publisher App as the sole tag Ruleset bypass.
- Require a separate selected-repository Dispatcher App with only Actions write, Contents read and Metadata read; bind its reviewed installation to the Release Request and tag provenance while allowing normal host credentials to remain outside Agent runtimes.
- Bind workflow dispatch to the reviewed Dispatcher bot actor, defer PR readback to the built-in read-only workflow token, and allow recovery publication to use a new approved gate without rewriting the immutable tag gate.
- Push Maintainer branches only from Broker-owned bare snapshots, bind CONNECT tunnels to TLS SNI, delete task credential homes on exit, and make install and binding updates transactionally restorable.
- Make local release apply dispatch a digest-bound Release Request without creating, deleting or pushing tags or publishing Releases; raw tag pushes no longer trigger publication.
- Bind RC4 provenance to legacy WOR-45 and WOR-48 records plus the final Phase 1 integration-validation record, the Release Request, workflow run, Environment approval actor, Publisher installation, CI and bounded Maintenance Review evidence.
- Add the host-only workflow-console Skill, automatic Maintainer/Reviewer handoff loop, two-phase Runtime bindings Bootstrap, install/doctor/update/rollback scripts, and a self-contained `win-x64` release asset.
- Scope Observer workflow gates to managed objects while preserving pending-Incident recovery for legacy/external intake.
- Disable automatic maintenance-tree expansion, require human-gated maintenance intake and lazily create later stages.
- Keep Reporter Skill available while Observer Autopilot operations are paused.
- Correct RC4 current-main finalization evidence to require legacy WOR-45, legacy WOR-48 and the final Phase 1 integration-validation record; document that Dispatcher does not inspect Publisher App identity and that omitted `bypass_actors` relies on reviewed administrator evidence while malformed or incorrect present values fail closed.

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
