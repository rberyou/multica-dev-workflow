# Protocol v4 Design

## Objective

Keep ordinary development governance while removing a maintenance control plane that duplicated the active Codex task's ability to notice and fix problems.

## Managed Runtime Objects

- seven development Agents;
- one development Squad with one human approver;
- reusable requirement-intake, delivery-policy, workflow-manager, workflow-incidents, and workflow-console Skills;
- one `工作流问题` Project for durable Incidents.

The desired state contains no scheduled automation. Plan Reviewer and Code Reviewer remain because they provide independent review inside the normal development flow; maintenance-specific roles do not.

## Configurable Development Delivery

Protocol v4 keeps Git, independent Review, tests, Plan approval, final human approval, and merge traceability mandatory while allowing projects to change the cost of checkout isolation and PR transport.

An application repository may commit `multica.delivery.json`. The portable Delivery Policy Skill validates that file, selects a deterministic remote without exposing its URL, resolves the effective Requirement configuration, and produces a compact `v3.sha256:<digest>`. A new Plan freezes only `plan_revision`, `policy_digest`, and `target_branch`; Resolver diagnostics and complete policy/capability JSON are not Plan state. The digest binds normalized project policy, selected remote identity and semantic capability, and the actual workspace/Task PR/Requirement PR choices. A changed bound input invalidates approval and requires a new Plan Review Loop; Agents never change modes automatically. Resolver implementation changes with an identical digest continue. Existing approved schema-v1/v2 Plans retain their old complete-snapshot validation until a material revision migrates them to the compact contract.

The human-readable Plan design is limited to the problem and root cause, selected solution and important tradeoffs, interface/data/user-visible changes, acceptance and tests, risks and rollback, and unresolved human decisions. Independent Plan Review and `APPROVE PLAN vN` remain authoritative in platform comment history rather than duplicated Plan metadata.

All modes retain Requirement and Task branches:

- `branch_only` uses the existing checkout and a repository-wide serial lease;
- `lightweight` uses one Requirement worktree and a Requirement-wide serial lease;
- `isolated` uses a Requirement worktree plus Task worktrees and permits DAG parallelism.

Before a non-isolated acquire, Delivery Policy validates a fresh complete domain containing every terminal Requirement's unique Implementation authority and final integration-validation mirror plus current claims. Ordinary Task lease fields are residue, not additional authorities. A pair that is already canonical released is `canonical`; a fully evidenced historical terminal pair may be `retired_terminal_compatible`, including an authority/mirror state mismatch such as `released` and `held_by_integrator` when both point to the same done inactive holder. This is only a read-time occupancy decision: it emits no writes, checkpoints, or transition records and never changes historical Plan, Review, delivery, Issue status, or lease metadata. Any held/unknown/active tuple, endpoint ambiguity, stale evidence, unsafe checkout, or current claim blocks acquisition. New writers use only canonical `held` and `released` tuples.

Task PR and Requirement PR are independent selections constrained by project and remote capability. When a PR is disabled, immutable Git base/head SHAs, independent Review, tests, approval comments, merge method, and merged commit replace PR evidence. A repository without any remote performs no push, PR, or remote CI operation; an unsupported remote requires explicit local/direct-push project policy. This checkout isolation is not a secure execution environment.

Protocol v4 assigns parent status by workflow object. Plan and Implementation become `done` when their internal work closes. Only the top-level Requirement becomes `in_review`, after Implementation is `done` and Leader opens a final approval gate bound to the current Plan revision, reviewed head, and policy digest. Final approval is accepted only on that root Issue. Integrator records approval and delivery evidence, explicitly wakes Leader, and never writes the root status; Leader alone converges delivery to `done`. Generic platform Stage comments are event notifications and cannot override these semantics or reopen a completed Requirement.

Requirement delivery targets the Plan-defined branch, which may be non-default. Terminal verification preserves independent default and target baselines and covers Requirement PR, direct-push, and no-remote local-only evidence. Complete delivery and handoff evidence are stored in two versioned scalar records rather than expanded fields, keeping real Requirements below the platform's 50-key metadata limit. Duplicate approvals never repeat a merge; when delivery is complete but the root remains non-terminal, they recover the Leader handoff.

## Event-Driven Incident Boundary

The active Agent already observes the commands, state transitions, Review results, and failures involved in its task. It should repair local mistakes immediately. Durable recording is only useful when the issue outlives that execution context.

The Incident Skill therefore combines:

- written instructions for judgment and escalation;
- a deterministic Python command for binding, redaction, deduplication, state changes, source blocking/restoration, and evidence persistence.

The Python command is called by an ordinary Agent or human host at the moment an ordinary development Issue is created, a durable workflow problem is found, an external fix record is explicitly created or linked, a legacy ordinary fix is linked, or a deployed fix is checked. It is never called by a timer. `report` itself does not manufacture a fix record.

## Fix Flow

```text
discovery -> Incident(open) -> external incident_fix_requirement(backlog)
          -> external owner/process -> deployed and verified -> Incident(closed)
```

The external fix record is a verifiable Requirement-shaped Issue, but it has no development-tree root or protocol metadata and does not trigger Leader, Planner, Integrator, implementation, Code Review, integration validation, or final Requirement approval. It must use an explicitly selected external Project and must not be assigned to the managed development Squad. Existing Incidents already linked to ordinary protocol-v4 Requirements retain their legacy Review Loop and close gate. A narrow explicit independent-remediation close exists only when such a legacy fix was cancelled: the link remains immutable, the Incident must still be `in_fix`, and typed immutable fix/deployment evidence replaces neither the record nor its history. Closure restores only directly owned blocked sources and never traverses descendants. There is still no Observation inbox, maintenance decision object, maintenance case, maintenance-only role, scheduler, or secure runtime.

## Deployment and Release

Workspace deployment consumes either a clean Git checkout or an extracted, verified formal Release Bundle and remains digest-approved and stale-state checked. A Git source is identified by its commit and clean-worktree state. A Release Bundle is identified by the digest of its internal `release-manifest.json`, which records the provenance commit and SHA-256 hash of every published file; deployment does not require `.git`. Runtime UUID selection is machine-level configuration stored at `~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json`. Workflow and Workspace namespacing prevents unrelated workflows or Workspaces from reusing Runtime identities, while multiple source copies of the same workflow share the intended local selection.

The same deployment Plan owns machine-global Skill publishing. Every manifest Skill whose `targets` includes `local` is desired as a physical directory copy under the resolved `~/.agents/skills` root. The Plan digest binds that root, sorted source file digests, observed target type (`missing`, `copy`, `symlink`, `junction`, or `foreign`), target content digest, and action. Apply uses staged sibling copies, exact digest verification, rollback backups, path-containment checks, and the existing journal before final deployment evidence is written. Verify treats any link as drift even when its resolved content matches. Only `SKILL.md` with the exact name plus `managed_by=multica-dev-workflow` and `workflow_id=development-delivery` establishes ownership, so foreign same-name Skills are never adopted, overwritten, or deleted. Previously managed links migrate to physical copies; retired owned copies may be removed.

Source-local execution artifacts remain under `<source>/.multica/`: deployment Plans, Apply journals, immutable deployment evidence, formal release Plans, and local worktrees. Diagnostic snapshots remain under `<source>/exports/`. Generated directories are excluded from Release Bundle verification, while changes to published workflow files invalidate the source. Local Skill evidence stores names, actions, and content digests rather than Skill contents or unnecessary environment data. This keeps portable source separate from both machine configuration and execution evidence.

A formal Release is a separate optional packaging action from clean `main`. It creates Skill archives, a directly deployable allowlisted workflow archive with an internal release manifest, checksums, a tag, and a GitHub Release through the authenticated human host. CI configuration, tests, development-only documentation, and release tooling are not deployment inputs and are excluded from that archive.

## Compatibility

Protocol v4 does not execute or infer older workflow protocols, metadata contracts, release evidence, maintenance records, or development-delivery defaults. Unconfigured application repositories immediately use the current delivery defaults. The reconciler recognizes existing ownership markers only for one-way cleanup: it removes retired roster bindings and automations, reassigns preserved historical Projects, archives retired Agents, and deletes retired Skills. All active objects are then rebuilt or adopted from current v4 desired state.
