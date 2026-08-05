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

An application repository may commit `multica.delivery.json`. The portable Delivery Policy Skill validates that file, selects a deterministic remote without exposing its URL, resolves the effective Requirement configuration, and produces a policy digest. The versioned Plan stores the complete snapshot. A changed project policy, selected remote, provider, capability, or effective choice invalidates approval and requires a new Plan Review Loop; Agents never change modes automatically.

All modes retain Requirement and Task branches:

- `branch_only` uses the existing checkout and a repository-wide serial lease;
- `lightweight` uses one Requirement worktree and a Requirement-wide serial lease;
- `isolated` uses a Requirement worktree plus Task worktrees and permits DAG parallelism.

Task PR and Requirement PR are independent selections constrained by project and remote capability. When a PR is disabled, immutable Git base/head SHAs, independent Review, tests, approval comments, merge method, and merged commit replace PR evidence. A repository without any remote performs no push, PR, or remote CI operation; an unsupported remote requires explicit local/direct-push project policy. This checkout isolation is not a secure execution environment.

## Event-Driven Incident Boundary

The active Agent already observes the commands, state transitions, Review results, and failures involved in its task. It should repair local mistakes immediately. Durable recording is only useful when the issue outlives that execution context.

The Incident Skill therefore combines:

- written instructions for judgment and escalation;
- a deterministic Python command for binding, redaction, deduplication, state changes, source blocking/restoration, and evidence persistence.

The Python command is called by an ordinary Agent or human host at the moment an Issue is created, a durable workflow problem is found, a fix Requirement is linked, or a deployed fix is checked. It is never called by a timer.

## Fix Flow

```text
discovery -> Incident(open) -> ordinary Requirement -> normal Review Loop
          -> workspace Plan/Apply/Verify -> Incident(closed)
```

There is no Observation inbox, maintenance decision object, maintenance case, or maintenance-only approval. Product-impacting choices still use the ordinary Requirement's human gates.

## Deployment and Release

Workspace deployment consumes either a clean Git checkout or an extracted, verified formal Release Bundle and remains digest-approved and stale-state checked. A Git source is identified by its commit and clean-worktree state. A Release Bundle is identified by the digest of its internal `release-manifest.json`, which records the provenance commit and SHA-256 hash of every published file; deployment does not require `.git`. Runtime UUID selection is machine-level configuration stored at `~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json`. Workflow and Workspace namespacing prevents unrelated workflows or Workspaces from reusing Runtime identities, while multiple source copies of the same workflow share the intended local selection.

Source-local execution artifacts remain under `<source>/.multica/`: deployment Plans, Apply journals, immutable deployment evidence, formal release Plans, and local worktrees. Diagnostic snapshots remain under `<source>/exports/`. Generated directories are excluded from Release Bundle verification, while changes to published workflow files invalidate the source. This keeps portable source separate from both machine configuration and execution evidence.

A formal Release is a separate optional packaging action from clean `main`. It creates Skill archives, a directly deployable allowlisted workflow archive with an internal release manifest, checksums, a tag, and a GitHub Release through the authenticated human host. CI configuration, tests, development-only documentation, and release tooling are not deployment inputs and are excluded from that archive.

## Compatibility

Protocol v4 does not execute or infer older workflow protocols, metadata contracts, release evidence, maintenance records, or development-delivery defaults. Unconfigured application repositories immediately use the current delivery defaults. The reconciler recognizes existing ownership markers only for one-way cleanup: it removes retired roster bindings and automations, reassigns preserved historical Projects, archives retired Agents, and deletes retired Skills. All active objects are then rebuilt or adopted from current v4 desired state.
