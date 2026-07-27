# Release Policy

Workspace deployment and formal release are separate operations.

## Workspace Deployment

Any reviewed clean Git checkout may be deployed with `workflow.py plan`, explicit digest approval, `apply`, and `verify`. A branch may be ahead of `main`; no tag or GitHub Release is required. The default Runtime map is selected after Workspace resolution from `.multica/runtime-maps/<workspace-id>.json`; `--runtime-map` remains an explicit override. The Plan binds the exact source commit, source hash, Runtime map, workspace, and observed state.

## Formal Release

A formal release is optional distribution of the current workflow source and active Skills. It must be created from a reviewed clean `main` checkout.

Release planning verifies:

- requested version equals `VERSION`, `workflow.version`, and every active Skill version;
- `CHANGELOG.md` contains the exact version heading;
- the current branch is `main` and the worktree is clean;
- the source commit and exact asset names are digest-bound in the Plan.

Required sequence:

```text
python scripts/release.py doctor
python scripts/release.py plan --version <version>
python scripts/release.py publish --plan <file>
git fetch --tags
python scripts/release.py verify-tag --tag <v-version>
```

Optionally run `python scripts/release.py package --plan <file>` before publishing to preview the assets locally. `publish` verifies the Plan again and rebuilds those assets; it does not consume evidence from an earlier `package` command.

The release contains one archive for each Skill in `workflow.json`, one repository archive, and `checksums.txt`. Skill packaging uses tracked and non-ignored source files, so local ignored caches or credentials cannot enter an asset. `publish` refuses a daemon-managed Agent identity, invokes `gh release create` using the authenticated human host, and automatically marks SemVer prerelease versions as prereleases. Fetch the created tag before local snapshot verification. There is no repository release workflow, deployment environment gate, dispatcher, publisher service, maintenance provenance, or legacy protocol verification.

Publishing does not deploy a workspace. Every workspace still requires its own digest-approved deployment Plan.
