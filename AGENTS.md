# Repository Guidelines

## Project Structure & Module Organization

- `scripts/` contains the Python 3.11 reconciler, release tooling, contract generators, and packaging utilities.
- `tests/` contains `unittest` coverage for reconciliation, manifests, observers, packaging, and release controls.
- `secure-runtime/` is the .NET 7 secure agent runtime: production projects live under `src/`, policy files under `policy/`, and the executable security test suite under `tests/`.
- `skills/` packages reusable Codex skills; keep each skill's `SKILL.md`, `agents/`, `references/`, and optional `scripts/` together.
- `instructions/`, `deployment-profiles/`, and `docs/` define role behavior, deployment variants, architecture, operations, and release evidence.
- `workflow.json` is desired state and must remain valid against `workflow.schema.json`.

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

Validate desired-state changes with `check-jsonschema --schemafile workflow.schema.json workflow.json`. For local workflow inspection, use `python scripts/workflow.py doctor`, then `export`, `plan`, and `verify`; never apply an unreviewed plan.

## Coding Style & Naming Conventions

Use four-space indentation. Follow Python conventions (`snake_case` functions, `PascalCase` classes, explicit imports) and C# conventions (`PascalCase` public members, nullable-aware code). The .NET build treats warnings as errors. No repository-wide formatter is configured, so match surrounding code and keep JSON deterministic and consistently ordered. Never embed local UUIDs, credentials, tokens, or user-specific paths in portable files.

## Testing Guidelines

Name Python files `test_*.py` and methods `test_<behavior>`. Add regression tests beside the affected subsystem and cover failure paths for approval, digest, redaction, and policy changes. Tests must remain deterministic and pass on Windows, Linux, and macOS. Run the full Python and .NET suites before opening a PR.

## Agent Workflow Concepts

**Review Loop:** Review a design or code change, fix every logical flaw, and repeat until no issue remains or human judgment is required. Return ordinary findings to the author automatically; never request user confirmation between cycles.

## Commit & Pull Request Guidelines

Prefer short, imperative Conventional Commit subjects such as `fix: harden release verification`, `test: make checks deterministic`, or `docs: record evidence`. PRs must complete `.github/pull_request_template.md`: summarize the change, identify plan/incident and protocol impact, list validation, bind independent review to the current head SHA, and document risk, canary, and rollback considerations. Include linked issues and screenshots only when UI or console output changes.
