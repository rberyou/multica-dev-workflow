# Delivery Policy Resolver and Digest Contract

## Resolver provenance

Every schema-v2 resolution records `resolver_provenance` with the Resolver ID, Skill package version, SHA-256 implementation digest, snapshot schema version, and policy-digest schema version. Provenance is bound by `snapshot_record_digest`, which hashes the complete canonical snapshot except that digest field itself. It is audit evidence, not a delivery-policy input: a compatible Resolver implementation change may change provenance and the full record digest without changing `policy_digest`.

Legacy schema-v1 snapshots did not declare Resolver provenance or a full record digest. Verification identifies that fact as `provenance_status=legacy_undeclared`; it must not invent a package version. The immutable Plan, workflow Issue metadata, source deployment record, or other external evidence remains the authority for the workflow version that created such a snapshot.

## Stable policy digest schema

`policy_digest_schema_version=2` hashes a fixed semantic projection rather than the complete Resolver output. The projection contains:

- workflow and project-policy source identity;
- normalized project policy, with workspace-mode capability order canonicalized;
- selected remote presence, name, fingerprint, provider, PR capability, and canonical `direct_target_push` capability;
- effective workspace mode, PR selections, concurrency, and lease scope;
- the source of each effective selection.

The legacy `direct_default_push` field is normalized to `direct_target_push`; when both exist they must agree. Resolver diagnostics, provenance, aliases, annotations, and future fields outside the fixed projection do not change `policy_digest`. Adding or removing a semantic field requires a new policy-digest schema version and the recovery process below. Project policy, selected remote identity or fingerprint, provider, semantic capabilities, effective choices, or selection sources still change the digest and invalidate the Plan.

Schema-v2 snapshots carry both digests:

- `policy_digest` freezes the versioned semantic delivery contract across compatible Resolver versions;
- `snapshot_record_digest` proves the complete snapshot and Resolver provenance were not modified.

## Pinning and supersession recovery

Verification never overwrites an approved digest. An exact schema/digest match returns `verification_outcome=exact_match` and continues to propagate the frozen digest.

When the frozen and current snapshots have equal schema-v2 semantic projections but different digest schemas or legacy full-snapshot digests, the first verification returns exit `1`, `verification_outcome=recovery_required`, `requires_plan_revision=false`, and a scalar `policy_digest_recovery_record`. The record binds:

- the immutable frozen snapshot identity and pinned digest;
- the digest that descendants must continue to propagate;
- the current stable digest and semantic digest;
- frozen and current Resolver provenance;
- any legacy current digest superseded because a non-semantic Resolver field changed.

Persist the returned scalar without decoding or rewriting it on the versioned Plan as `policy_digest_recovery_record`, and record the same evidence in the recovery Review comment. An independent reviewer reruns verification with `--recovery-record <file>` containing that scalar. Only a matching, canonical, digest-bound record returns exit `0` with `verification_outcome=pinned_equivalent`. The approved Plan revision, `policy_digest`, Review, and human approval remain unchanged because the semantic contract is unchanged. Descendants continue to use `policy_digest_to_propagate`, which equals the original frozen digest.

If the semantic projections differ, verification returns `verification_outcome=semantic_drift`, no recovery record, and `requires_plan_revision=true`. Reopen the Plan, increment its revision, repeat independent Review, obtain a new `APPROVE PLAN`, and re-establish affected implementation state. A recovery record can never authorize a real policy, remote, provider, capability, or effective-selection change.

Rollback uses the same rule. A newer frozen snapshot may be pinned across an older compatible projection only when the current verifier can validate both schemas, prove semantic equality, and bind an explicit recovery record. Unsupported schemas block; they are never guessed or downgraded.

## Command sequence

Run ordinary verification first:

```text
python <this-skill>/scripts/delivery_policy.py verify --repo <repo> --snapshot <file>
```

For `recovery_required`, save the returned `policy_digest_recovery_record` as a JSON string or plain scalar in a temporary UTF-8 file, persist it on the Plan, obtain independent recovery Review, and rerun:

```text
python <this-skill>/scripts/delivery_policy.py verify --repo <repo> --snapshot <file> \
  --recovery-record <record-file>
```

The recovery file is evidence input, not portable project configuration. Do not commit project-specific digests, repository identities, or Issue IDs to this workflow repository.
