---
name: multica-workflow-manager
description: Plan, deploy, verify, and inspect a Git-managed Multica development workflow from the current checkout. Use when the user asks to rebuild or update the squad, sync workflow configuration, install current Skills, inspect drift, deploy a reviewed checkout, change Runtime providers, or create a formal release.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.1
---

# Multica Workflow Manager

Git is desired state and Multica is runtime state. A clean reviewed checkout may be deployed directly; a tag or formal Release is not required.

## Required Workspace Flow

1. Use the repository explicitly provided by the user, or locate `workflow.json` and `scripts/workflow.py` in the current directory or its parents.
2. Run `python scripts/workflow.py doctor` for the intended profile, workspace, and deployment profile.
3. Resolve workspace, Runtime, approver, and adoption ambiguity. The default Runtime map is `.multica/runtime-maps/<workspace-id>.json`; use `--runtime-map` only for an explicit override. Do not guess.
4. Run `python scripts/workflow.py plan` and present all mutation actions and the short digest.
5. Wait for the exact approval `APPROVE WORKFLOW PLAN <short-digest>`.
6. Apply the exact Plan and run a fresh `verify`.

Any checkout, source, Runtime-map, or observed-state change invalidates the Plan. A dirty checkout may produce a draft Plan with `--allow-dirty`, but a draft cannot be applied.

## Safety Rules

- Never bypass doctor, Plan approval, stale-state checks, or verify.
- Apply refuses daemon-managed Agent identities by default. Use `--allow-agent-identity` only as an explicitly reviewed break-glass action.
- Never invent CLI flags or use undocumented HTTP APIs.
- Never commit Runtime, Workspace, Agent, Squad, or Member UUIDs, credentials, cookies, or environment secrets.
- Preserve unrelated objects and non-conflicting extra roster members.
- Existing unmarked same-name objects require `--adopt`.
- A reviewed Plan removes retired Agents from the managed Squad, deletes dependent automations, reassigns preserved Projects, archives the Agents, and deletes retired Skills. Retired Projects are preserved because they may contain durable history.
- Runtime rebinding requires `--rebind-runtimes` and explicit review; Runtime profiles are provider selection, not an isolation boundary.
- Do not add scheduled scans or maintenance-specific roles.

Read [commands.md](references/commands.md) for examples and stop conditions.
