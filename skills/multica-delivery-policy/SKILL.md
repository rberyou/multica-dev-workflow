---
name: multica-delivery-policy
description: Resolve and validate project delivery policy plus protocol-v4 final approval and delivery convergence. Use when a managed development Agent must select workspace isolation, Task PR, Requirement PR, repository capability, final approval gates, or local/remote delivery evidence for a Requirement.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.4
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

Any project-policy, selected-remote, provider, capability, or effective-selection change makes the snapshot stale. Stop and return to Plan Review; do not upgrade or downgrade the mode.

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

Read [policy-contract.md](references/policy-contract.md) for configuration, remote capability, selection, and workspace lease rules. Read [evidence-contract.md](references/evidence-contract.md) before Review or merge evidence. Read [final-approval-contract.md](references/final-approval-contract.md) before opening or processing final approval, delivery handoff, recovery, or Requirement convergence.
