# Multica Development Workflow

Git-managed desired state for the Multica `开发交付小队` and its reusable Skills.

Protocol v4 deliberately keeps the system small:

- seven ordinary development Agents, including independent Plan and Code Reviewers;
- no Observer, maintenance-specific Agent, scheduled scan, or background maintenance loop;
- no secure execution environment; Git branch/worktree isolation is a configurable delivery policy rather than a security boundary;
- event-driven workflow Incidents created only when a discovered problem must survive the current task;
- Incident fixes tracked by external `incident_fix_requirement` records and executed outside this development Squad, with legacy ordinary Requirement links still supported;
- workspace deployment from either a reviewed clean Git checkout or a verified formal Release Bundle;
- optional formal releases created directly from a reviewed clean `main` checkout.

Each managed development Agent also receives `multica-delivery-policy`. A product repository may commit `multica.delivery.json` to constrain and default its checkout topology, Task PR, Requirement PR, and direct-target-push capability. A new Requirement Plan freezes only `plan_revision`, a compact versioned policy digest, and `target_branch`; current policy and remote capability are re-resolved when the digest is verified.

Without project configuration, a supported GitHub remote resolves to `lightweight`, Task PR disabled, and Requirement PR enabled. Without any remote, both PRs are disabled and delivery remains local. An unsupported or ambiguous remote blocks planning until the project declares a valid policy. `branch_only` and `lightweight` are serial and require the acting Agents to share the same repository filesystem; `isolated` permits independent Task worktrees and DAG parallelism. Review, tests, human approval, and merge evidence remain mandatory in every mode.

When a reviewed workspace Plan is applied, previously managed Agents, Skills, and scheduled automations that are absent from v4 desired state are retired. Retired Agents are removed from the managed Squad roster before archival. Historical Projects and their Issue records are preserved; a Project led by a retiring Agent is reassigned to the current development leader first.

The rationale and exact boundaries are recorded in [docs/workflow-design.md](docs/workflow-design.md).

## Install

```text
python -m pip install -r requirements.txt
```

## Local State Layout

Machine-level configuration lives under the current user's Home directory:

```text
~/.multica/profiles/
~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json
```

Profiles select the Multica server and authentication context. Runtime maps select concrete Runtime UUIDs for one Workflow and Workspace, so every source copy of the same Workflow uses the same local selection.

Deployment-source generated state remains beside the Git checkout or extracted Release Bundle:

```text
<source>/.multica/plans/
<source>/.multica/journals/
<source>/.multica/deployments/
<source>/.multica/release-plans/
<source>/.multica/worktrees/
<source>/exports/
```

These files bind the immutable source identity, Plans, Apply execution, deployment evidence, releases, worktrees, or diagnostic snapshots to this source directory. They are never part of a formal Release Bundle or committed to Git.

By contrast, `multica.delivery.json` belongs to an application repository that opts into explicit delivery policy. It is portable, reviewed project source and must not be stored under the ignored `.multica/` generated-state directory.

## Deploy a Checkout or Release Bundle

A Git checkout does not need a tag or GitHub Release, but it must be clean. A formal repository ZIP may instead be extracted and deployed directly without `.git`; its internal `release-manifest.json` binds the release tag, source commit, and SHA-256 hash of every published file. Any changed or undeclared workflow file blocks planning or Apply.

```text
python scripts/workflow.py doctor
python scripts/workflow.py export
python scripts/workflow.py plan --adopt
```

Review the generated actions, then approve that exact digest:

```text
APPROVE WORKFLOW PLAN <short-digest>
python scripts/workflow.py apply --plan <plan-file> --approve <short-digest>
python scripts/workflow.py verify
```

After resolving the target Workspace, `doctor`, `plan`, `drift`, and `verify` automatically use `~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json`; `apply` uses the exact path and hash stored in its reviewed Plan. Workflow and Workspace namespacing prevents unrelated projects or Workspaces from sharing Runtime identities while allowing Git checkouts and extracted Release Bundles to share the intended machine-level selection. If the file is absent, provider selection is automatic and succeeds only when every required provider has a single online Runtime. Use `--runtime-map <path>` only for an explicit override.

Use `--deployment-profile codex-only --rebind-runtimes` only for an intentional Runtime-provider change. Runtime bindings select available execution providers; they are not a security-isolation boundary.

## Workflow Incidents

Every development Agent receives `multica-workflow-incidents`. It is invoked at these event points:

1. immediately after a managed workflow Issue is created, to bind protocol metadata;
2. when an Agent or the host discovers a workflow defect that cannot be safely resolved inside the current task and needs durable tracking;
3. when an external Project and optional external owner are known, to explicitly and idempotently create an `incident_fix_requirement` outside the development Squad;
4. when recovering or linking either the external record or a previously existing legacy ordinary Requirement;
5. after the fix is deployed and verified, to record failed verification or close the Incident with mode-specific immutable evidence.

`report` never creates a fix record automatically. New external fix records remain in `backlog`, have no development-tree root metadata, and do not enter Plan, implementation, Code Review, integration, or final Requirement approval.

There is no polling or low-frequency fallback scan. See [docs/incident-runbook.md](docs/incident-runbook.md).

## Formal Release

Formal publishing is optional for workspace deployment. From a reviewed clean `main` checkout:

```text
python scripts/release.py doctor
python scripts/release.py plan --version <version>
# Optional local asset preview:
python scripts/release.py package --plan <plan-file>
python scripts/release.py publish --plan <plan-file>
git fetch --tags
python scripts/release.py verify-tag --tag <v-version>
```

`publish` rebuilds the digest-bound assets and uses the authenticated human host's `gh release create`; the separate `package` command is only a local preview. The repository ZIP contains only the workflow manifest and schema, deployment profiles, runtime instructions, required deployment scripts, active Skills, version and dependency declaration, plus `release-manifest.json`. It excludes `.github`, tests, top-level development documentation, and release-only tooling, and is directly deployable after extraction without cloning the source repository. See [docs/release-policy.md](docs/release-policy.md).
