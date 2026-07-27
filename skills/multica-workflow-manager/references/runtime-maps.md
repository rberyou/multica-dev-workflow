# Workspace Runtime Maps

## Contents

- Purpose and selection
- Deployment profiles
- New Workspace initialization
- Moving existing checkout maps
- Switching Workspaces
- Intentional rebinding
- Failure interpretation

## Purpose and Selection

Runtime binding keys in `workflow.json` are portable logical names. Deployment profiles choose the required provider, model, and thinking level. A local Runtime map chooses the concrete Runtime UUID in one Workspace.

After resolving the Workspace, `doctor`, `plan`, `drift`, and `verify` automatically select:

```text
~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json
```

Runtime UUIDs are machine- and Workspace-specific, while binding names are Workflow-specific. The two path namespaces allow multiple checkouts of one Workflow to share local configuration without colliding with another Workflow or Workspace. Never copy a map between Workspaces or commit it to Git. Use `--runtime-map <path>` only as an explicit override. A missing file is an empty map. Existing compatible Agent bindings are preserved; when a new binding must be selected, automatic selection succeeds only when exactly one online Runtime matches the required provider.

Plans, journals, deployment evidence, release Plans, and worktrees are repository-specific and remain under `<repo>/.multica/`; they do not belong beside the Home-level Runtime maps.

## Deployment Profiles

The binding key is logical; its name does not guarantee a provider. Select UUIDs according to the chosen deployment profile:

| Binding | `quality` | `codex-only` | `opencode-only` |
|---|---|---|---|
| `codex-orchestrator` | Codex | Codex | OpenCode |
| `codex-worker` | Codex | Codex | OpenCode |
| `opencode-review` | OpenCode | Codex | OpenCode |

Do not treat a deployment profile as an isolation boundary.

## New Workspace Initialization

1. From the workflow checkout, resolve the Workspace and expected map path:

```text
python scripts/workflow.py doctor --profile <cli-profile> --workspace <id-or-slug> --deployment-profile quality
```

2. List that Workspace's Runtime objects:

```text
multica --profile <cli-profile> --workspace-id <workspace-id> runtime list --output json
```

3. Select online Runtime UUIDs that match the deployment profile. If more than one Runtime matches, use device, owner, custom name, and intended execution location to resolve the choice; do not guess.

4. Create `~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json`. Use the `Workflow` ID printed by `doctor`; for this repository it is `development-delivery`. For `quality`:

```json
{
  "bindings": {
    "codex-orchestrator": {
      "runtime_id": "<workspace-codex-runtime-id>"
    },
    "codex-worker": {
      "runtime_id": "<workspace-codex-runtime-id>"
    },
    "opencode-review": {
      "runtime_id": "<workspace-opencode-runtime-id>"
    }
  }
}
```

5. Run `doctor` again. Require `present, 3 bindings` for this profile.

6. Generate and review a clean Plan. Do not Apply until the exact digest is approved.

## Moving Existing Checkout Maps

When upgrading a checkout that stored maps under `<repo>/.multica/runtime-maps/`, move each `<workspace-id>.json` file to `~/.multica/workflows/<workflow-id>/runtime-maps/`. Do not leave two active copies or merge maps from different Workspaces.

The reconciler does not fall back to the old checkout path. Every saved deployment Plan that references the old absolute path is stale after the move; run `doctor`, generate a fresh Plan, and approve its new digest.

## Switching Workspaces

Use the target Workspace in every command. The reconciler selects its Home-level map by Workflow ID and resolved Workspace ID, so no `--runtime-map` flag is needed during normal switching or when changing repository checkouts.

Each Plan stores the selected map's absolute path and hash. Editing or replacing that file invalidates only Plans that reference it. Separate files allow Plans for different Workspaces to coexist without overwriting each other's Runtime selection.

## Intentional Rebinding

Existing compatible Agent bindings are preserved unless `--rebind-runtimes` is supplied. Rebind only as an intentional, separately reviewed change:

```text
python scripts/workflow.py plan --workspace <id-or-slug> --deployment-profile <profile> --rebind-runtimes
```

Review every `UPDATE_AGENT` Runtime change. Do not combine incidental instruction or Skill updates with an unreviewed rebind.

## Failure Interpretation

| Error | Meaning | Response |
|---|---|---|
| `multiple Runtimes match binding ...` | The map is absent or lacks a binding and automatic selection is ambiguous. | Create or complete the Workspace map. |
| `references a missing Runtime` | The UUID belongs to another Workspace or the Runtime was removed. | Query the target Workspace and replace the UUID. |
| `provider mismatch` | The selected UUID conflicts with the deployment profile. | Select a Runtime with the required provider. |
| `is offline` | The selected Runtime exists but is unavailable. | Restore it or review an intentional rebind. |
| `runtime map changed after planning` | The approved Plan no longer matches the local map. | Generate and approve a fresh Plan. |
