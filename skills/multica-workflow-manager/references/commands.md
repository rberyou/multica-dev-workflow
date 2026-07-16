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

Review and approve this Plan before checking out an older release. It pauses the managed Observer Autopilot and detaches Reporter capability from development Agents without deleting Incident history.

## Runtime Migration

```text
python scripts/workflow.py plan --workspace <id-or-slug> --deployment-profile codex-only --rebind-runtimes
```

Never migrate Runtimes as an incidental effect of updating instructions.

## Release Plan

```text
python scripts/release.py plan --version <version> --maintenance-issue <T-ID>
python scripts/release.py approval-block --plan <release-plan>
python scripts/release.py apply --plan <release-plan> --approve <short-digest>
```

After Plan generation, the durable human approver must comment `APPROVE WORKFLOW RELEASE <short-digest>` on the Maintenance Issue. Run the read-only `approval-block` command after that comment exists, then have the immutable GitHub approver in `docs/bootstrap-v6.json` post its complete output on the selected merged PR. Apply verifies both comments and the independent Review record; `verify-tag` rechecks the complete GitHub provenance block, tag commit, exact merged PR and CI run before publication. The one-time `v1.1.0-rc.1` bootstrap may use `--bootstrap-plan v6`. No later release may use bootstrap mode.

## Install Local Skills

```text
python scripts/workflow.py install-skills
```

Prefer links or Windows junctions. Copy only when links are unavailable, and rerun after Git updates.
