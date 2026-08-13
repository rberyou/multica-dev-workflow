# Repository Guidelines

## Project Structure

- `scripts/` contains the Python 3.11 reconciler, release tooling, and Skill packaging.
- `tests/` contains deterministic `unittest` coverage.
- `skills/` packages Codex Skills. Keep each Skill's `SKILL.md`, `agents/`, `references/`, and optional `scripts/` together.
- `instructions/`, `deployment-profiles/`, and `docs/` define the development workflow and host operations.
- `workflow.json` is desired state and must remain valid against `workflow.schema.json`.

## Current Design Boundary

Protocol v4 manages the seven ordinary development Agents, their Squad, reusable Skills, and one workflow Incident project. There is no Observer, workflow Maintainer, maintenance-specific Reviewer, scheduled scan, maintenance case, or secure execution environment. Workflow problems are reported only when discovered during real work. New fixes are tracked as external `incident_fix_requirement` records and are not executed by the managed development Squad; previously linked ordinary Requirements retain their legacy flow.

## Build, Test, and Development Commands

Install dependencies:

```powershell
python -m pip install -r requirements.txt check-jsonschema
```

Run the validation loop:

```powershell
python -m unittest tests.test_manifest tests.test_docs tests.test_delivery_policy tests.test_incidents tests.test_package_skills tests.test_reconcile tests.test_release -v
python -m py_compile scripts/workflow.py scripts/workflow_lib.py scripts/package_skills.py scripts/release.py skills/multica-delivery-policy/scripts/delivery_policy.py skills/multica-workflow-incidents/scripts/incidents.py skills/multica-workflow-console/scripts/workflow_console.py
python -m check_jsonschema --schemafile workflow.schema.json workflow.json
python -m check_jsonschema --schemafile skills/multica-delivery-policy/references/project-delivery.schema.json skills/multica-delivery-policy/references/project-delivery.example.json
python -m check_jsonschema --schemafile skills/multica-delivery-policy/references/plan-policy.schema.json skills/multica-delivery-policy/references/plan-policy.example.json
```

Inspect a Git checkout or extracted formal Release Bundle with `python scripts/workflow.py doctor`, then `export`, `plan`, `drift`, and `verify`. The default machine-level Runtime map is workflow- and workspace-scoped at `~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json`; Plans, journals, deployment evidence, release Plans, and worktrees remain under the selected source's `.multica/`. Use `--runtime-map` only for an explicit override. Deploy reviewed source with `apply` only after the exact `APPROVE WORKFLOW PLAN <short-digest>` approval. Any source, runtime-map, or observed-state change invalidates the Plan.

## Coding and Testing

Use four-space indentation and normal Python conventions. Match surrounding code, keep JSON deterministic, and never embed credentials, tokens, concrete UUIDs, or user-specific paths in portable files.

Name tests `test_*.py` and methods `test_<behavior>`. Add regression coverage for approval, digest, redaction, state transitions, and failure paths. Tests must be deterministic across Windows, Linux, and macOS.

## Review Loop

Review a design or code change, fix every logical flaw, and repeat until no issue remains or human judgment is required. Return ordinary findings to the author automatically; never request confirmation between cycles.

## Commit and Pull Requests

Prefer short imperative Conventional Commit subjects. Complete `.github/pull_request_template.md`, bind independent review to the current head SHA, list validation, and document risk and rollback.
