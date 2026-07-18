# Multica Development Workflow

Install the reconciler runtime dependency before using repository commands:

```text
python -m pip install -r requirements.txt
```

Git-managed desired state for the Multica `开发交付小队`, its workflow operations control plane, runtime policies and reusable skills.

The base architecture is recorded in [docs/design-plan-v5.md](docs/design-plan-v5.md). Observer and Maintainer control-plane design is recorded in [docs/design-plan-v6.md](docs/design-plan-v6.md). Multica v0.4.2 Autopilot corrections are recorded separately in [docs/rc2-compatibility-amendment.md](docs/rc2-compatibility-amendment.md) so the immutable RC1 design evidence remains unchanged.

## Safety Model

- Git is desired state; Multica is runtime state.
- `plan` is read-only and produces a digest-bound action file.
- `apply` requires an explicitly approved plan digest and rechecks Git and Multica preconditions.
- Runtime, workspace, agent, squad and member UUIDs remain local.
- Existing unrelated skills and non-conflicting roster members are preserved.
- v1.1 does not prune, destroy or archive managed objects.
- Observer reporting may create/update Incident data only. Maintenance intake is human-gated and later stages are created lazily.
- Local release tooling can validate and dispatch a request but cannot create tags or Releases. Publication runs only behind the protected `workflow-release` GitHub Environment.

## Quick Start

```text
python scripts/workflow.py doctor
python scripts/workflow.py export
python scripts/workflow.py plan --adopt
```

Review the generated plan before applying:

```text
APPROVE WORKFLOW PLAN <short-digest>
```

Then run:

```text
python scripts/workflow.py apply --plan <plan-file> --approve <short-digest>
python scripts/workflow.py verify
```

Audit workflow Issues and Observer health:

```text
python scripts/workflow.py audit --workspace <id-or-slug> --output json
python scripts/workflow.py health --workspace <id-or-slug> --output json
```

Prepare a controlled rollback by disabling operations before checking out an older tag:

```text
python scripts/workflow.py plan --workspace <id-or-slug> --disable-operations
```

See `skills/multica-workflow-manager/SKILL.md` for the external Agent workflow.

Tagged releases publish requirement-intake, workflow-manager, workflow-observer and workflow-maintainer Skills, the complete repository bundle and SHA256 checksums.

Release control is configured in `docs/release-control.json`. RC4 requires the repository to be public and to provide the protected Environment plus the main-only deployment policy and a `refs/tags/v*` ruleset whose only bypass is the GitHub Actions App. Before planning a release, run:

```text
python scripts/release.py doctor
```

The required GitHub Environment reviewer must use a credential unavailable to Agent runtimes. Owner/admin `gh` accounts, any identity with repository `push`, owner-capable SSH access and reusable GitHub HTTPS credential-helper entries must be removed from release-capable Agent runtimes before privileged GitHub changes. The remaining dispatcher is Contents-read-only and carries only the bounded Actions permission required to start the workflow. `release.py apply` dispatches the request; `github-actions[bot]` creates the approved tag and Release after Environment approval.
