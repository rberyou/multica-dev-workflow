---
name: multica-workflow-manager
description: Plan, rebuild, update, verify, and audit a Git-managed Multica squad workflow. Use when the user asks Codex or OpenCode to “重建小队”, “同步小队配置”, “更新工作流”, “从 Git 获取最新 Skill”, inspect drift, migrate runtimes, or apply a reviewed Multica workflow plan.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 1.0.0-rc.1
---

# Multica Workflow Manager

Manage Multica through the repository reconciler. Git is desired state; Multica is runtime state.

## Locate the Repository

Use the repository explicitly provided by the user. Otherwise search the current directory and parents for `workflow.json` plus `scripts/workflow.py`.

Do not edit Multica objects directly when the reconciler manages them.

## Select a Release

- Stable release: fetch tags and checkout the requested or latest reviewed SemVer tag.
- Main channel: checkout `main` and pull with fast-forward only.
- Show the relevant Git diff or changelog before planning an upgrade.

Never apply a different checkout without generating a new plan.

## Required Flow

1. Run `python scripts/workflow.py doctor` with the intended profile, workspace and deployment profile.
2. Resolve every ambiguity. Do not guess Runtime, workspace, approver or adoption choices.
3. Run `python scripts/workflow.py plan` and present the action summary and short digest.
4. Wait for the exact user response `APPROVE WORKFLOW PLAN <short-digest>`.
5. Run `apply` using the exact plan file and digest.
6. Run `verify` and report the deployment record and residual warnings.

The repository-level design approval such as `APPROVE WORKFLOW PLAN v5` authorizes implementation of this system. It does not authorize a later workspace mutation plan with a different digest.

## Safety Rules

- Never bypass `doctor`, Plan approval, stale-state checks or `verify`.
- Never use undocumented HTTP APIs or invent CLI flags.
- Never commit Runtime, Workspace, Agent, Squad or Member UUIDs into portable files.
- Never commit tokens, cookies, MCP secrets or custom environment values.
- Preserve unrelated Agent Skill assignments and non-conflicting extra roster members.
- Do not run destructive pruning or archiving; v1 does not implement it.
- Runtime rebinding requires `--rebind-runtimes` and explicit review of every old/new binding.
- Existing unmarked objects require `--adopt`; same-name objects are not overwritten implicitly.

## Common Commands

Read [commands.md](references/commands.md) for command examples and expected stop conditions.
