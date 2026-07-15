# Workflow Manager Commands

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

## Verify and Drift

```text
python scripts/workflow.py verify --workspace <id-or-slug>
python scripts/workflow.py drift --workspace <id-or-slug>
```

## Runtime Migration

```text
python scripts/workflow.py plan --workspace <id-or-slug> --deployment-profile codex-only --rebind-runtimes
```

Never migrate Runtimes as an incidental effect of updating instructions.

## Install Local Skills

```text
python scripts/workflow.py install-skills
```

Prefer links or Windows junctions. Copy only when links are unavailable, and rerun after Git updates.
