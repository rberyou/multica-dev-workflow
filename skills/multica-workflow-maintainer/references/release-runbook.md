# Release Runbook

The release Plan binds version, selected merged PR, reviewed PR head SHA, exact merge commit, green validation run, Changelog, expected assets and the bounded Maintenance Implementation Review.

Implementation, the single PR, CI and commit-bound Review may be completed while the repository remains private and the development owner credential is still available. Before changing repository visibility or configuring the release Environment/Ruleset, stop Agent-side administration, remove owner/admin `gh` credentials, owner-capable SSH access and reusable GitHub HTTPS credential-helper entries from Agent and release-capable runtimes, then hand the GitHub administration steps to the human owner outside those runtimes. Resume only with read-only verification credentials after the repository is public and both controls have been created.

Before merge, metadata must contain Maintainer and Maintenance Reviewer IDs, Plan revision, matching `pr_head_sha` / `reviewed_commit_sha`, and `review_comment_id`. Review must be authored by the managed Reviewer before merge and use the exact `APPROVED`, `plan_revision=...`, and `reviewed_commit_sha=...` binding lines.

After merge, record `github_pr_number` and `github_merge_commit_sha`, then run:

```text
python scripts/release.py doctor
python scripts/release.py plan --version <version> --maintenance-issue <T-ID>
```

The durable Multica human approver posts:

```text
APPROVE WORKFLOW RELEASE <short-digest>
```

Then run:

```text
python scripts/release.py approval-block --plan <release-plan>
python scripts/release.py apply --plan <release-plan> --approve <short-digest>
```

`doctor` must verify public repository visibility, owner/default branch, the complete normalized `workflow-release` Environment configuration, and the `workflow-release-tags` ruleset for `refs/tags/v*`, with creation/update/deletion restricted and the GitHub Actions App as the only bypass. It records a disabled admin-bypass readback when GitHub exposes that field, or hashes the unsupported-readback residual risk when it does not. It must fail when owner/admin/reviewer credentials remain visible through `gh`, an inactive account remains switchable but unverified, the active identity has repository `push`, or GitHub SSH/a reusable HTTPS credential helper is present. The dispatcher must be Contents-read-only and hold only the bounded Actions permission needed to trigger the workflow. `approval-block` is read-only. `apply` dispatches a Release Request and cannot mutate tags or Releases. Stop at the protected `workflow-release` Environment. Do not post or synthesize the Environment approval and do not use an approver credential from an Agent runtime.

The isolated GitHub reviewer approves the Environment outside the Agent runtime. `github-actions[bot]` creates the annotated tag, verifies provenance, builds the exact asset set and publishes the prerelease.

If publication fails after tag creation, keep the tag. A recovery dispatch must use the same request digest and source, set `recover_existing_tag=true`, and receive a new protected Environment approval. Never delete, move or overwrite a published tag.
