# Release Runbook

The release Plan binds version, the exact current `origin/main` tip, selected merged PR, reviewed PR head SHA, exact merge commit, green validation run, Changelog, expected assets and the bounded Maintenance Implementation Review.

Implementation, PR, CI and commit-bound Review run only inside the dedicated secure runtime. Maintainer uses the repository-scoped Maintainer App through Broker RPC; Reviewer is tokenless. GitHub App creation, key provisioning, Ruleset/Environment administration and human approval happen outside Agent runtimes.

Before merge, metadata must contain Maintainer and Maintenance Reviewer IDs, Plan revision, matching `pr_head_sha` / `reviewed_commit_sha`, and `review_comment_id`. Review must be authored by the managed Reviewer before merge and use the exact `APPROVED`, `plan_revision=...`, and `reviewed_commit_sha=...` binding lines.

After merge, record `github_pr_number` and `github_merge_commit_sha`, then run:

```text
python scripts/release.py plan --version <version> --maintenance-issue <T-ID> \
  --implementation-provenance <implementation-issue> \
  --implementation-provenance <implementation-issue>
```

The durable Multica human approver posts:

```text
APPROVE WORKFLOW RELEASE <short-digest>
```

Then run:

```text
$env:GH_TOKEN = <short-lived Dispatcher App installation token>
python scripts/release.py doctor
python scripts/release.py approval-block --plan <release-plan>
python scripts/release.py apply --plan <release-plan> --approve <short-digest>
Remove-Item Env:GH_TOKEN
```

The human owner mints the Dispatcher token outside Agent runtimes. Its reviewed installation must be selected-repository only and grant exactly Actions write, Contents read and Metadata read. `doctor`, `approval-block` and `apply` reject a missing token or any App, installation, repository or permission mismatch. The host user's normal `gh` and SSH credentials may remain installed because `GH_TOKEN` is explicit and managed Agents cannot inherit host credentials.

`doctor` verifies public repository visibility, owner/default branch, the normalized `workflow-release` Environment, and the `workflow-release-tags` ruleset attested by reviewed administrator evidence. The sole bypass actor is the dedicated Publisher App. `approval-block` is read-only. `apply` verifies local source and CI state, then dispatches a Release Request without mutating tags or Releases. Because Dispatcher has no Pull Requests permission, the workflow's read-only `validate-request` job rechecks the merged PR before the protected Environment can start. Stop at the protected Environment and never synthesize its approval.

The isolated GitHub reviewer approves the Environment outside the Agent runtime. The workflow verifies the gate with the read-only built-in token, then mints a short-lived Publisher App token. That distinct App creates the annotated tag and prerelease through reviewed REST operations.

If publication fails after tag creation, keep the tag and its original gate annotation. A recovery dispatch must use the same request digest and source, set `recover_existing_tag=true`, and receive a new protected Environment approval. The recovery workflow records a new gate for Release publication without requiring it to replace or equal the immutable tag's original gate. Never delete, move or overwrite a published tag.

RC4 requires exactly two independently reviewed Implementation provenance Issues: the already merged stabilization implementation and the final Secure Runtime implementation. One must bind the final release merge commit and the earlier merge commit must be its Git ancestor.
