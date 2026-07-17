---
name: multica-requirement-intake
description: Create, draft, submit, and follow top-level development requirements in Multica for a configured development squad. Use when the user asks Codex or OpenCode to “创建需求”, “发布到 Multica”, “交给开发小队”, inspect or approve a Plan, answer a decision block, follow a T-* issue, or perform final requirement approval. Enforces portable runtime discovery, duplicate checks, project and squad resolution, and human approval gates.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 1.1.0-rc.3
---

# Multica Requirement Intake

Use this skill as the portable entry point between the user and a Multica development squad.

## Fixed Workflow Contract

- Create only the top-level requirement issue. Never create Plan, Implementation, design, split, or development task issues from outside the squad.
- Assign the top-level issue to the resolved development squad, not to the leader or an individual agent.
- Create safely in `backlog`, verify the result, then move it to `todo` only when the user asked to submit or start it.
- Do not set, copy, guess, cache, or request `human_approver_id`. The squad leader discovers the unique roster member whose role is `人工审批人`.
- Do not design the implementation or start coding while creating the requirement. Record user-supplied solution constraints or hypotheses only as unapproved Plan inputs.
- Never infer approval. Post an approval command only after the user explicitly authorizes that exact revision and the authenticated actor is the roster approver.
- Never invent Multica CLI commands, flags, profile names, workspace IDs, squad IDs, or project IDs. Inspect the current environment and CLI help.
- Never persist machine-specific values inside this skill.

Portable defaults:

```text
squad_name: 开发交付小队
approver_role: 人工审批人
```

Allow the user to override the squad name explicitly. An optional `MULTICA_REQUIREMENT_SQUAD` environment variable may provide a machine-local default. Always resolve the name to a unique squad in the selected workspace at runtime.

## Determine Intent

Choose one mode from the user's wording:

- **Draft**: “整理一下”, “写个需求”, “先看看怎么描述”, or “不要创建”. Produce a draft only.
- **Submit**: “创建”, “发布”, “提交到 Multica”, “交给小队”, or “立即启动”. Create, verify, and move the issue to `todo`.
- **Queue**: “先放待规划”, “暂不启动”, or “放 backlog”. Create and leave it in `backlog`.
- **Follow**: “看看 T-123”, “现在到哪一步”, or “我需要做什么”. Read the issue chain and current gate; do not modify unless requested.
- **Approve or decide**: act only on an explicit user decision and validate the actor, target revision, and gate first.

If “帮我创建需求” is explicit, do not ask for a separate confirmation after preparing the description. Ask only when the workspace or project is ambiguous, or a testable acceptance outcome cannot be inferred safely.

## Resolve the CLI Portably

Use the first executable candidate that passes `version --output json`:

1. Path explicitly supplied by the user.
2. `MULTICA_BIN` environment variable.
3. `multica` or `multica.exe` on `PATH`.
4. A Multica Desktop managed or bundled CLI found under the current OS user's standard application-data or application-install directories.

Limit filesystem searches to Multica-specific standard directories. Do not scan entire disks. Do not modify permanent `PATH`, install software, download binaries, or change CLI configuration unless explicitly requested.

If no CLI is available:

1. Use an authenticated Multica browser UI when browser control is available.
2. Otherwise produce a ready-to-submit draft and state clearly that no issue was created.

Do not call undocumented HTTP endpoints with `curl` as a fallback.

Read [portable-setup.md](references/portable-setup.md) for provider paths, CLI discovery examples, and first-use checks on a new computer.

## Resolve Profile and Workspace

Build an optional profile argument from, in order:

1. profile explicitly supplied by the user;
2. `MULTICA_REQUIREMENT_PROFILE` environment variable;
3. the CLI default profile when `config show` reports a configured server;
4. a uniquely configured named profile discovered under `~/.multica/profiles/`.

When discovering named profiles, enumerate directory names only, then inspect candidates through `config show --profile <name>`. Do not read or print profile `config.json` files because they may contain authentication tokens. If multiple configured profiles remain, ask the user. Do not assume a profile name from another computer.

Resolve the workspace from, in order:

1. workspace explicitly supplied by the user;
2. `MULTICA_WORKSPACE_ID` environment variable;
3. the selected profile's configured default, inspected with `config show` and `workspace get`;
4. `workspace list --output json` when no default exists.

If exactly one accessible workspace exists, use it. If multiple workspaces exist and none was selected, ask the user. Pass the resolved workspace ID explicitly to all later commands. Do not run `workspace switch` or alter the user's default unless requested.

Inside a daemon-managed Multica task, trust the injected `MULTICA_WORKSPACE_ID`; never fall back to a user-global CLI config.

## Resolve the Squad

1. Use the explicit squad name, then `MULTICA_REQUIREMENT_SQUAD`, then `开发交付小队`.
2. Run `squad list --output json` in the resolved workspace.
3. Require exactly one active exact-name match.
4. Read the squad and roster back. Confirm it has a leader and exactly one `member_type=member` entry whose role is `人工审批人`.
5. Use the resolved squad UUID only for the current operation. Do not write it into the skill.

If no match, multiple matches, no leader, or an invalid approver roster is found, stop before creating or starting an issue and report the configuration problem. Do not create or repair a squad unless explicitly requested.

## Resolve the Project

1. Prefer an explicit Multica project named by the user.
2. Otherwise inspect the current repository remote and compare it with project resources.
3. Otherwise list active projects and choose only when exactly one project matches the repository or product name.
4. If more than one candidate remains, ask the user which project to use.
5. Never create a Multica project unless explicitly requested.

## Prepare the Requirement

Read [requirement-template.md](references/requirement-template.md) and include:

- background and observed problem;
- evidence, reproduction, or examples;
- desired outcome and testable acceptance criteria;
- scope, non-goals, constraints, and known risks;
- repository, environment, logs, screenshots, or links when relevant;
- unresolved product decisions that the Plan must address.

Keep the issue solution-neutral. Do not turn an implementation guess into an approved design.

## Create Through the CLI

Before using a command, inspect `--help`. Use structured `--output json` whenever supported. Conceptual command shape:

```text
<multica> [--profile <profile>] --workspace-id <workspace-id> <command> ...
```

If the installed CLI does not expose a required command or cannot preserve the safe `backlog`-then-verify flow, use the authenticated browser path or Draft mode. Do not approximate missing commands with undocumented flags or direct API calls.

Then:

1. Verify authentication with a read-only command.
2. Resolve workspace, squad, roster, and project as described above.
3. Search active issues using distinctive title keywords. Reuse or update an equivalent issue unless the user explicitly permits a duplicate.
4. Write multiline descriptions to a temporary UTF-8 file and use `--description-file`. This is required on Windows and preferred on every OS.
5. Create the issue in `backlog`, with project and squad assignment in the create call when supported.
6. Read the created issue back and verify title, description, project, assignee, parent, and status. The parent must be empty.
7. For Submit mode, change `backlog` to `todo` only after verification. For Queue mode, leave it in `backlog`.
8. Remove only the temporary description file created for this operation.
9. If creation partially succeeds, do not create a second copy. Leave the issue in `backlog`, report its key, and repair the existing issue.

Current CLI shape, subject to help verification:

```text
<cli> [profile] workspace list --output json
<cli> [profile] --workspace-id <workspace-id> project list --output json
<cli> [profile] --workspace-id <workspace-id> squad list --output json
<cli> [profile] --workspace-id <workspace-id> squad member list <squad-id> --output json
<cli> [profile] --workspace-id <workspace-id> issue search "<keywords>" --output json
<cli> [profile] --workspace-id <workspace-id> issue create --title "<title>" --description-file "<utf8-file>" --status backlog --project "<project-id>" --assignee-id "<squad-id>" --output json
<cli> [profile] --workspace-id <workspace-id> issue get "<issue-key>" --output json
<cli> [profile] --workspace-id <workspace-id> issue status "<issue-key>" todo --output json
```

## Create Through the Browser

1. Select the intended workspace and project explicitly.
2. Resolve and verify the exact squad and its approver roster.
3. Search for an equivalent active issue.
4. Create one top-level issue in `backlog` and fill the complete title and description.
5. Assign the resolved squad and verify there is no parent issue.
6. Re-open the issue and verify the saved content.
7. Move it to `todo` only for Submit mode.

Do not manually create the squad's child issues in the UI.

## Follow Approvals and Decisions

Before changing anything, read the top-level requirement, relevant child issue, recent comments, metadata, Plan revision, review outcome, and current assignee.

For a human approval action, also:

1. Reject execution when `MULTICA_AGENT_ID` or `MULTICA_TASK_ID` indicates a daemon agent identity.
2. Read the authenticated user ID, for example through `user profile get --output json`.
3. Read the current squad roster and find exactly one `member_type=member`, `role=人工审批人` entry.
4. Require the authenticated user ID to equal that roster member ID.

If the authenticated identity cannot be verified, do not post an approval or decision command.

Then apply the requested action:

- Plan approval: after explicit user authorization and a valid independent review, post `APPROVE PLAN v<N>` and mention the current Plan owner.
- Decision response: after explicit user direction, post `DECISION: <decision>` and mention the current stage owner.
- Final approval: after explicit user authorization and completed integration validation, post `APPROVE REQUIREMENT v<N>` and mention the integration owner.

Do not approve an unreviewed Plan, a stale revision, or a requirement whose integration validation is incomplete. If identity or gate validation fails, explain the mismatch instead of posting the command.

## Report the Result

After a mutation, report:

- issue key and link when available;
- selected workspace and project;
- resolved squad and final status;
- whether the squad was started;
- any ambiguity, duplicate, configuration problem, or remaining user action.

Never claim an issue was created or approved without reading the resulting state back.

For the human-facing lifecycle, status meanings, and ready-to-use prompts, read [operating-manual.md](references/operating-manual.md).
