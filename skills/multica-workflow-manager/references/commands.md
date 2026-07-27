# Workflow Manager Commands

```text
python -m pip install -r requirements.txt
```

## Inspect and Plan

```text
python scripts/workflow.py doctor --deployment-profile quality
python scripts/workflow.py export --workspace <id-or-slug>
python scripts/workflow.py plan --workspace <id-or-slug> --deployment-profile quality --adopt
```

Add `--allow-dirty` only to inspect a draft Plan from an uncommitted checkout. Draft Plans cannot be applied.

Stop on a missing CLI, authentication failure, workspace ambiguity, Runtime ambiguity, invalid approver roster, an unmarked same-name object without `--adopt`, or any `BLOCKED` action.

After resolving the Workspace, `doctor`, `plan`, `drift`, and `verify` automatically select `.multica/runtime-maps/<workspace-id>.json`. Runtime UUIDs are Workspace-specific, so keep one ignored local file per Workspace. A missing file is treated as an empty map; automatic selection then succeeds only when exactly one online Runtime matches each required provider. Use `--runtime-map <path>` only to override the workspace-scoped default.

```json
{
  "bindings": {
    "codex-orchestrator": {"runtime_id": "<workspace-codex-runtime-id>"},
    "codex-worker": {"runtime_id": "<workspace-codex-runtime-id>"},
    "opencode-review": {"runtime_id": "<workspace-opencode-runtime-id>"}
  }
}
```

## Apply and Verify

```text
python scripts/workflow.py apply --plan .multica/plans/<plan>.json --approve <short-digest>
python scripts/workflow.py verify --workspace <id-or-slug>
python scripts/workflow.py drift --workspace <id-or-slug>
```

Apply rechecks the exact clean Git commit, desired-source hash, selected Runtime map, workspace, and observed Multica state. The Plan stores the resolved map path and hash, so changing that Workspace's map invalidates the Plan. `verify` performs a fresh read after mutation.

Apply refuses `MULTICA_AGENT_ID` or `MULTICA_TASK_ID` by default. `--allow-agent-identity` is a break-glass override that requires explicit review; it is not part of the normal deployment flow.

If Apply stops after a partial mutation, inspect the journal, generate a fresh Plan from the new observed state, and review that Plan before continuing. Do not reuse the stale Plan; retirement actions are ordered and idempotently converge across a fresh Plan.

The Plan also shows retirement actions for previously managed Agents, Skills, and scheduled automations that no longer exist in `workflow.json`. Apply removes retired Agents from the managed Squad roster, removes their Skill attachments, deletes dependent automations, reassigns preserved Projects to the current development leader, archives the Agents, and deletes retired Skills. It preserves retired Projects and their Issue history with a warning.

## Deploy an Unreleased Checkout

Commit and review the desired changes on any branch, then run the ordinary workspace Plan/Apply flow. Do not create a temporary version tag merely to deploy the checkout. The deployment record binds the source commit and Plan digest.

## Runtime Provider Change

```text
python scripts/workflow.py plan --workspace <id-or-slug> --deployment-profile codex-only --rebind-runtimes
```

Review every binding change. Do not rebind Runtimes as an incidental effect of instruction or Skill updates.

## Incident Commands

```text
python scripts/workflow.py bind-workflow-issue --workspace <workspace> --issue <issue> --object-type <type> --created-by-role <role>
python scripts/workflow.py report-incident --workspace <workspace> --source-issue <issue> --rule-id <stable-rule-id> --severity <low|medium|high|urgent> --summary <text> --expected <text> --actual <text> --evidence <redacted-evidence>
python scripts/workflow.py link-incident-fix --workspace <workspace> --incident <incident> --requirement <requirement>
python scripts/workflow.py close-incident --workspace <workspace> --incident <incident> --result failed --evidence <text>
python scripts/workflow.py close-incident --workspace <workspace> --incident <incident> --result passed --evidence <text> --source-commit <40-char-commit> --deployment-plan-digest <64-char-digest>
```

These repository wrappers are event-driven human-host commands; nothing invokes them on a schedule. Managed Agents use the attached Incident Skill's direct `bind-workflow-issue`, `report`, `link-fix`, and `close` commands because product repositories do not contain this workflow repository's `scripts/workflow.py`.

## Formal Release

```text
python scripts/release.py doctor
python scripts/release.py plan --version <version>
python scripts/release.py publish --plan <release-plan>
git fetch --tags
python scripts/release.py verify-tag --tag <v-version>
```

Formal release planning requires a clean `main` checkout whose VERSION, workflow version, active Skill versions, and Changelog section match. Run `package` with the same Plan only when a local asset preview is useful; `publish` rebuilds the assets and uses the authenticated human host's `gh release create`.

## Install Local Skills

```text
python scripts/workflow.py install-skills
```

The default target is `~/.agents/skills`. Prefer links or Windows junctions. Use `--copy` only when links are unavailable, `--target` for another local Skill root, and `--replace-existing` only after reviewing an existing same-name destination.

The command removes only known retired Observer/Maintainer Skill destinations whose metadata confirms that they belong to this workflow; it refuses to delete an unowned same-name directory.
