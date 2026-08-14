---
name: multica-workflow-manager
description: Plan, deploy, verify, and inspect a packaged Multica development workflow from a Git checkout or formal Release Bundle. Use when the user asks to rebuild or update the squad, sync workflow configuration, install current Skills, inspect drift, deploy reviewed source, change Runtime providers, or create a formal release.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.8
---

# Multica Workflow Manager

Reviewed workflow source is desired state and Multica is runtime state. A clean Git checkout or verified formal Release Bundle may be deployed directly.

## Workspace Deployment

1. Use the Git checkout or extracted formal Release Bundle explicitly provided by the user, or locate `workflow.json` and `scripts/workflow.py` in the current directory or its parents.
2. Run `python scripts/workflow.py doctor` for the intended profile, workspace, and deployment profile.
3. Resolve workspace, Runtime, approver, and adoption ambiguity. Do not guess.
4. Run `python scripts/workflow.py plan` and present every Workspace and machine-global Local Skill action plus the short digest.
5. Wait for the exact approval `APPROVE WORKFLOW PLAN <short-digest>`.
6. Apply the exact Plan and run a fresh `verify`.

Every Skill whose manifest `targets` contains `local` is installed as a physical copy under `~/.agents/skills` by the normal approved Apply. The Plan binds the resolved root, desired Skill digests, observed destination types and digests, and all Local Skill actions. Any source, Runtime-map, Workspace, or Local Skill state change invalidates the Plan. A dirty Git checkout may produce a draft Plan with `--allow-dirty`, but a draft cannot be applied. A Release Bundle with a changed or undeclared workflow file is invalid rather than draft.

## Guardrails

- Never bypass doctor, Plan approval, stale-state checks, or verify.
- Apply refuses daemon-managed Agent identities by default. Use `--allow-agent-identity` only as an explicitly reviewed break-glass action.
- Never invent CLI flags or use undocumented HTTP APIs.
- Never commit Runtime, Workspace, Agent, Squad, or Member UUIDs, credentials, cookies, or environment secrets.
- Preserve unrelated objects and non-conflicting extra roster members.
- Existing unmarked same-name objects require `--adopt`.
- Never create a Local Skill symlink or Windows junction. Migrate only links whose resolved `SKILL.md` proves this workflow owns them; never overwrite, adopt, or delete a foreign same-name target.
- A reviewed Plan removes retired Agents from the managed Squad, deletes dependent automations, reassigns preserved Projects, archives the Agents, and deletes retired Skills. Retired Projects are preserved because they may contain durable history.
- Runtime rebinding requires `--rebind-runtimes` and explicit review; Runtime profiles are provider selection, not an isolation boundary.
- Do not add scheduled scans or maintenance-specific roles.

## Reference Routing

- Read [commands.md](references/commands.md) when executing or explaining commands, inspecting drift, deploying a checkout, installing Skills, handling deployment approval, using Incident wrappers, or creating a formal Release.
- Read [runtime-maps.md](references/runtime-maps.md) only when initializing or switching Workspaces, resolving Runtime ambiguity, selecting a deployment profile, creating a Runtime map, or intentionally rebinding Runtimes.
- Read both references when a new Workspace must be configured and deployed.
