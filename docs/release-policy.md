# Release Policy

Release planning binds:

- VERSION, workflow and all Skill versions;
- source commit exactly equal to the current `origin/main` tip;
- exact merged PR head and merge commit;
- green validation run for the merge commit;
- tracked source hash and Changelog section;
- expected release assets;
- verified bounded Maintenance Implementation Review evidence;
- exact Multica human release approval;
- reviewed Dispatcher and Publisher App installations;
- protected GitHub Environment, workflow run, independent reviewer and publish gate.

Required sequence:

```text
python scripts/release.py plan --version <version> --maintenance-issue <T-ID> \
  --implementation-provenance <implementation-issue> \
  --implementation-provenance <implementation-issue>
APPROVE WORKFLOW RELEASE <short-digest>
$env:GH_TOKEN = <short-lived Dispatcher App installation token>
python scripts/release.py doctor
python scripts/release.py approval-block --plan <file>
python scripts/release.py apply --plan <file> --approve <short-digest>
Remove-Item Env:GH_TOKEN
```

Implementation, PR, CI and commit-bound Review run through the dedicated Secure Agent Runtime. The Maintainer receives only Broker RPCs and the Reviewer is tokenless. GitHub App creation, key installation, Environment/Ruleset administration and human approvals remain outside Agent runtimes.

`doctor` requires an explicit short-lived Dispatcher App installation token in `GH_TOKEN`. The token must match reviewed evidence exactly: Actions write, Contents read, Metadata read, selected-repository scope, and only this repository. It then verifies that the repository is public, checks owner/default branch, the `workflow-release` Environment, main-only branch policy, self-review prevention, explicit human reviewers and the normalized Environment configuration hash. If the GitHub API exposes `can_admins_bypass`, it must be false; otherwise the unavailable readback and residual owner-reconfiguration risk are recorded inside the hashed boundary. It also verifies reviewed Publisher App evidence and an active `workflow-release-tags` ruleset for `refs/tags/v*`, with creation/update/deletion restrictions and the Publisher App as the sole bypass.

The human mints the Dispatcher token outside Agent runtimes and removes it after use. Dispatcher private keys must not enter Agent runtimes, repository files, workflow artifacts or the protected Publisher Environment. `GH_TOKEN` takes precedence over stored `gh` credentials, and the exact installation readback is verified before release operations. The host human may retain normal `gh`, SSH, Git config, Credential Manager and browser credentials; Secure Agent Runtime isolation prevents managed Agents from inheriting them.

`approval-block` is read-only. It verifies Multica approval and prints the bounded Release Request summary. It does not generate a GitHub approval comment.

`apply` verifies the exact Plan, local source state, bound CI run, Multica evidence and Dispatcher installation, writes a local request record and dispatches `.github/workflows/release.yml`. The Dispatcher App does not have Pull Requests permission, so the merged PR is rechecked by the workflow's read-only `validate-request` job before any Environment approval or publication can begin. Local apply must not create, delete or push tags and must not publish a Release.

The workflow validates the request from the exact workflow-dispatch commit with the read-only built-in token and requires `github.actor` to equal the reviewed `<dispatcher-app-slug>[bot]` identity. Its publish job is protected by the `workflow-release` Environment. After approval it records a digest-bound gate, mints a short-lived Publisher App installation token, verifies the exact installation/repository/permission contract, creates the annotated tag through the Git database REST API and publishes the exact approved assets. The built-in token never receives `contents: write`.

Direct `v*` tag pushes do not trigger publication and are rejected by the tag ruleset for ordinary users, Dispatcher credentials, Maintainer credentials and the built-in workflow token. Unauthorized tags are inert and must be reported. New tags bind the release Request digest, Plan digest, source commit, both RC4 Implementation records, CI run, expected assets, Maintenance provenance commitments, Dispatcher installation, workflow run ID, Environment approval, publish gate and Publisher installation.

`verify-tag` rechecks the protected Environment approval and exact provenance. Legacy RC1-RC3 annotations remain verifiable as historical evidence but cannot authorize a new release or deployment.

Release recovery keeps the immutable tag and its original publish-gate digest. A recovery run must reuse the exact Release Request digest and source commit, receive a new protected Environment approval, and bind its new gate in the workflow audit output before publishing the missing Release assets.

Publishing a Release does not authorize Multica Apply. Each Workspace requires a separate digest-bound deployment Plan and approval.
