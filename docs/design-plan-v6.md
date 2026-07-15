# Multica Workflow Plan v6: Observability and Maintenance Control Plane

## 1. Status and Approval Gate

- Status: Draft under review.
- Baseline release: `v1.0.0` at commit `63380d2ea0f957eac00168584f01c30408a3db20`.
- Target release: `v1.1.0-rc.1`, followed by `v1.1.0` after canary validation.
- Current protocol: `v2`.
- Target protocol: `v3`.

This Plan adds a workflow operations control plane around the existing development-delivery workflow. It does not authorize repository implementation, Multica mutation, release creation or workspace rollout until review completes and the human approver gives:

```text
APPROVE WORKFLOW PLAN v6
```

Every later Multica mutation still requires its own digest-bound approval. Release tagging also receives a separate release-plan approval.

## 2. Goals

1. Let every managed development Agent report suspected workflow defects without silently bypassing the workflow.
2. Add an independent Observer Agent that receives reports, runs scheduled audits, deduplicates evidence and classifies incidents.
3. Add a Maintainer Agent and an independent Maintenance Reviewer Agent that repair the workflow product through Git, review, CI, release candidates and controlled rollout.
4. Keep Git as desired state and Multica as runtime state for Agents, Skills, Project and Autopilot configuration.
5. Add deterministic audit rules for process invariants instead of relying entirely on LLM judgment.
6. Preserve existing v2 requirements while new requirements adopt protocol v3.
7. Provide explicit canary, release, production rollout and rollback gates.
8. Preserve unrelated Multica Projects, Agents, Skills, Squads and Autopilots.

## 3. Non-Goals

- Automatically modifying source code or Multica configuration when the Observer finds a problem.
- Treating every task failure, code defect or unclear product requirement as a workflow incident.
- Giving the Observer authority to approve Plans, merge PRs, create releases or apply deployment Plans.
- Deleting unrelated or retired Multica objects in v1.1.
- Providing a hard security sandbox around an Agent that already has owner-authenticated Multica CLI access. Role instructions and audit provide governance, not an OS-level command boundary.
- Automatically migrating every in-flight v2 requirement to v3.
- Replacing Multica product support. Defects in Multica itself must be routed to the upstream Multica issue tracker after confirmation.

## 4. Verified Baseline

The following baseline was verified read-only before this Plan was written:

- Git `main` is clean and exactly tagged `v1.0.0`.
- The `v1.0.0` Release is stable and contains the full repository, both existing Skills and SHA256 checksums.
- The `main` validation run for the tagged commit passed.
- Multica profile: `desktop-api.multica.ai`.
- Workspace: `T0`.
- Deployment profile: `quality`.
- Runtime inventory required by the profile is online.
- Reconciler verification reports `NO_CHANGE: 9`.
- Workspace inventory contains 10 Agents, of which 7 are managed by this workflow.
- Workspace inventory contains one Squad and one workspace Skill managed by this workflow.
- Workspace inventory contains two unrelated user Projects and zero Autopilots.
- A local redacted export snapshot was generated under `exports/t0/`; it is gitignored.

The current Multica CLI supports the required primitives:

- Project create, list, get and update.
- Autopilot create with Agent, description/task prompt, mode, title, issue-title template, priority, Project ID and subscriber IDs.
- Autopilot update with Agent, description, mode, title, priority, Project ID, status and complete subscriber replacement/clear.
- Autopilot list/get, where get includes triggers, plus execution runs and manual trigger.
- Autopilot trigger add/update/delete/rotate operations, including cron, timezone, label and enabled state for schedules.
- Issue creation with Project, assignee, status, priority, parent and stage.
- Issue comments, explicit assignment, status changes and parent/stage child creation.
- Issue metadata set/get/list/delete and metadata-filtered listing.
- Issue subscriber management by stable member or Agent ID.
- Active duplicate prevention during issue creation.
- Issue execution history and run messages.
- Workspace list/get/switch/update, but no CLI workspace creation command.

The repository is private and the current GitHub account plan does not provide Branch Protection or Rulesets for this repository. PR discipline therefore cannot be described as a hard repository-level prevention. The release pipeline must add its own verifiable source checks, while owner bypass remains an explicit residual risk.

## 5. Control-Plane Topology

The existing development-delivery Squad remains unchanged in shape. The new control-plane Agents are workspace-visible managed Agents but are not Squad members.

```text
Development Delivery Squad
├── existing seven managed Agents
└── shared workflow incident reporting contract

Workflow Operations Control Plane
├── Project: 工作流运维
├── Agent: 工作流观察员
├── Agent: 工作流维护员
├── Agent: 工作流维护审查员
└── Autopilot: 工作流健康巡检
```

No second Squad is introduced in v1.1. Direct Issue assignment is sufficient and avoids expanding the current single-Squad manifest model before it is needed.

### 5.1 Workload and Trust Boundaries

| Actor | May read/audit | May create or update Incident data | May change Git | May review workflow changes | May tag/release | May Apply Multica Plan |
|---|---:|---:|---:|---:|---:|---:|
| Development Agent in Reporter Mode | scoped | report only | existing task scope only | no | no | no |
| Workflow Observer | yes | yes | no | runtime verification only | no | no |
| Workflow Maintainer | yes | yes | yes | no self-review | only after release approval | only after deployment approval |
| Maintenance Reviewer | yes | review comments only | no modification of reviewed change | yes | no | no |
| Human Approver | yes | decisions | policy approval | final authority | approval | digest approval |

These are workflow policy boundaries. Multica Runtime shell/CLI access is not currently a command-level RBAC boundary. Violations must be detectable through Git history, deployment journals, managed-object drift and Incident audits.

### 5.2 Durable Identity Model

Workflow independence is evaluated with Multica identities, not GitHub login names:

- `reporter_agent_id`: Agent that raised an Incident.
- `observer_id`: unique managed Workflow Observer Agent.
- `maintainer_id`: unique managed Workflow Maintainer Agent and original owner of a Maintenance Change.
- `maintenance_reviewer_id`: unique managed Maintenance Reviewer Agent.
- `human_approver_id`: unique `member_type=member`, role `人工审批人`, resolved from the managed development Squad roster.

Every Incident and Maintenance tree stores the applicable IDs as durable metadata. A valid maintenance Review comment must have `author_type=agent`, `author_id=maintenance_reviewer_id`, bind the exact Plan revision and commit SHA, and come from an Agent different from `maintainer_id`. A valid human decision or release approval must have `author_type=member` and `author_id=human_approver_id`.

GitHub logins are recorded as provenance but are not used to prove reviewer independence because multiple local Agents may operate through the same authenticated GitHub account. Release tooling verifies the Multica review/approval records and the exact released SHA. Same-owner control of multiple Agents remains an explicit governance limitation rather than independent human review.

## 6. Managed Objects

### 6.1 Skills

#### `multica-workflow-observer`

Targets:

- Local installation for external recovery and manual audit.
- Workspace Skill import.
- Attached to the seven development Agents and the Workflow Observer.

Modes:

- Reporter Mode: create or update a standardized Incident and link it to the source Issue.
- Observer Mode: run deterministic audits, triage Incidents, request evidence and hand confirmed defects to the Maintainer.

Reporter Mode is the default for non-Observer task contexts. Observer-specific instructions authorize Observer Mode. The Skill must not describe its mode distinction as a hard security sandbox.

#### `multica-workflow-maintainer`

Targets:

- Local installation so an external Codex/OpenCode session can recover the workflow even when Multica control-plane Agents are unavailable.
- Workspace Skill import.
- Attached only to the Workflow Maintainer and Maintenance Reviewer.

Modes:

- Maintainer Mode: diagnosis, change Plan, implementation, tests, RC, release, rollout and rollback preparation.
- Reviewer Mode: independent Plan, code, release and rollback review without modifying reviewed content.

### 6.2 Agents

| Key | Name | Runtime/model | Thinking | Concurrency | Membership |
|---|---|---|---|---:|---|
| `workflow-observer` | 工作流观察员 | OpenCode / `team-litellm/gpt-5.5` | default | 1 | no Squad |
| `workflow-maintainer` | 工作流维护员 | Codex / `gpt-5.5` | `xhigh` | 1 | no Squad |
| `workflow-maintenance-reviewer` | 工作流维护审查员 | OpenCode / `team-litellm/gpt-5.5` | default | 1 | no Squad |

The Observer is optimized for independent read-heavy triage. The Maintainer receives the quality-first Codex binding. The Reviewer stays independent from the author Runtime and role.

### 6.3 Operations Project

- Key: `workflow-operations`.
- Title: `工作流运维`.
- Lead: Workflow Observer.
- Initial status: `in_progress`.
- Contains Incident and Maintenance Issue trees only.
- Project description contains the managed marker and operator-facing purpose. Runtime UUIDs are resolved locally and never committed.

The reconciler must match the Project by managed marker and stable object key. A same-name unmarked Project requires `--adopt`; multiple matches block planning.

The current Project CLI accepts a lead name but not a lead UUID. Before create/update, the reconciler resolves the managed Observer Agent, verifies that its exact name is unique in the Workspace, passes that exact name to the CLI and verifies the returned `lead_id`. Ambiguous names block planning.

### 6.4 Observer Autopilot

- Key: `workflow-health-audit`.
- Title: `工作流健康巡检`.
- Agent: Workflow Observer.
- Mode: `run_only`.
- Schedule: hourly, `0 * * * *`, timezone `Asia/Shanghai`.
- Concurrency: inherited from Observer Agent, maximum one task.
- Project: Workflow Operations Project for context and future findings.
- Initial status: active only after canary deployment approval.

The Autopilot does not create a healthy-scan Issue. It runs the deterministic audit command and creates or updates Incidents only when findings exist. Manual trigger remains available for validation and recovery.

The Autopilot prompt contains the exact audit-report command and resolves the Operations Project by managed marker at runtime. It does not rely on undocumented injected Project context. Scheduled execution is the configured fallback detection path while the Multica scheduler and Runtime are healthy.

Direct assignment of a `todo` Incident to a workspace-visible non-Squad Agent is expected to enqueue that Agent based on the Issue execution behavior already used by the development workflow. Canary must prove this exact non-Squad case. Until that test passes, immediate assignment is treated as a release-blocking assumption, while the hourly Autopilot remains the fallback detector.

The reconciler manages only triggers carrying its stable managed label. Unrelated triggers are preserved. v1.1 supports create and in-place schedule updates; replacing a managed schedule trigger with a webhook trigger is blocked for explicit design rather than silently deleting and recreating it.

## 7. Incident Reporting Contract

### 7.1 What Must Be Reported

Development Agents report when they observe any of the following:

- Required workflow rules conflict or cannot all be satisfied.
- An approval, Review, dependency, branch or status gate is bypassed or incorrectly accepted.
- A required Agent, Runtime, Skill, roster role or metadata contract is missing or ambiguous.
- The platform behaves differently from the assumptions encoded in the workflow.
- Repeated workflow-level failure prevents progress even though the product task itself is valid.
- Managed workflow configuration appears to have drifted.
- The workflow creates duplicate, orphaned or incorrectly linked Issues.

They do not report ordinary code defects, failed product tests, unclear business requirements, a single transient Runtime error or a Plan defect already handled by the existing Plan revision process unless the workflow itself mishandles that condition.

### 7.2 Severity and Blocking

| Severity | Example | Source Issue behavior |
|---|---|---|
| `urgent` | invalid human approval, security/privacy exposure, destructive Git history action | immediately `blocked` |
| `high` | workflow may merge or complete incorrect work | block affected stage |
| `medium` | workflow stalls, duplicates work or violates recoverable process rules | report; continue only when correctness remains intact |
| `low` | documentation, usability or low-risk efficiency gap | report and continue |

The reporting Agent records why it blocked or continued. Observer classification may later raise or lower severity but cannot retroactively legitimize a bypass.

### 7.3 Creation Sequence

1. Resolve the unique managed Workflow Operations Project and Workflow Observer Agent.
2. Build a stable `incident_dedupe_key`.
3. Search active Incidents using metadata filters.
4. If a match exists, append new evidence and link the source Issue to the existing Incident.
5. Otherwise create a top-level Issue in the Workflow Operations Project with status `todo`, priority mapped from severity and assignee set to the Observer.
6. Write durable Incident metadata.
7. Add a source-Issue comment containing the Incident link and report disposition.
8. Write `workflow_incident_id` and `workflow_incident_pending=false` on the source Issue.

Issue assignment plus `todo` is the intended immediate Observer trigger and is a canary release gate. A comment or mention alone is not considered a durable report.

Reporter Mode and scheduled audit-report mode share one deterministic Incident writer rather than independently composing CLI calls. The implementation exposes a bounded command/API that owns Project/Observer resolution, dedupe, creation, metadata, source linkage, subscribers and failure reporting. LLM instructions provide classification input but do not reimplement idempotency.

### 7.4 Incident Metadata

Required keys:

```json
{
  "workflow_object_type": "incident",
  "workflow_id": "development-delivery",
  "incident_dedupe_key": "development-delivery:v3:approval-current-author:T-123",
  "incident_rule_id": "WF-APPROVAL-001",
  "incident_status": "new",
  "incident_severity": "high",
  "source_issue_id": "T-123",
  "source_requirement_id": "T-100",
  "reporter_agent_id": "<resolved-agent-id>",
  "reporter_role": "代码审查员",
  "observer_id": "<resolved-observer-id>",
  "human_approver_id": "<resolved-member-id>",
  "workflow_version": "1.1.0-rc.1",
  "protocol_revision": "v3",
  "waiting_on": "workflow_observer"
}
```

Expected behavior, actual behavior, reproduction and evidence belong in the Incident description/comments rather than long metadata values. Metadata remains compact and routing-oriented. Evidence links, timestamps, hashes and run IDs may be added.

Tokens, cookies, authorization headers, private keys, raw environment values and unredacted user content must never be copied into Incident metadata or comments. A shared redaction helper removes known secret-key fields, Bearer/basic credentials, cookies, private-key blocks, sensitive environment values and oversized payloads. Tests include nested values and mixed text. When evidence cannot be summarized safely, the Incident records only the source reference and the statement `raw evidence withheld by privacy boundary`.

The version is read from the installed Observer Skill or another managed release marker. The protocol revision comes from the current Issue contract; missing protocol metadata on a pre-v3 Issue is treated as v2.

### 7.5 Dedupe and Cooldown

The dedupe key is derived from workflow ID, protocol revision, deterministic rule ID and the smallest stable affected entity. It must not include timestamps or free-form prose.

- Active duplicate: append evidence; do not create another Incident.
- Closed duplicate recurring in the same release before a verified fix: move the original Incident back to `todo` and append evidence.
- Recurrence after a verified fix or in a later release: create a new Incident with `recurrence_of=<old-incident-id>`.
- Reopening a same-release unresolved Incident reuses its existing Maintenance Change tree. It never creates a second Maintenance Change; the Observer updates or re-promotes the earliest incomplete/failed relevant stage and records the recurrence evidence.
- Scheduled scans apply a 24-hour notification cooldown per active Incident.
- Cooldown is bypassed for urgent findings, severity increases, a newly affected requirement/entity, the first deterministic confirmation of a heuristic report, recurrence after closure or an increased blocked-requirement count.
- The Observer never uses `--allow-duplicate` during normal reporting.

### 7.6 Reporting Failure

If Incident creation or metadata completion fails:

1. Do not retry unboundedly.
2. Set source metadata `workflow_incident_pending=true` when possible.
3. Record a compact local/source comment with the failure reason.
4. Block only when the underlying severity requires it.
5. The scheduled Observer audit searches for pending reports and completes them.

If a deterministic finding cannot be persisted as an Incident or linked to an existing one, the audit-report operation is considered failed rather than healthy.

## 8. Observer Triage Contract

The Observer reads the Incident, source Issue, parent chain, relevant comments, metadata and bounded execution summaries before classifying it. It does not implement fixes.

Allowed verdicts:

- `CONFIRMED_WORKFLOW_BUG`
- `WORKFLOW_GAP`
- `USAGE_ERROR`
- `PROJECT_DEFECT`
- `RUNTIME_INCIDENT`
- `MULTICA_PRODUCT_DEFECT`
- `FALSE_POSITIVE`
- `DECISION_REQUIRED`

### 8.1 Confirmed Defect Handoff

For `CONFIRMED_WORKFLOW_BUG` or `WORKFLOW_GAP`, the Observer creates exactly one Maintenance Change child Issue under the Incident:

```text
Incident Issue                         owner: Workflow Observer
└── Maintenance Change                owner: Workflow Maintainer
    ├── Change Plan                   stage 1
    ├── Implementation                stage 2
    ├── Canary Validation             stage 3, Observer
    └── Rollout Verification          stage 4, Observer
```

The Incident remains `in_progress`. Change Plan starts as `todo`; Implementation, Canary Validation and Rollout Verification start as `backlog` and are promoted only when their preceding gate completes. The tree stores `maintainer_id`, `maintenance_reviewer_id` and `human_approver_id` resolved from managed Agents and the development Squad roster.

The Maintenance Change records the Incident link, affected release, severity, reproduction, required acceptance scenarios and whether active requirements must remain blocked.

### 8.2 Non-Workflow Results

- `USAGE_ERROR`: explain the correct operation and close after the reporter acknowledges or evidence is sufficient.
- `PROJECT_DEFECT`: link or create a requirement in the affected product Project; do not send it to the workflow Maintainer.
- `RUNTIME_INCIDENT`: route to Runtime/operator handling and retain the Incident until service recovery is verified.
- `MULTICA_PRODUCT_DEFECT`: produce a redacted upstream report draft and require human approval before external publication.
- `FALSE_POSITIVE`: record the violated detection assumption and add a regression scenario when appropriate.
- `DECISION_REQUIRED`: set `blocked`, record options and request the configured human approver.

### 8.3 Privacy Boundary

Scheduled audits do not read full run-message content by default. They inspect Issue structure, metadata, comments, statuses, assignees, PR references, run outcomes and managed configuration. Full run messages are read only for a specific Incident when necessary, with bounded pagination and redacted reporting.

### 8.4 Operational Visibility and SLOs

- Urgent and high Incidents subscribe `human_approver_id` and emit a compact notification comment immediately after persistence.
- Medium and low Incidents notify the Observer only unless they block a requirement or expand in scope.
- A directly reported Incident should enter Observer triage immediately; while the scheduler and Runtime are healthy, the configured fallback SLO is one hourly schedule interval plus 15 minutes.
- An urgent/high Incident not triaged within two hours is escalated to the human approver by the next successful audit-report run.
- A Maintenance Change for an urgent Incident without progress for 24 hours creates an escalation finding.
- Two consecutive failed Observer Autopilot runs create or update an Observer-health finding when another control path can read run history.

A total Multica scheduler outage cannot be detected by an Autopilot running on that scheduler. Add a read-only `workflow.py health` command and Workflow Manager runbook that inspect the latest Autopilot runs and require an external human/agent scheduler for hard out-of-band monitoring. v1.1 documents this residual dependency and does not claim complete self-monitoring.

## 9. Deterministic Audit Engine

The canonical deterministic implementation is packaged inside the Observer Skill so Reporter tasks and run-only Autopilot tasks do not depend on a workflow Git checkout or Project working directory. The Skill contains a portable script entry point, for example:

```text
<observer-skill>/scripts/observer.py audit ...
<observer-skill>/scripts/observer.py audit --report ...
<observer-skill>/scripts/observer.py report-incident ...
```

The repository-level command delegates to that same implementation for operator convenience and testing:

```text
python scripts/workflow.py audit --workspace <id-or-slug> --scope all --output json
python scripts/workflow.py audit --workspace <id-or-slug> --scope all --report --output json
```

The Autopilot prompt resolves the installed Observer Skill directory according to the portable Skill rules and invokes the packaged entry point. It must not assume the repository exists, the current directory is a project checkout or a fixed user path is available.

Without `--report`, the command is read-only. With `--report`, it may only create/update Incident Issues, metadata, comments and subscribers in the managed Operations Project.

The command returns a structured result containing scanned counts, coverage status, finding counts by severity, created/updated Incident IDs and notification results. It exits successfully when the scan completes and all required Incident persistence succeeds, even when findings exist. Operational, authentication, schema, incomplete-coverage, data-fetch or required-persistence failures return non-zero. This prevents findings from being confused with process failure while ensuring an urgent finding cannot be silently hidden behind a healthy Autopilot run.

The command is stateless across machines. It scans active workflow-managed Issues with pagination and metadata filters. New v3 top-level requirements and descendants carry `workflow_id`, `workflow_version` and `protocol_revision`. Existing Issues without protocol metadata are treated as v2 and are scanned only when explicitly reported or linked.

### 9.1 Initial Rule Set

| Rule ID | Invariant | Default severity |
|---|---|---|
| `WF-APPROVAL-001` | approval author/type/version matches durable human approval metadata | urgent |
| `WF-PLAN-001` | Implementation is not started before approved Plan completion | high |
| `WF-REVIEW-001` | reviewed commit equals current remote head SHA | high |
| `WF-REVIEW-002` | reviewer is independent from the author | high |
| `WF-DEPENDENCY-001` | todo/in-progress task dependency contract is satisfied | high |
| `WF-PARENT-001` | parent done requires required children and gates complete | high |
| `WF-BLOCKED-001` | blocked Issue has `blocked_reason` and `waiting_on` | medium |
| `WF-BACKLOG-001` | dependency-satisfied backlog Issue is not stranded beyond threshold | medium |
| `WF-PLANREV-001` | task and child Plan revisions match the active approved revision | high |
| `WF-INCIDENT-001` | pending Incident reports are completed or visibly failed | medium |
| `WF-RUNTIME-001` | repeated or offline Runtime failure is surfaced and not silently reassigned | high |
| `WF-DRIFT-001` | managed Agent, Skill, Squad, Project and Autopilot state matches Git | high |
| `WF-OBSERVER-001` | Observer Autopilot has a recent successful run and no repeated execution failure | high |
| `WF-MAINT-001` | confirmed urgent/high Maintenance Change is not stranded beyond its SLO | high |

Each finding contains `rule_id`, severity, affected entity, expected/actual summary, evidence references, dedupe key and suggested routing. LLM analysis may add context but cannot remove or rewrite deterministic evidence.

### 9.2 Scan Bounds

- Default schedule: hourly.
- Process only workflow-managed active Issues plus pending Incident markers.
- Paginate; do not assume the default 50-Issue CLI limit is complete.
- Paginate to completion with a default safety limit of 5,000 active workflow Issues.
- When the safety limit is exceeded, prioritize `in_review`, `done`, `blocked` and `in_progress`, mark coverage `incomplete`, create an urgent operational Incident and return non-zero. The run cannot be reported as healthy until full coverage is restored or the limit is explicitly increased through reviewed configuration.
- Do not create an Issue when no findings exist.
- Store no cross-machine local cursor as the source of truth.

## 10. Maintainer Contract

The Maintainer accepts a Maintenance Change only when it is linked to a confirmed Incident or explicitly created by the human approver as a planned workflow enhancement.

### 10.1 Change Plan

The Change Plan includes:

- reproduction and root cause;
- affected releases, protocol revisions and workspaces;
- instruction, Skill, schema, reconciler, CI and documentation impact;
- alternatives and tradeoffs;
- deterministic and Agent-behavior tests;
- migration and compatibility behavior;
- canary scope;
- production rollout order;
- rollback procedure;
- unresolved decisions.

The Maintenance Reviewer performs an independent Plan Review Loop. Decision-bearing findings require the human approver. Plan approval uses an explicit versioned comment, separate from later deployment digest approval.

Maintenance metadata records `maintainer_id`, `maintenance_reviewer_id`, `human_approver_id`, `plan_revision`, `reviewed_commit_sha`, `review_comment_id`, `approval_comment_id`, `github_pr_number`, `github_merge_commit_sha` and release/canary Plan digests as they become available.

### 10.2 Implementation

- Branch from the latest stable/green `main` using `fix/`, `feat/` or `security/` naming.
- Never edit deployed Multica objects to prototype a source change.
- Add a failing regression test before or with the fix when deterministic reproduction is possible.
- Run schema validation, compile checks, all unit/integration tests, Skill packaging and portability/secret checks.
- Open a PR and bind Review to a concrete commit SHA.
- Any code or instruction update invalidates the previous Review.
- Release/audit tooling rejects a Review whose Multica `author_id` is not the managed Maintenance Reviewer, equals the Maintainer ID, or does not bind the current PR head SHA before merge. The release Plan separately binds the resulting PR `merge_commit_sha` and verifies both links. GitHub self-review restrictions are not used as the identity source.

### 10.3 Release Candidate and Canary

Protocol or control-plane changes require at least one RC. The initial target is `v1.1.0-rc.1`.

Canary order:

1. Select a pre-existing isolated canary Workspace. The current CLI cannot create a Workspace; UI creation or membership changes are explicit human operations outside the reconciler and must complete before planning.
2. Apply the RC desired state using the normal digest gate.
3. Generate a digest-bound Canary Execution Plan listing every test Issue, metadata mutation, manual Autopilot trigger and expected cleanup status.
4. Wait for `APPROVE WORKFLOW CANARY <short-digest>`.
5. Execute only the approved scenario injections and manually trigger the Observer Autopilot.
6. Mark test Issues done/cancelled after evidence is recorded; do not destructively delete history.
7. Confirm Incident creation, dedupe, triage, Maintenance handoff, Observer verification and zero drift.
8. Confirm unrelated Projects, Agents, Skills and Autopilots are unchanged.
9. Run a rollback rehearsal before stable release.

The production T0 Workspace is not the first canary target.

### 10.4 Stable Release and Rollout

The Maintainer prepares a release Plan bound to version, Git commit, one exact merged PR, validation run, Multica maintenance Review record, human approval record, changelog and expected assets. The release commit must equal the `merge_commit_sha` of that closed merged PR, whose base is `main`; cherry-picked, directly pushed, rebase-only or ambiguous multi-PR release sources are rejected in v1.1. The exact approval form is:

```text
APPROVE WORKFLOW RELEASE <short-digest>
```

After approval, the release tool creates the tag. GitHub Release automation verifies version/tag consistency, commit reachability from `main`, a green validation run and association with a merged PR before publishing assets.

Each Workspace then receives its own reconciler Plan and existing approval form:

```text
APPROVE WORKFLOW PLAN <short-digest>
```

Stable release publication never authorizes Workspace mutation by itself.

## 11. Protocol v3 and In-Flight Compatibility

Adding cross-role Incident reporting changes the shared contract, so `protocol_revision` becomes `v3`.

Updating managed Agents in place means old v2 Issues will be handled by v3-capable instructions after deployment. The system therefore uses compatibility behavior rather than pretending old Agent instructions remain active:

- A top-level requirement created after v3 deployment receives `workflow_id`, `workflow_version` and `protocol_revision=v3` metadata, propagated to descendants.
- An existing Issue with explicit `protocol_revision=v2` remains a v2 requirement.
- An existing Issue with no protocol metadata is treated as v2.
- The top-level requirement is the protocol authority for its complete development Issue tree.
- A child with missing protocol metadata inherits the top-level value without requiring a write.
- A child whose explicit protocol conflicts with its top-level requirement is blocked and reported; a v3 child cannot silently migrate a v2 parent.
- Incident and Maintenance trees use the control-plane protocol but record the source requirement protocol separately.
- Core Plan, Review, branch, approval and merge semantics remain backward compatible.
- Incident reporting is additive and may be used for a v2 Issue without migrating its core workflow.
- Existing Issue trees are not bulk edited during rollout.
- A behavior-breaking future protocol requires versioned parallel Agents or an explicit migration design; v3 does not introduce that complexity.

## 12. Manifest, Schema and Reconciler Changes

### 12.1 Schema

Increment manifest `schema_version` to 2 and add optional top-level collections:

```json
{
  "projects": [],
  "autopilots": []
}
```

The v1.1 reconciler validates schema version 2 and keeps existing v1 release behavior available through its tagged code. The portable manifest contains stable keys and selectors, never resolved UUIDs.

### 12.2 Project Reconciliation

Add actions:

- `CREATE_PROJECT`
- `ADOPT_PROJECT`
- `UPDATE_PROJECT`
- `NO_CHANGE`
- `BLOCKED`

Project reconciliation preserves unrelated resources and Issues. v1.1 does not delete or archive Projects.

### 12.3 Autopilot Reconciliation

Add actions:

- `CREATE_AUTOPILOT`
- `ADOPT_AUTOPILOT`
- `UPDATE_AUTOPILOT`
- `PAUSE_AUTOPILOT`
- `ADD_AUTOPILOT_TRIGGER`
- `UPDATE_AUTOPILOT_TRIGGER`
- `NO_CHANGE`
- `BLOCKED`

Autopilot desired state is limited to fields exposed by the verified CLI: Agent selector, mode, Project ID, task-prompt description, title, priority, status, complete subscriber set and managed schedule triggers. `autopilot get` must return enough data to verify each managed field and trigger. If the server omits a desired field, treats it as read-only or returns a value that cannot be verified, planning blocks rather than silently ignoring drift. Existing Runtime bindings are not changed by these actions.

Unmarked same-name objects require `--adopt`. Multiple candidates block. Unrelated Autopilots and triggers are preserved. No prune/delete action is introduced.

### 12.4 Apply Ordering

1. Validate repository, Runtime map and workspace preconditions.
2. Import/update Skills.
3. Create/update control-plane Agents.
4. Attach managed Skills while preserving unrelated assignments.
5. Create/update the Operations Project.
6. Create/update the Observer Autopilot and its managed schedule.
7. Update existing development Agents with protocol v3 reporting instructions.
8. Reconcile the existing Squad without adding control-plane Agents to the roster.
9. Verify the complete desired state.

Any partial failure is recovered by generating a new Plan against observed state. Apply remains idempotent and does not attempt destructive transaction rollback.

## 13. Rollback

Checking out `v1.0.0` alone is insufficient because the v1.0 reconciler does not know about the new Autopilot, Agents, Skills or Project.

Rollback sequence:

1. While still using v1.1 tooling, generate an explicit `--disable-operations` Plan.
2. Review and approve the digest.
3. Pause the managed Observer Autopilot.
4. Detach the Observer Skill from the seven development Agents.
5. Leave control-plane Agents, Project, Skills and Incident history present but idle; do not delete or archive them.
6. Inventory active v3 requirements. Rollback is blocked while active v3 requirements exist unless the human approver explicitly freezes them or approves degraded continuation under v2 core semantics.
7. Checkout the previous stable tag `v1.0.0`.
8. Generate and approve a normal rollback deployment Plan to restore v2 Agent/Squad/requirement-intake desired state.
9. Verify zero drift relative to v1.0 managed scope.

Rollback does not erase Incident evidence. Roll-forward to v1.1 reuses managed markers and resumes the Autopilot only through another reviewed Plan.

Before RC publication, an automated compatibility test runs the actual v1.0 tagged reconciler against a fake workspace containing paused v1.1 control-plane objects and proves that v1.0 ignores unrelated objects while reporting no change for its managed scope.

## 14. Release Governance Without Branch Protection

Because GitHub currently cannot enforce protected `main` for this private repository, v1.1 adds release-time controls:

- A release-plan command computes a digest from version, commit, merged PR, validation run, changelog and asset manifest.
- Release tagging requires exact human release approval.
- The tag annotation records the release-plan digest.
- The Release workflow rejects a tag when VERSION/workflow/Skill versions differ.
- The tag commit must be reachable from `origin/main`.
- The tag commit must have a successful validation run.
- The tag commit must exactly equal the `merge_commit_sha` of one selected closed merged PR targeting `main`.
- The selected PR head SHA must match the commit bound by the independent Multica Maintenance Review before merge.
- Release assets remain deterministic and checksummed.

These controls protect published releases but cannot prevent the repository owner from pushing directly to `main`. Upgrading the GitHub plan, making the repository public or moving to a Git service with private protected branches remains the only hard prevention for direct owner pushes.

## 15. Implementation Task DAG

### Stage 1: Declarative Model

#### V6-T1: Schema v2 and Manifest Model

- Add Projects and Autopilots to the schema and normalized desired-state model.
- Add the three control-plane Agent definitions and two new Skills.
- No Multica mutation.

#### V6-T2: Project Reconciliation

- Depends on V6-T1.
- Implement project observe/plan/apply/verify/adopt behavior.
- Add fake CLI lifecycle and preservation tests.

#### V6-T3: Autopilot Reconciliation

- Depends on V6-T1 and V6-T2.
- Implement Autopilot and managed-trigger observe/plan/apply/verify/adopt/pause behavior.
- Add trigger idempotence and unrelated-trigger preservation tests.

### Stage 2: Detection and Maintenance Capabilities

#### V6-T4: Deterministic Audit Engine

- Depends on V6-T1.
- Implement structured rules, pagination, filtering, evidence and dedupe keys.
- Add fixtures for every initial rule.

#### V6-T5: Observer Skill and Agent Instructions

- Depends on V6-T2, V6-T3 and V6-T4.
- Implement Reporter and Observer modes, Incident schema, dedupe, fallback and triage.
- Add Skill validation and portability tests.

#### V6-T6: Maintainer Skill and Maintenance Roles

- Depends on V6-T1.
- Implement Maintainer and Reviewer modes, change/release/rollback runbooks and approval gates.
- Add Skill validation and secret/path portability tests.

### Stage 3: Protocol and Release Controls

#### V6-T7: Protocol v3 Reporting Integration

- Depends on V6-T5.
- Update shared and role instructions.
- Add requirement version metadata and v2 compatibility behavior.
- Add tests proving absent protocol metadata is treated as v2.

#### V6-T8: Release Plan and Release Workflow Guards

- Depends on V6-T6.
- Implement digest-bound release planning and tag preflight checks.
- Test rejection of direct/unreviewed/failed-CI tag sources.

### Stage 4: Integration

#### V6-T9: End-to-End Fake Workspace Lifecycle

- Depends on V6-T2 through V6-T8.
- Empty workspace create, existing workspace adoption, no-change second Plan, partial failure recovery, operations disable and rollback preparation.

#### V6-T10: Documentation and Operator Manual

- Depends on V6-T5 through V6-T8.
- Add maintenance policy, Incident runbook, release policy, rollback runbook, Issue templates and PR template.

### Stage 5: RC and Rollout

#### V6-T11: RC Build and Canary

- Depends on V6-T9 and V6-T10.
- Publish `v1.1.0-rc.1`, deploy to canary through digest approval and run acceptance scenarios.

#### V6-T12: Stable Release and T0 Rollout

- Depends on successful V6-T11 and Observer verification.
- Publish `v1.1.0` through release approval, then generate a separate T0 deployment Plan and wait for digest approval.

## 16. Acceptance Scenarios

1. A development Agent reports a high-severity workflow defect; exactly one todo Incident is assigned to the Observer and the source Issue is linked and blocked.
2. Two Agents report the same rule/entity; the second report appends evidence instead of creating a duplicate.
3. Incident creation fails after source detection; `workflow_incident_pending=true` is later recovered by the scheduled audit.
4. A healthy scheduled scan creates no Issue and records a successful Autopilot run.
5. A scan with urgent findings persists/subscribes the Incident and reports urgent counts even though the completed scan exits zero.
6. A scan that cannot persist a required Incident returns non-zero and is not reported as healthy.
7. A non-approver comment resembling approval is detected as `WF-APPROVAL-001`.
8. A new commit after Review is detected as stale Review approval.
9. A backlog Issue whose dependencies are complete is detected after the configured threshold.
10. The Observer classifies a product-code defect as `PROJECT_DEFECT` and does not create a workflow Maintenance Change.
11. A confirmed workflow defect creates exactly one Maintenance Change with Plan, Implementation, Canary and Rollout stages and correct initial statuses.
12. Release/audit tooling rejects a maintenance Review authored by the Maintainer, by an unknown Agent or against a stale commit.
13. A release tag whose commit is not the exact merge commit of the selected closed PR, or lacks green CI, is rejected.
14. An urgent Incident expands to another requirement during cooldown and still notifies the human approver.
15. Routine redaction removes nested secrets and records a source reference when evidence cannot be copied safely.
16. A v3 child with missing protocol inherits its top-level requirement; an explicit v2/v3 child conflict is blocked and reported.
17. A same-release unresolved recurrence reopens the Incident; a post-fix/later-release recurrence creates a linked new Incident.
18. Reopening an unresolved same-release Incident reuses one existing Maintenance Change tree whether it is in progress, blocked or previously closed.
19. A same-name unmarked Operations Project requires `--adopt`; multiple candidates block.
20. Same-name unmarked control-plane Agents and Skills require explicit adoption; multiple candidates block planning.
21. An unrelated Autopilot trigger is preserved and a managed schedule-to-webhook replacement is blocked.
22. Autopilot create/update/get round-trips every managed field; unsupported, omitted or unverifiable fields block planning.
23. Direct assignment of a todo Incident to the non-Squad Observer enqueues it with the required Issue context.
24. RC deployment creates only reviewed control-plane objects and expected updates; unrelated workspace objects remain unchanged.
25. A second Plan after RC Apply reports only `NO_CHANGE`.
26. The Observer manually validates the injected defect is fixed before the Incident closes.
27. An existing Issue without protocol metadata continues under v2 core semantics after v3 Agent deployment.
28. The explicit disable-operations Plan pauses the Autopilot and detaches reporting capability before v1.0 rollback.
29. Rollback is blocked or explicitly degraded when active v3 requirements exist.
30. The actual v1.0 reconciler reports no change for its managed scope in a fake workspace containing paused v1.1 control-plane objects.
31. Checkout of `v1.0.0` followed by an approved rollback Plan restores the v2 managed scope without deleting Incident history.
32. Missing canary Workspace is reported as a blocker; the reconciler never invents a workspace-create command.
33. Observer run history older than the SLO is detected by `workflow.py health` from an external operator context.
34. `workflow.py health` returns non-zero with a clear diagnosis when authentication fails, the managed Autopilot is missing/ambiguous, run history cannot be read or coverage is incomplete.

## 17. Review Focus

The Plan Review Loop must explicitly assess:

- alert storms, duplicate reports and notification cooldown;
- Observer outage and report-failure recovery;
- false-positive routing and product/workflow boundary;
- privacy of run messages and attached evidence;
- command-level permissions versus prompt-only governance;
- idempotent Project, Autopilot and trigger reconciliation;
- v2 in-flight compatibility under in-place Agent updates;
- canary isolation and rollback rehearsal;
- release provenance without private branch protection;
- cost and Runtime load from hourly audits;
- rollback behavior for objects unknown to v1.0.

## 18. Default Decisions

Unless review finds a decision-bearing issue, v6 uses these defaults:

- Independent control-plane Agents, no second Squad.
- Dedicated `工作流运维` Project.
- Hourly `run_only` audit in `Asia/Shanghai`.
- OpenCode Observer and Reviewer; Codex Maintainer.
- Observer Skill attached to the seven development Agents for Reporter Mode.
- No full run-message reads during routine scans.
- No automatic source fix, release or Apply.
- No destructive prune/delete/archive in v1.1.
- Existing missing protocol metadata means v2.
- Production T0 is not the first canary target.
- Stable release and Workspace deployment use separate approval gates.

## 19. Approval

Implementation begins only after this Plan completes Review Loop and receives:

```text
APPROVE WORKFLOW PLAN v6
```
