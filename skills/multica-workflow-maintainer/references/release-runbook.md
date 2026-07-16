# Release Runbook

The release Plan binds version, selected merged PR, reviewed PR head SHA, exact PR merge commit SHA, green validation run, changelog, expected assets and the Multica Maintenance Review record.

Before planning, the Maintenance Issue metadata must contain the managed Maintainer, Maintenance Reviewer and human approver IDs, `plan_revision`, `reviewed_commit_sha`, `review_comment_id`, `github_pr_number` and `github_merge_commit_sha`. The Review comment must be authored by the managed Reviewer; its first non-empty line must be exactly `APPROVED`, followed by exactly one `plan_revision=<current>` line and exactly one `reviewed_commit_sha=<full-PR-head-SHA>` line.

Required approval:

```text
APPROVE WORKFLOW RELEASE <short-digest>
```

The durable human approver must post that exact line on the same Maintenance Issue after the release Plan is generated. Then run:

```text
python scripts/release.py approval-block --plan <release-plan>
```

This read-only command verifies the Multica approval and prints the exact GitHub approval block. The immutable workflow-product GitHub approver in `docs/bootstrap-v6.json` must post the complete block on the selected merged PR. It binds the digest to the Maintenance Issue, independent Review comment, Multica approval comment and non-reversible evidence hashes without publishing raw Multica UUIDs. Apply re-reads both systems and rejects missing, stale, mismatched or wrong-author approval. `verify-tag` binds that same block to the tag commit, exact merged-main PR and exact validation run. The one-time `v1.1.0-rc.1` bootstrap uses the durable GitHub record for both the approved v6 Plan and release approval.

The released commit must equal the selected closed PR merge commit targeting `main`. Stable release publication never authorizes Workspace mutation.
