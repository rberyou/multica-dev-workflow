# Release Policy

Workspace deployment and formal release are separate operations.

## Workspace Deployment

Any reviewed clean Git checkout or verified formal Release Bundle may be deployed with `workflow.py plan`, explicit digest approval, `apply`, and `verify`. A Git branch may be ahead of `main`; no tag or GitHub Release is required for that path. An extracted repository Release ZIP needs no `.git`: its internal `release-manifest.json` binds the published files, release tag, and provenance commit. The default Runtime map is selected after Workspace resolution from `~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json`; `--runtime-map` remains an explicit override. The Plan binds the portable source identity, desired-source hash, absolute Runtime-map path, Workspace, and observed state. Plans, journals, and deployment evidence remain local under the selected source directory's `.multica/`.

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

The release contains one archive for each Skill in `workflow.json`, one repository archive, and `checksums.txt`. The repository archive additionally contains `release-manifest.json`, with a SHA-256 hash for every published file and a digest-bound portable source identity. Skill packaging uses tracked and non-ignored source files, so local ignored caches or credentials cannot enter an asset. `publish` refuses a daemon-managed Agent identity, invokes `gh release create` using the authenticated human host, and automatically marks SemVer prerelease versions as prereleases. Fetch the created tag before local snapshot verification. There is no repository release workflow, deployment environment gate, dispatcher, publisher service, maintenance provenance, or legacy protocol verification.

Publishing does not deploy a workspace. Every workspace still requires its own Runtime selection and digest-approved deployment Plan, but the deployment host only needs the extracted repository archive rather than a Git clone.
