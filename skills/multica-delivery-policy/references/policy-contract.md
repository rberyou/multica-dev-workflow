# Project Delivery Policy Contract

## Project File

A project may commit `multica.delivery.json` at its repository root. This is portable source configuration and must not be placed under the generated, ignored `.multica/` directory.

```json
{
  "$schema": "https://raw.githubusercontent.com/rberyou/multica-dev-workflow/main/skills/multica-delivery-policy/references/project-delivery.schema.json",
  "schema_version": 1,
  "workflow_id": "development-delivery",
  "workspace_modes": {
    "allowed": ["branch_only", "lightweight", "isolated"],
    "default": "lightweight"
  },
  "task_pr": {"constraint": "optional", "default": false},
  "requirement_pr": {"constraint": "optional", "default": true},
  "remote": {
    "name": "origin",
    "provider": "auto",
    "allow_direct_default_push": false
  }
}
```

`allowed` is the workspace capability set. `constraint` is `optional`, `required`, or `forbidden`; a required PR has default `true`, and a forbidden PR has default `false`. `allow_direct_default_push` is the portable schema field retained by protocol v4. Its normalized capability is `direct_target_push`: it authorizes a Plan to select direct push for its explicit target branch, including a reviewed non-default branch. It is not permission for an Agent to bypass repository protection.

## Defaults and Remote Capability

Without the file, all workspace modes are allowed and `lightweight` is selected. Task PR defaults to disabled. Requirement PR defaults to enabled only when the selected remote is structurally recognized as a supported GitHub PR remote.

Remote selection uses the configured name, otherwise `origin`, otherwise the only remote. Multiple remotes without `origin` are ambiguous and block planning. The resolver records only a hash of remote URLs, never the URLs themselves.

Repository URL recognition proves configuration, not credentials or current availability. Before push or PR mutation, the acting Agent must verify authentication and remote state. A transient failure after approval blocks the Issue; it never changes the selected mode.

When no selected remote exists, both PR values resolve to disabled unless the Plan or project requires one, in which case resolution fails. A selected remote without a supported PR provider cannot use PR defaults; the project must explicitly select local delivery and declare `allow_direct_default_push=true` when its Plan target branch must be updated remotely. The Integrator must still verify that direct push to the exact target branch is actually permitted before delivery.

## Workspace Modes

All modes keep a Requirement branch and one branch per development or Revert Task. They differ only in checkout topology and concurrency:

- `branch_only`: use the existing project checkout; no workflow worktree is created; all development, review, and integration operations are serial.
- `lightweight`: create one Requirement worktree; serially switch Task branches inside it; development, review, and integration operations are serial.
- `isolated`: create a Requirement worktree and a separate branch/worktree for each Task; independent DAG stages may run concurrently.

`branch_only` and `lightweight` are valid only when the Integrator, assigned Developer, and Code Reviewer can access the same canonical repository filesystem and checkout. The project `allowed` list declares that the project permits these modes; the Planner must also verify the selected Runtime topology. If shared access cannot be proven, block Plan Review. Do not silently replace the selection with `isolated`.

The Integrator owns scheduling and maintains `workspace_lease_scope`, `workspace_lease_owner_issue_id`, `workspace_lease_owner_agent_id`, and `workspace_lease_state`. A non-isolated lease is handed from Developer to Code Reviewer to Integrator, never shared. Release it only after the current checkout is clean and at its recorded branch/head. Ambiguous or abandoned leases block until the Integrator validates the checkout and explicitly recovers the lease.

`workspace_lease_state` is only `held` or `released`. The Implementation Issue is the requirement-scope authority and the active development or integration Issue is its mirror. Both tuples and their `workspace_lease_transition_record` must be treated as one state machine. Acquisition requires a declared-complete inventory of every other Requirement authority/mirror pair, all fully released.

Run `lease-transition --snapshot <file>` after a fresh workspace guard. The zero-write validator binds Workspace, Squad, roster digest, Plan revision, policy digest, guard branch/head, both endpoint IDs, initial/desired tuples, and the mirror's current blocker. Release clears the mirror owners and marks it released before doing the same to the authority; acquire establishes the authority owner/state before the mirror. Every data write is followed by authority and mirror record checkpoints. A retry accepts only an exact prefix, replays the last idempotent write when necessary, and never returns status, `waiting_on`, `blocked_reason`, or Incident-owner writes.

While an Incident owns the active-child blocker, lease transition may change only `workspace_lease_state`, its two owner IDs, and `workspace_lease_transition_record`. Any blocker drift rejects the retry. No next Requirement may acquire until both release endpoints and all checkpoints are complete.

Acquire records bind the normalized other-Requirement inventory and recheck it on every retry; an inventory omission, newly held endpoint, or digest drift blocks before another lease write. A pair of completed endpoint records is idempotent terminal evidence. It may be overwritten only by the next valid held-to-released or released-to-held transition, so checkpoint retention never deadlocks later handoffs.

For `branch_only`, the Planner and Plan Reviewer also run `guard-workspace` against the existing checkout before Plan approval and record its branch/head. The same guard is repeated before every later handoff. A dirty or unexpected checkout blocks; it is never repaired automatically.

## Plan Freeze

The resolver output contains normalized project policy, a redacted capability snapshot, the effective selection, Resolver provenance, a versioned semantic `policy_digest`, and a complete `snapshot_record_digest`. The Planner stores the complete output in the versioned Plan. The Plan Reviewer verifies it independently.

After `APPROVE PLAN vN`, the exact approved digest is propagated to Implementation, development Task, integration-validation, Review, merge, and final approval evidence. A semantic policy change requires a new Plan revision, independent Plan Review, and new human approval. If implementation already started, the Integrator must stop affected Tasks and explicitly re-establish branches, worktrees, leases, and replacement Tasks; no Agent may switch modes in place.

A Resolver-only digest/schema change does not silently replace the approved digest. The explicit pinning and supersession process in [resolver-contract.md](resolver-contract.md) must prove semantic equivalence, preserve the old and current digests plus provenance, and retain the original digest in all descendant evidence. Without the accepted recovery record, verification remains blocked.
