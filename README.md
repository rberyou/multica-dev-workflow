# Multica Development Workflow

Git-managed desired state for the Multica `开发交付小队` and its reusable Skills.

Protocol v4 deliberately keeps the system small:

- seven ordinary development Agents, including independent Plan and Code Reviewers;
- no Observer, maintenance-specific Agent, scheduled scan, or background maintenance loop;
- no secure execution environment or environment-isolation contract;
- event-driven workflow Incidents created only when a discovered problem must survive the current task;
- Incident fixes implemented and reviewed through an ordinary Requirement;
- workspace deployment from any reviewed clean Git checkout, independent of formal releases;
- optional formal releases created directly from a reviewed clean `main` checkout.

When a reviewed workspace Plan is applied, previously managed Agents, Skills, and scheduled automations that are absent from v4 desired state are retired. Retired Agents are removed from the managed Squad roster before archival. Historical Projects and their Issue records are preserved; a Project led by a retiring Agent is reassigned to the current development leader first.

The rationale and exact boundaries are recorded in [docs/workflow-design.md](docs/workflow-design.md).

## Install

```text
python -m pip install -r requirements.txt
```

## Deploy the Current Checkout

The checkout does not need a Git tag or GitHub Release. It must be clean so the Plan can bind an exact commit and source hash.

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

After resolving the target Workspace, `doctor`, `plan`, `drift`, and `verify` automatically use `.multica/runtime-maps/<workspace-id>.json`; `apply` uses the exact path and hash stored in its reviewed Plan. Each Workspace therefore has an independent local Runtime UUID map. If the file is absent, provider selection is automatic and succeeds only when every required provider has a single online Runtime. Use `--runtime-map <path>` only for an explicit override.

Use `--deployment-profile codex-only --rebind-runtimes` only for an intentional Runtime-provider change. Runtime bindings select available execution providers; they are not a security-isolation boundary.

## Workflow Incidents

Every development Agent receives `multica-workflow-incidents`. It is invoked at four event points:

1. immediately after a managed workflow Issue is created, to bind protocol metadata;
2. when an Agent or the host discovers a workflow defect that cannot be safely resolved inside the current task and needs durable tracking;
3. when the ordinary Requirement that will fix an Incident is known, to link that Requirement;
4. after the fix is deployed and verified, to record failed verification or close the Incident with exact deployment evidence.

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

`publish` rebuilds the digest-bound assets and uses the authenticated human host's `gh release create`; the separate `package` command is only a local preview. No release workflow, protected publishing environment, dispatcher, or publisher service is required. See [docs/release-policy.md](docs/release-policy.md).
