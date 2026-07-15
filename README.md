# Multica Development Workflow

Git-managed desired state for the Multica `开发交付小队`, its agents, runtime policies and reusable skills.

The approved architecture and safety contract are recorded in [docs/design-plan-v5.md](docs/design-plan-v5.md).

## Safety Model

- Git is desired state; Multica is runtime state.
- `plan` is read-only and produces a digest-bound action file.
- `apply` requires an explicitly approved plan digest and rechecks Git and Multica preconditions.
- Runtime, workspace, agent, squad and member UUIDs remain local.
- Existing unrelated skills and non-conflicting roster members are preserved.
- v1 does not prune, destroy or archive managed objects.

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

See `skills/multica-workflow-manager/SKILL.md` for the external Agent workflow.
