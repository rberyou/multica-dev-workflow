# Delivery Policy Digest and Legacy Migration Contract

## Compact v3 digest

New Plans use one self-identifying value: `v3.sha256:<digest>`. There is no separate `policy_digest_schema_version` Plan field.

The SHA-256 payload binds only:

- normalized project delivery policy;
- selected remote identity and semantic capabilities;
- actual `workspace_mode`;
- actual Task PR and Requirement PR selections.

It does not bind Resolver provenance, snapshot record digests, selection sources, derived `parallel_tasks`, derived workspace lease scope, diagnostics, annotations, compatibility aliases, policy-source bookkeeping, or complete Resolver output. Portable compatibility fields are normalized to canonical semantics before hashing (for example, `allow_direct_default_push` becomes `allow_direct_target_push`). Workspace concurrency and lease scope are derived from `workspace_mode` after verification.

The selected remote projection includes presence, configured name, URL fingerprint, provider, PR capability, and direct target-push capability. Raw remote URLs are never emitted.

## Verification

Run:

```text
python <this-skill>/scripts/delivery_policy.py verify-approved \
  --repo <repo> --policy-digest <digest>
```

The validator loads the current normalized project policy and selected remote once, enumerates the finite valid combinations of workspace mode plus two PR booleans, and requires exactly one digest match. A unique match returns the actual selection and its derived concurrency/lease values. No match returns `policy_drift` and `requires_plan_revision=true`. Multiple matches are rejected as ambiguous.

This mechanism correctly restores non-default choices without storing selection metadata in the Plan. It never chooses a different valid combination when the digest does not match.

An unsupported prefix is not recovered or guessed. Future digest schemas require a new Plan revision, independent Review, and `APPROVE PLAN vN`. Resolver implementation changes that preserve the v3 digest require no new Plan.

## Legacy schema-v1/v2 Plans

Existing approved schema-v1/v2 Plans remain immutable. They continue to use their stored complete snapshot and the legacy command:

```text
python <this-skill>/scripts/delivery_policy.py verify --repo <repo> --snapshot <file>
```

The legacy validator retains the schema-v1/v2 provenance, snapshot-record, and explicit pinning/supersession recovery behavior needed to validate those historical contracts. Those fields and recovery records are compatibility-only; do not add them to a new Plan.

Do not rewrite an approved legacy Plan in place. When its design body changes materially, or its policy digest or target branch changes, increment `plan_revision` and create the new revision under the compact v3 contract. Preserve the old Plan and approval comments as history.

## Plan revision and approval

Platform comment history is authoritative for independent Plan Review and `APPROVE PLAN vN`. New Plans do not duplicate `design_digest`, `review_comment_id`, `approval_comment_id`, or `approval_author_id` as Plan freeze fields.

Increment `plan_revision` and repeat independent Review plus human Plan approval when:

- the human-readable design changes materially;
- `policy_digest` changes;
- `target_branch` changes;
- the current validator cannot validate the digest schema.

Do not increment solely because Resolver implementation or diagnostic output changed while the approved v3 digest remains identical.
