# Release Policy

Current scope is Phase 1. A new feature is released from its completed top-level Requirement. An Observer Incident fix is released from its approved Maintenance Case, which must link to the ordinary development Requirement and final integration-validation Issue. Neither path requires a Workflow Maintainer or Maintenance Reviewer.

Release planning binds:

- VERSION, workflow and current Phase 1 Skill versions;
- source commit exactly equal to the current `origin/main` tip;
- exact merged Requirement PR head and merge commit;
- green validation run for the merge commit;
- tracked source hash and Changelog section;
- the final `integration_validation` Issue, managed Code Reviewer identity, exact review comment ID, Plan revision and reviewed head SHA;
- exact Multica human release approval on the selected Requirement or Maintenance Case;
- reviewed Dispatcher and Publisher App installations;
- protected GitHub Environment, workflow run, independent human reviewer and publish gate.

For an Incident fix, the Maintenance Case must additionally bind its Incident, maintenance-intake digest, human maintenance-decision comment, ordinary-development executor and `implementation_issue_ids`.

Required sequence:

```text
python scripts/release.py plan --version <version> --development-issue <requirement-or-maintenance-case> --implementation-provenance <integration-validation-issue>
APPROVE WORKFLOW RELEASE <short-digest>
$env:GH_TOKEN = <short-lived Dispatcher App installation token>
python scripts/release.py doctor
python scripts/release.py approval-block --plan <file>
python scripts/release.py apply --plan <file> --approve <short-digest>
Remove-Item Env:GH_TOKEN
```

`--maintenance-issue` remains a CLI alias for historical scripts, but Phase 1 documentation and new automation use `--development-issue`.

The ordinary development workflow owns implementation, PR, CI and commit-bound Code Review. The final Code Reviewer comment must start with `APPROVED` and contain exactly one `plan_revision=<value>` line and exactly one `reviewed_commit_sha=<full-head-sha>` line. The integration-validation metadata records the comment ID, reviewer ID, PR head, PR number and merge commit. Any SHA, Plan revision, reviewer or evidence change invalidates the release Plan.

`doctor` requires an explicit short-lived Dispatcher App installation token in `GH_TOKEN`. The token must match reviewed evidence exactly: Actions write, Contents read, Metadata read, selected-repository scope, and only this repository. It verifies the public repository, owner/default branch, the `workflow-release` Environment, main-only branch policy, self-review prevention, explicit human reviewers, Publisher App evidence and the active `workflow-release-tags` ruleset.

`approval-block` is read-only. It revalidates Multica evidence and prints the bounded Release Request summary. It does not generate an approval comment.

`apply` verifies the exact Plan, local source state, CI, Multica evidence and Dispatcher installation, writes a local request record and dispatches `.github/workflows/release.yml`. It cannot create, delete or push tags and cannot publish a Release.

The release workflow validates the request with a read-only built-in token and requires the reviewed Dispatcher App actor. The publish job is protected by the `workflow-release` Environment. After human approval it records a digest-bound gate, mints a short-lived Publisher App token, creates the annotated tag through the Git database API and publishes only the approved Phase 1 assets:

- requirement-intake Skill;
- workflow-manager Skill;
- workflow-observer Skill;
- workflow-console Skill;
- complete repository bundle;
- SHA256 checksums.

Maintainer Skill and Secure Runtime binary packages are future-component artifacts and are not part of the Phase 1 release asset set. Their source remains in the repository, but its validation belongs to a separately reviewed future-component change and must not become a Phase 1 release gate.

Direct `v*` tag pushes do not trigger publication and are rejected by the tag ruleset for ordinary users, Dispatcher credentials and the built-in workflow token. New tags bind the Release Request digest, Plan digest, source commit, implementation provenance, CI run, expected assets, Multica approval commitments, Dispatcher installation, workflow run ID, Environment approval, publish gate and Publisher installation.

`verify-tag` rechecks the protected Environment approval and exact provenance. Legacy RC1-RC4 maintenance annotations remain verifiable as historical evidence but do not require Phase 1 to deploy their Maintainer/Reviewer control plane.

Release recovery keeps the immutable tag and its original publish-gate digest. A recovery run must reuse the exact Release Request digest and source commit, receive a new protected Environment approval, and bind its new gate before publishing missing approved assets. With the short-lived Dispatcher token set, re-dispatch the embedded Request from an existing tag or a durable local Request file:

```text
python scripts/release.py recover --tag <v-version>
python scripts/release.py recover --request <release-request.json>
```

Recovery refuses an altered Request or unexpected Release assets. When the GitHub Release already exists, the workflow may replace only the approved Phase 1 assets so an interrupted upload can complete safely.

Publishing a Release does not authorize Multica Apply. Each Workspace requires a separate digest-bound deployment Plan and approval.
