# Workflow Manager Commands

Install the repository runtime dependency once per Python environment:

```text
python -m pip install -r requirements.txt
```

## First Check

```text
python scripts/workflow.py doctor --deployment-profile quality
```

Stop on missing CLI, authentication, workspace ambiguity, Runtime ambiguity or invalid approver roster.

## Export Existing State

```text
python scripts/workflow.py export --workspace <id-or-slug>
```

Exports are local, redacted and gitignored.

## Adoption Plan

```text
python scripts/workflow.py plan --workspace <id-or-slug> --deployment-profile quality --adopt
```

Present every CREATE, ADOPT, UPDATE, ATTACH_SKILL, DETACH_SKILL, ADD_MEMBER, SET_ROLE, WARNING and BLOCKED action.

## Apply an Approved Plan

```text
python scripts/workflow.py apply --plan .multica/plans/<plan>.json --approve <short-digest>
```

Apply must fail when Git HEAD, manifest hash, Runtime map or observed Multica preconditions changed after planning.

`apply` performs an immediate post-mutation reconciliation check. Before advancing to a release, deployment or Canary gate, run a separate explicit `verify` as a fresh read and retain its operator result.

## Verify and Drift

```text
python scripts/workflow.py verify --workspace <id-or-slug>
python scripts/workflow.py drift --workspace <id-or-slug>
```

## Audit and Observer Health

```text
python scripts/workflow.py audit --workspace <id-or-slug> --output json
python scripts/workflow.py health --workspace <id-or-slug> --output json
```

`audit` is read-only unless `--report` is explicitly supplied. `health` must fail clearly when the managed Autopilot or readable run history is unavailable.

The portable Observer validates the generated full desired-state contract. Workflow source changes must run:

```text
python scripts/generate_audit_contract.py
python scripts/generate_audit_contract.py --check
```

## Disable Operations Before Rollback

```text
python scripts/workflow.py plan --workspace <id-or-slug> --disable-operations
```

Review and approve this Plan before checking out an older release. It pauses the managed Observer Autopilot while retaining Reporter capability and Incident history.

## Runtime Migration

```text
python scripts/workflow.py plan --workspace <id-or-slug> --deployment-profile codex-only --rebind-runtimes
```

Never migrate Runtimes as an incidental effect of updating instructions.

## Release Plan

```text
python scripts/release.py plan --version <version> \
  --development-issue <requirement-or-maintenance-case> \
  --implementation-provenance <integration-validation-issue>
$env:GH_TOKEN = <short-lived Dispatcher App installation token>
python scripts/release.py doctor
python scripts/release.py approval-block --plan <release-plan>
python scripts/release.py apply --plan <release-plan> --approve <short-digest>
Remove-Item Env:GH_TOKEN
```

Generate the Plan with the normal human-host read context. Use the completed top-level Requirement for a feature release, or the approved Phase 1 Maintenance Case for an Incident fix. The linked integration-validation Issue must contain the ordinary Code Reviewer comment and exact Plan/SHA/PR bindings. After Plan generation, the durable human approver comments `APPROVE WORKFLOW RELEASE <short-digest>` on the selected development Issue. Then mint a short-lived, selected-repository Dispatcher App token outside Agent runtimes and expose it only through `GH_TOKEN` for `doctor`, `approval-block` and `apply`. The read-only `approval-block` prints a Release Request summary. `apply` dispatches that request but cannot mutate tags or Releases. The isolated reviewer approves the protected `workflow-release` Environment, and the dedicated Publisher App performs publication. `verify-tag` rechecks Environment, workflow-run, PR, CI, Dispatcher, Publisher and release provenance. Bootstrap mode is historical and cannot authorize a new release.

## Install Local Skills

```text
python scripts/workflow.py install-skills
```

Prefer links or Windows junctions. Copy only when links are unavailable, and rerun after Git updates.

The default Phase 1 install contains requirement-intake, workflow-manager, workflow-observer and workflow-console. Future Maintainer and Secure Runtime components are not installed by this command.
