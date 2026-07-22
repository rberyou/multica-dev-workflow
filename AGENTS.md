# Repository Guidelines

## Project Structure & Module Organization

- `scripts/` contains the Python 3.11 reconciler, release tools, contract generators, and packaging; `tests/` contains `unittest` coverage.
- `secure-runtime/` contains the .NET 7 runtime, policies, and security tests.
- `skills/` packages Codex skills. Keep each skill's `SKILL.md`, `agents/`, `references/`, and optional `scripts/` together.
- `instructions/`, `deployment-profiles/`, and `docs/` define roles, deployments, architecture, operations, and release evidence.
- `workflow.json` is desired state and must remain valid against `workflow.schema.json`.

## Current Development Boundary

Phase 1 is the current scope: development Agents report anomalies; Observer scans registered projects, deduplicates findings, manages Incidents, and verifies fixes. Maintainer/Reviewer automation and enforced Secure Runtime isolation belong to later phases; do not activate them without approved scope. See `docs/workflow-maintenance-roadmap.md` and `docs/workflow-maintenance-phase1-design.md`.

## Build, Test, and Development Commands

Install Python dependencies first:

```powershell
python -m pip install -r requirements.txt check-jsonschema
```

Run the main validation loop:

```powershell
python -m unittest discover -s tests -v
python scripts/generate_audit_contract.py --check
python scripts/generate_secure_runtime_manifest.py --check
dotnet build secure-runtime/WorkflowSecureRuntime.sln --configuration Release
dotnet run --project secure-runtime/tests/WorkflowSecureRuntime.Tests/WorkflowSecureRuntime.Tests.csproj --configuration Release --no-build
```

Validate desired-state changes with `check-jsonschema --schemafile workflow.schema.json workflow.json`. Inspect locally with `python scripts/workflow.py doctor`, then `export`, `plan`, and `verify`. Apply only a reviewed plan with matching `APPROVE WORKFLOW PLAN <short-digest>` approval; plan changes invalidate approval.

## Coding Style & Naming Conventions

Use four-space indentation. Follow Python conventions (`snake_case` functions, `PascalCase` classes, explicit imports) and C# conventions (`PascalCase` public members, nullable-aware code). .NET warnings are errors. Match surrounding code, keep JSON deterministic, and never embed local UUIDs, credentials, tokens, or user-specific paths in portable files.

## Testing Guidelines

Name Python files `test_*.py` and methods `test_<behavior>`. Add regression tests beside the affected subsystem, including failure paths for approval, digest, redaction, and policy changes. Tests must be deterministic across Windows, Linux, and macOS. Run both suites before opening a PR.

## Agent Workflow Concepts

**Review Loop:** Review a design or code change, fix every logical flaw, and repeat until no issue remains or human judgment is required. Return ordinary findings to the author automatically; never request user confirmation between cycles.

## Commit & Pull Request Guidelines

Prefer short, imperative Conventional Commit subjects such as `fix: harden release verification` or `docs: record evidence`. Complete `.github/pull_request_template.md`: summarize the change, identify plan/Incident and protocol impact, list validation, bind independent review to the current head SHA, and document risk, canary, and rollback. Link issues; add screenshots only for UI or console-output changes.
