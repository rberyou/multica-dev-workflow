---
name: multica-delivery-policy
description: Resolve and validate project delivery policy plus protocol-v4 final approval and delivery convergence. Use when a managed development Agent must select workspace isolation, Task PR, Requirement PR, repository capability, final approval gates, or local/remote delivery evidence for a Requirement.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.5
---

# Multica Delivery Policy

Use this Skill before approving a Plan and again before implementation or merge operations. It does not choose or change a mode autonomously; it validates the project policy and the exact selection requested by the Plan.

## Resolve the Plan Snapshot

From the product repository:

```text
python <this-skill>/scripts/delivery_policy.py resolve --repo <repo>
```

Pass `--workspace-mode`, `--task-pr`, or `--requirement-pr` only when the Plan explicitly selects a non-default value. Copy the complete JSON result into the Plan and preserve `policy_digest` in every descendant Issue.

An absent `multica.delivery.json` uses the protocol defaults. A repository without any remote automatically resolves both PR values to `disabled`. An explicit or required PR selection without capability is an error, never an automatic fallback. A remote that is not supported for PRs requires explicit project policy for local/direct-push delivery.

## Recheck a Frozen Plan

Write the approved snapshot to a temporary UTF-8 JSON file and run:

```text
python <this-skill>/scripts/delivery_policy.py verify --repo <repo> --snapshot <file>
```

An exact match continues with the frozen digest. A compatible cross-version digest change first returns `recovery_required`; persist the returned scalar `policy_digest_recovery_record`, obtain independent recovery Review, and rerun with `--recovery-record <file>`. Never replace the approved digest. A real project-policy, selected-remote, provider, semantic-capability, or effective-selection change returns `requires_plan_revision=true`; stop and return to Plan Review.

## Guard a Checkout

Before switching, editing, reviewing, or merging in a non-isolated checkout:

```text
python <this-skill>/scripts/delivery_policy.py guard-workspace \
  --repo <checkout> --workspace-mode <branch_only|lightweight|isolated> \
  --expected-branch <branch> --expected-head <full-sha>
```

The command blocks dirty workspaces, detached HEAD, unfinished Git operations, unexpected branches, and unexpected commits. Never repair a failed guard with stash, reset, clean, force checkout, or by committing unknown changes.

## Validate Final Approval and Delivery

Before opening the final approval gate, accepting final approval, recording delivery, handing off to Leader, or completing the Requirement, normalize the current canonical evidence and run:

```text
python <this-skill>/scripts/delivery_policy.py final-gate \
  --action <open|approve|delivery|handoff|converge> --snapshot <file>
```

Apply only the returned metadata and status writes. A rejected transition writes nothing. Repeated approval never requests another merge.

Every final-gate snapshot includes the root's fresh `metadata_keys` inventory so projected writes can be rejected before exceeding Multica's 50-key limit. Delivery and handoff return versioned scalar `delivery_evidence_record` and `delivery_handoff_record` values. Store each returned string exactly as one metadata key; never expand or manually encode the record.

Read [policy-contract.md](references/policy-contract.md) for configuration, remote capability, selection, and workspace lease rules. Read [evidence-contract.md](references/evidence-contract.md) before Review or merge evidence. Read [final-approval-contract.md](references/final-approval-contract.md) before opening or processing final approval, delivery handoff, recovery, or Requirement convergence.
Read [resolver-contract.md](references/resolver-contract.md) before validating Resolver provenance, digest schemas, cross-version pinning, migration, rollback, or supersession evidence.
