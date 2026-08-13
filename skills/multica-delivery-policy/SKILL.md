---
name: multica-delivery-policy
description: Resolve and validate project delivery policy plus protocol-v4 final approval and delivery convergence. Use when a managed development Agent must select workspace isolation, Task PR, Requirement PR, repository capability, final approval gates, or local/remote delivery evidence for a Requirement.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.6
---

# Multica Delivery Policy

Use this Skill before approving a Plan and again before implementation or merge operations. It does not choose or change a mode autonomously; it validates the project policy and the exact selection requested by the Plan.

## Resolve the Plan Policy

From the product repository:

```text
python <this-skill>/scripts/delivery_policy.py resolve --repo <repo>
```

Pass `--workspace-mode`, `--task-pr`, or `--requirement-pr` only when the Plan explicitly selects a non-default value. The Resolver returns diagnostics plus a `v3.sha256:<digest>` value. Freeze only `plan_revision`, `policy_digest`, and `target_branch` in a new Plan; do not copy the complete Resolver JSON into the human-readable Plan or Plan metadata. Preserve the approved digest in every descendant Issue.

An absent `multica.delivery.json` uses the protocol defaults. A repository without any remote automatically resolves both PR values to `disabled`. An explicit or required PR selection without capability is an error, never an automatic fallback. A remote that is not supported for PRs requires explicit project policy for local/direct-push delivery.

## Recheck a Compact Frozen Plan

```text
python <this-skill>/scripts/delivery_policy.py verify-approved \
  --repo <repo> --policy-digest <digest>
```

The command re-resolves the current repository and enumerates the finite valid workspace/PR selections. Exactly one digest match returns the actual selection for propagation. No match, ambiguity, an unsupported future digest prefix, or a real project-policy/remote/capability/selection change requires a new Plan revision, independent Review, and `APPROVE PLAN vN`. A Resolver implementation change with the same digest continues.

Existing approved schema-v1/v2 Plans remain immutable and continue to use their stored complete snapshot:

```text
python <this-skill>/scripts/delivery_policy.py verify --repo <repo> --snapshot <file>
```

Do not rewrite those Plans in place. If their design changes materially, migrate the new revision to the compact contract.

## Guard a Checkout

Before switching, editing, reviewing, or merging in a non-isolated checkout:

```text
python <this-skill>/scripts/delivery_policy.py guard-workspace \
  --repo <checkout> --workspace-mode <branch_only|lightweight|isolated> \
  --expected-branch <branch> --expected-head <full-sha>
```

The command blocks dirty workspaces, detached HEAD, unfinished Git operations, unexpected branches, and unexpected commits. Never repair a failed guard with stash, reset, clean, force checkout, or by committing unknown changes.

## Recover Legacy Terminal Leases

Before acquiring a non-isolated lease, fresh-read the complete authority-domain batch and run `lease-transition --action acquire-preflight --snapshot <fresh-batch.json>`. `recovery_required` never writes or acquires. Run `normalize-legacy-terminal` explicitly and apply only its one ordered metadata write, then fresh-read the full batch and repeat until every whitelisted terminal group is complete.

The validator never assumes platform metadata CAS. It binds each step to endpoint and inventory digests, checks UTF-8 scalar and total metadata byte capacity, and leaves Issue status, Plan, Review, approval, merge, and delivery evidence unchanged.

## Validate Final Approval and Delivery

Before opening the final approval gate, accepting final approval, recording delivery, handing off to Leader, or completing the Requirement, normalize the current canonical evidence and run:

```text
python <this-skill>/scripts/delivery_policy.py final-gate \
  --action <open|approve|delivery|handoff|converge> --snapshot <file>
```

Apply only the returned metadata and status writes. A rejected transition writes nothing. Repeated approval never requests another merge.

Every final-gate snapshot includes the root's fresh `metadata_keys` inventory so projected writes can be rejected before exceeding Multica's 50-key limit. Delivery and handoff return versioned scalar `delivery_evidence_record` and `delivery_handoff_record` values. Store each returned string exactly as one metadata key; never expand or manually encode the record.

Read [policy-contract.md](references/policy-contract.md) for configuration, remote capability, selection, workspace lease rules, and the [compact Plan schema](references/plan-policy.schema.json). Read [lease-transition-contract.md](references/lease-transition-contract.md) and its [snapshot schema](references/lease-transition.schema.json) before lease acquisition or terminal compatibility recovery. Read [evidence-contract.md](references/evidence-contract.md) before Review or merge evidence. Read [final-approval-contract.md](references/final-approval-contract.md) before opening or processing final approval, delivery handoff, recovery, or Requirement convergence.
Read [resolver-contract.md](references/resolver-contract.md) before validating compact digests or migrating legacy schema-v1/v2 Plans.
