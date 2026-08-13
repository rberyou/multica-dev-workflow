# Read-Only Workspace Lease Compatibility

`lease-transition --action acquire-preflight --snapshot <fresh-batch.json>` is a read-only validator for `branch_only` and `lightweight` acquisition. It never returns metadata writes, status writes, transition records, checkpoints, normalization commands, or mutation advice.

The inventory domain is `protocol-v4-terminal-authority-mirror-v1`. The caller must independently discover every terminal ordinary Requirement in the workflow instance and select exactly two endpoints per root:

- the unique Issue with `workflow_object_type=implementation` and `workflow_stage=implementation`, as `implementation_authority`;
- the unique final Issue with `workflow_object_type=integration_validation` and `workflow_stage=integration_validation`, as `final_integration_validation_mirror`.

Ordinary development Task metadata is historical residue, not an authority endpoint. The snapshot therefore includes an independently discovered inventory of every `done` root `workflow_object_type=requirement`, its root ID list, and the complete discovered Issue inventory. Endpoint roles are derived from object type/stage plus the explicit final-integration marker, then compared with the selected endpoint role; the validator does not trust an arbitrary role label. Missing or duplicate roots/endpoints, a selected endpoint not present in the discovery inventory, or any unknown authority role makes the inventory incomplete. `inventory_complete=true` is only an assertion and is never sufficient by itself.

Each root, authority, and mirror must be `done`, share the same root and workflow instance, and use the scope derived from the root's workspace mode (`branch_only=repository`, `lightweight=requirement`). `isolated` does not use this compatibility. The root must have no active child, pending Review, or open approval gate. Its current Plan revision, approved policy digest, target branch, Review, approval, merge, and delivery evidence must match the historical record. The recorded checkout/worktree must still exist, be clean, have no unfinished Git operation, and match its branch, full head SHA, and worktree identity.

An endpoint pair is `canonical` only when both tuples are `released` with both owners empty. It is `retired_terminal_compatible` when every non-canonical endpoint has both owner fields, uses one of `developer`, `reviewer`, `integrator`, `held_by_integrator`, or `released`, and all non-canonical endpoints name the same historical holder; the other endpoint may already be canonical released. The holder must be `done`, inactive, in the same root and workflow instance, and match the owner pair exactly. `held_by_integrator` requires holder role `integrator`; role-state values require the matching historical role; `released` may accompany any validated historical Developer, Reviewer, or Integrator. Authority and mirror states may differ, including `released` plus `held_by_integrator`.

`held`, unknown states, a missing owner field, mismatched owners, active holders, dirty or drifted Git state, stale evidence, incomplete root/endpoint/current-claim discovery, and any current claim fail closed. Acquisition is allowed only when every historical group is `canonical` or `retired_terminal_compatible` and the explicitly complete `current_claims` inventory is empty on the same fresh full read.

New writers use only `held` with both owners or `released` with both owners cleared. Read-only compatibility never rewrites historical metadata and does not change the compact Plan contract.
