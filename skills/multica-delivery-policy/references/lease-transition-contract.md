# Workspace Lease Terminal Compatibility

## Authority Inventory Domain

`lease-transition` consumes a fresh batch snapshot matching [lease-transition.schema.json](lease-transition.schema.json). The inventory domain is `protocol-v4-terminal-authority-mirror-v1`: for every discovered terminal Requirement, select exactly one Implementation Issue as `implementation_authority` and exactly one final integration-validation Issue as `final_integration_validation_mirror`. Independently discovered `discovered_requirement_root_ids` must equal `requirement_root_ids`; those IDs, `migration_groups`, selected `endpoint_inventory`, and the same selected endpoints in `discovered_issue_inventory` must agree exactly.

Ordinary development Tasks may retain historical `workspace_lease_*` fields, but they are residue rather than lease authorities and stay outside `endpoint_inventory`. They remain visible in `discovered_issue_inventory` without an endpoint role. A missing or duplicate selected endpoint, an unknown authority role, or a Requirement omitted from the declared root list blocks; never infer authority from the mere presence of lease metadata.

`current_claims` is a separate fresh inventory of active claims in the same lease domain. Any item blocks the batch. `snapshot_read_id` identifies the caller's read round only. It is not a platform transaction version or compare-and-swap token.

## Compatibility Whitelist

Only these terminal families may normalize:

- `role_state`: state is `developer`, `reviewer`, or `integrator`; owner Issue and Agent are non-empty and equal the same done, inactive historical holder, including its role.
- `released_with_owner`: state is `released`; both owner fields are non-empty and equal the same done, inactive historical holder.

The canonical result is state `released` with both owner fields empty. `held`, `held_by_integrator`, unknown states, missing owners, inconsistent holders, active holders, tuple disagreement, isolated mode, or unsafe evidence block the entire batch.

The root supplies `workspace_mode`; scope is derived as `branch_only=repository`, `lightweight=requirement`, and `isolated=task`. The root does not need a lease-scope metadata field. Both selected endpoints must match the derived scope. Root, authority, and mirror must be `done`, share the root and workflow instance, and retain current Plan, target branch, checkout/head, Review, approval, merge, and delivery evidence. Active children, pending Review, an open approval gate, dirty or unfinished Git state, incomplete delivery, or a current claim block normalization.

Schema-v1/v2 Plans remain readable through their legacy complete snapshots. A v3 Plan still freezes only `plan_revision`, `policy_digest`, and `target_branch`; lease scope and transition state are not added to the Plan or policy digest.

## Explicit Batch Recovery

Ordinary acquisition runs `lease-transition --action acquire-preflight --snapshot <fresh-batch.json>`. If whitelisted groups are pending, the result is `recovery_required` with blocker `legacy_terminal_normalization_required` and summaries for every group. It writes nothing. Acquisition is allowed only after every group is canonical or has matching complete checkpoints and a fresh full reread shows no current claim.

Recovery runs `lease-transition --action normalize-legacy-terminal --snapshot <fresh-batch.json>`. Groups are ordered by root Requirement ID, authority ID, and mirror ID. Each invocation returns at most one conditional metadata write. For a group the legal prefix is:

1. write the same prepared checkpoint to authority;
2. fresh full reread, then write it to mirror;
3. fresh full reread, then canonicalize the mirror tuple;
4. fresh full reread, then canonicalize the authority tuple;
5. fresh full reread, then mark the authority checkpoint complete;
6. fresh full reread, then mark the mirror checkpoint complete;
7. fresh full reread of all declared groups and current claims.

The validator binds each write to the observed endpoint digest and full selected-inventory digest and sets `platform_cas_assumed=false`. The Agent must compare these against its fresh read immediately before writing and reread the full batch afterwards. A changed root/evidence/checkpoint/migration set stops with a stable blocker. Never roll back completed evidence, clear another claim, change Issue status, or acquire a new lease as part of normalization.

Both endpoints use the single scalar key `workspace_lease_transition_record`. The compact record binds group identities, before/after digests, migration-set digest, safety digest, prepared inventory digest, and baseline metadata byte counts. `metadata_total_bytes` is the byte length of the endpoint's current canonical compact UTF-8 JSON metadata object. The snapshot also provides its total metadata limit and scalar-value byte limit. Replacement projection uses canonical JSON entry bytes, including quotes, colon, and a new-entry separator. The validator reports record bytes and projected total; either exceeded limit blocks before mutation. One metadata key is not proof that the scalar fits.

This is a reusable validator contract. A one-time operational migration may consume it, but no particular repository, Issue ID, endpoint UUID, or already deployed fix is assumed.
