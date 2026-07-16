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
- Observer reporting may create/update Incident data only; it cannot release or Apply workflow changes.

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
