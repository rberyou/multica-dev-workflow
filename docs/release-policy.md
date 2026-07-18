# Release Policy

Release planning binds:

- VERSION, workflow and all Skill versions;
- exact merged PR head and merge commit;
- green validation run for the merge commit;
- tracked source hash and Changelog section;
- expected release assets;
- verified bounded Maintenance Implementation Review evidence;
- exact Multica human release approval;
- protected GitHub Environment, workflow run, independent reviewer and `github-actions[bot]` operator.

Required sequence:

```text
python scripts/release.py doctor
python scripts/release.py plan --version <version> --maintenance-issue <T-ID>
APPROVE WORKFLOW RELEASE <short-digest>
python scripts/release.py approval-block --plan <file>
python scripts/release.py apply --plan <file> --approve <short-digest>
```

Implementation, PR, CI and commit-bound Review precede privileged GitHub administration. While the repository is still private, the development owner credential may be used for that implementation work. Before making the repository public or creating/changing the Environment or Ruleset, remove owner/admin credentials from Agent and release-capable runtimes and hand those administrative actions to the human owner outside Agent runtimes. Continue only after read-only control verification succeeds.

`doctor` requires the repository to be public and verifies its owner/default branch, the `workflow-release` Environment, main-only branch policy, self-review prevention, explicit human reviewers and the normalized Environment configuration hash. If the GitHub API exposes `can_admins_bypass`, it must be false; otherwise the unavailable readback and residual owner-reconfiguration risk are recorded inside the hashed boundary. It also verifies an active `workflow-release-tags` ruleset for `refs/tags/v*`, hashes its complete normalized rule configuration, and requires creation/update/deletion restrictions with the GitHub Actions App as the sole bypass.

The runtime credential preflight fails closed when an owner/admin or Environment-reviewer account is visible through `gh`, when any inactive configured account remains switchable but cannot be verified without activation, when the active repository principal has `admin`, `maintain` or `push`, or when reusable GitHub SSH or HTTPS credential-helper credentials are available. The dispatch identity must be Contents-read-only (`push=false`) while separately holding only the bounded Actions permission needed to trigger the reviewed workflow. Owner credentials must be removed before repository visibility, Environment, Ruleset, Release or tag mutation; those administrative changes happen outside Agent runtimes.

`approval-block` is read-only. It verifies Multica approval and prints the bounded Release Request summary. It does not generate a GitHub approval comment.

`apply` verifies the exact Plan and Multica evidence, writes a local request record and dispatches `.github/workflows/release.yml`. It must not create, delete or push tags and must not publish a Release.

The workflow validates the request from the exact workflow-dispatch commit with read-only permissions. Its publish job is protected by the `workflow-release` Environment. The required reviewer credential must be unavailable to Codex, OpenCode, Maintainer runtimes and CI. Only the Environment-scoped `github-actions[bot]` token receives `contents: write` and creates the annotated tag and GitHub Release.

Direct `v*` tag pushes do not trigger publication and are rejected by the tag ruleset for ordinary users and Agent credentials. Unauthorized tags are inert and must be reported. New tags bind the release Request digest, Plan digest, source commit, PR, CI run, expected assets, Maintenance provenance commitments, workflow run ID, Environment, approval actor and release operator.

`verify-tag` rechecks the protected Environment approval and exact provenance. Legacy RC1-RC3 annotations remain verifiable as historical evidence but cannot authorize a new release or deployment.

Publishing a Release does not authorize Multica Apply. Each Workspace requires a separate digest-bound deployment Plan and approval.
