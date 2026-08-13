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

The Integrator owns scheduling and maintains `workspace_lease_scope`, `workspace_lease_owner_issue_id`, `workspace_lease_owner_agent_id`, and `workspace_lease_state`. New writers use only `held` with both owner fields or `released` with both owner fields empty. A non-isolated lease is handed from Developer to Code Reviewer to Integrator, never shared. Release it only after the current checkout is clean and at its recorded branch/head. Ambiguous or abandoned leases block until the Integrator validates the checkout and explicitly recovers the lease.

Before acquisition, use the explicit batch contract in [lease-transition-contract.md](lease-transition-contract.md). It recognizes only terminal `developer|reviewer|integrator` role-state tuples and `released` tuples with the same proven historical owner. It selects exactly the Implementation authority plus final integration-validation mirror for every terminal Requirement in the complete domain; ordinary Task residue is not an authority. Multiple eligible groups normalize in stable order without blocking one another, but acquisition waits until all are canonical and a fresh full inventory contains no claim. Unknown legacy authorities, active tuples, unsafe evidence, or insufficient metadata byte capacity fail closed.

For `branch_only`, the Planner and Plan Reviewer also run `guard-workspace` against the existing checkout before Plan approval and record its branch/head. The same guard is repeated before every later handoff. A dirty or unexpected checkout blocks; it is never repaired automatically.

## Plan Freeze

The Resolver may display normalized project policy, a redacted capability view, the effective selection, and diagnostics for the current run. A new or materially revised Plan stores none of that JSON. Its frozen policy contract contains only:

- `plan_revision`;
- `policy_digest`, formatted as `v3.sha256:<digest>`;
- `target_branch`.

The human-readable design contains only the problem/root cause, selected solution and important tradeoffs, interface/data/user-visible changes, acceptance/tests, risks/rollback, and unresolved human decisions. Do not embed the complete Resolver output.

After `APPROVE PLAN vN`, the exact approved digest is propagated to Implementation, development Task, integration-validation, Review, merge, and final approval evidence. A semantic policy change requires a new Plan revision, independent Plan Review, and new human approval. If implementation already started, the Integrator must stop affected Tasks and explicitly re-establish branches, worktrees, leases, and replacement Tasks; no Agent may switch modes in place.

`parallel_tasks` and workspace lease scope are derived from the verified workspace mode. Resolver provenance, selection source, annotations, diagnostics, compatibility aliases, and a complete snapshot record are not frozen inputs. A Resolver-only implementation change that preserves the v3 digest may continue. A future digest schema requires a new Plan rather than cross-schema recovery. Existing approved schema-v1/v2 Plans remain immutable and use their legacy complete-snapshot validator until a material revision migrates them to this contract.
