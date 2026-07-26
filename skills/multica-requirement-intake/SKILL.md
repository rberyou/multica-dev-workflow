---
name: multica-requirement-intake
description: Clarify, confirm, create, submit, and continuously follow top-level development requirements in Multica for a configured development squad. Use when the user asks Codex or OpenCode to “创建需求”, “发布到 Multica”, “交给开发小队”, inspect or approve a Plan, answer a decision block, follow a T-* issue, or perform final requirement approval. Enforces confirmation before creation, portable runtime discovery, duplicate checks, project and squad resolution, continuous progress tracking, and human approval gates.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 1.1.0-rc.4
---

# Multica Requirement Intake

Use this skill as the portable entry point between the user and a Multica development squad.

## Fixed Workflow Contract

- Create only the top-level requirement issue. Never create Plan, Implementation, design, split, or development task issues from outside the squad.
- Never create, start, or materially update a requirement from the user's initial description alone. Clarify the requirement, present the complete current draft and duplicate disposition, and obtain explicit confirmation bound to that draft revision before the corresponding create, start, or material-update mutation.
- Assign the top-level issue to the resolved development squad, not to the leader or an individual agent.
- Create safely in `backlog`, verify the result, then move it to `todo` only when the user asked to submit or start it.
- After starting a requirement, keep following its complete Issue chain until it reaches a terminal state or the user explicitly asks to stop. Do not treat issue creation, an approval request, or one completed stage as the end of the task.
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

- **Draft**: “整理一下”, “写个需求”, “先看看怎么描述”, or “不要创建”. Produce and revise a draft only.
- **Prepare to submit**: “创建”, “发布”, “提交到 Multica”, “交给小队”, or “立即启动”. Clarify and present a versioned draft; after confirmation, create and safely move a new/backlog issue to `todo`, or reuse an already-active equivalent issue without regressing its status.
- **Prepare to queue**: “先放待规划”, “暂不启动”, or “放 backlog”. Clarify and present a versioned draft; after confirmation, create and leave a new issue in `backlog`. If the confirmed duplicate is already active, disclose that it will not be downgraded and require the user to choose reuse-and-follow or explicitly permit a separate queued issue.
- **Follow**: “看看 T-123”, “现在到哪一步”, or “我需要做什么”. Read the issue chain and current gate; do not modify unless requested.
- **Approve or decide**: act only on an explicit user decision and validate the actor, target revision, and gate first.

An initial request to “创建需求” authorizes preparation, read-only discovery, and duplicate checks. It does not authorize issue creation. Silence, “看起来可以”, approval of a different draft, or approval of a Plan is not requirement-creation confirmation.

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

## Confirm Before Creation

For Prepare to submit and Prepare to queue modes:

1. Use read-only discovery to resolve the workspace, project, squad, roster, repository, and likely duplicates before asking for confirmation. Read each proposed reuse/update target in full and require it to be a top-level issue in the resolved project. Do not mutate Multica.
2. Ask focused questions about missing or conflicting user-visible behavior, acceptance criteria, scope, non-goals, compatibility, risk, or target environment. Do not move implementation choices that belong in Plan into the requirement.
3. Present the complete proposed title and description, plus the resolved workspace, project, squad, intended final/start status, and duplicate disposition. A reuse or update disposition must name the existing issue's canonical identifier and link when available.
4. Label the draft `Requirement Draft v<N>` and state explicitly that no issue has been created yet.
5. Request explicit confirmation of the current revision. The preferred confirmation is `CONFIRM REQUIREMENT v<N>`; an equally explicit natural-language confirmation is valid only when it names the same revision and unambiguously authorizes creation.
6. If the user changes any material requirement content or target after confirmation, increment the draft revision, show the complete revised draft, and obtain confirmation again.

Requirement-intake confirmation authorizes only the displayed top-level action (create new, reuse unchanged, or update existing) with the displayed target and start mode. It is not Plan approval, final requirement approval, release approval, deployment approval, or permission to create child issues.

## Create Through the CLI

Before using a command, inspect `--help`. Use structured `--output json` whenever supported. Conceptual command shape:

```text
<multica> [--profile <profile>] --workspace-id <workspace-id> <command> ...
```

If the installed CLI does not expose a required command or cannot preserve the safe `backlog`-then-verify flow, use the authenticated browser path or Draft mode. Do not approximate missing commands with undocumented flags or direct API calls.

Then:

1. Verify authentication with a read-only command.
2. Resolve workspace, squad, roster, and project as described above.
3. Search active issues using distinctive title keywords. Read any candidate before proposing reuse or update, and require it to be a top-level issue in the resolved project. Propose reuse of an equivalent issue, a bounded update to it, or creation of a duplicate only when the user explicitly permits one.
4. Immediately before mutation, re-read the resolved project, squad, roster, and proposed existing target. Reconfirm that the current target, complete content, start mode, and duplicate disposition exactly match the confirmed draft revision. Stop and issue a revised draft if confirmation is missing, stale, or no longer matches current state.
5. When creation or the confirmed update needs a multiline description, write it to one temporary UTF-8 file and use `--description-file`. This is required on Windows and preferred on every OS. Do not create a temporary file for unchanged reuse.
6. Carry out the confirmed disposition. Reuse an unchanged equivalent issue without creating a copy; update only the confirmed fields of an existing issue; otherwise create one new issue in `backlog`, with project and squad assignment in the create call when supported.
7. Read the target issue back and verify title, description, project, assignee, parent, and status. The parent must be empty.
8. For confirmed Prepare to submit mode, change `backlog` to `todo` only after verification; never regress an already-active reused issue. For confirmed Prepare to queue mode, leave a new issue in `backlog` and do not downgrade an already-active reused issue.
9. After all mutations, perform a fresh read using the canonical issue ID returned by the service. Verify the final identifier, workspace, project, assignee, content, parent, and status before reporting success or beginning Follow mode.
10. In a finally-style cleanup that runs after success or failure, remove only the temporary description file created for this operation, if one was created.
11. If creation or update partially succeeds, do not create a second copy. Leave a new issue in `backlog` when possible, report the canonical target ID only if known, and repair the same issue.

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
3. Search for an equivalent active issue, open each candidate to verify it is a top-level issue in the resolved project, and propose a duplicate disposition.
4. Immediately before mutation, reload the resolved project, squad, roster, and proposed existing target. Reconfirm that the current target, complete content, start mode, and duplicate disposition exactly match the confirmed draft revision. Stop and issue a revised draft if confirmation is missing, stale, or no longer matches current state.
5. Carry out the confirmed disposition: reuse unchanged, update only confirmed fields, or create one new top-level issue in `backlog`.
6. Assign the resolved squad when creating or when the confirmed update requires it, and verify there is no parent issue.
7. Re-open the target issue and verify the saved content.
8. Move `backlog` to `todo` only for a confirmed Prepare to submit mode; never downgrade an already-active reused issue.
9. Reload the target by its canonical service ID and verify the final workspace, identifier, content, assignment, parent, and status before reporting success or beginning Follow mode.

Do not manually create the squad's child issues in the UI.

## Continuously Follow a Started Requirement

After a confirmed Prepare to submit operation starts a new/backlog issue or reuses an issue that is already active, immediately switch to Follow mode for that top-level issue. The default outcome of “创建并启动” is create **and follow**, not create and stop.

1. Keep the issue key as the active requirement for the current conversation. On later turns, resume this requirement unless the user changes the target or explicitly stops following it.
2. Follow the top-level issue, its active descendants, recent comments, metadata, assignees, task runs, branch/commit/PR bindings, CI, tests, review outcomes, and `waiting_on`/`blocked_reason` changes.
3. Prefer a host-provided event subscription, recurring monitor, wait, or wake-up mechanism. Otherwise use bounded read-only polling with backoff. Respect host wait limits and never busy-loop.
4. Do not ask the user to manually check Multica. Suppress routine no-change updates; report meaningful stage transitions and keep monitoring.
5. When human action is required, provide the current revision and evidence summary, the exact decision or approval needed, and what will happen after it. Then wait for the user's explicit response.
6. After validating and applying an authorized decision or approval, resume following the same requirement automatically. An approval interaction is a pause in monitoring, not completion.
7. On `done`, report the delivered PR/commit, validation evidence, acceptance results, and residual risks. On `cancelled`, report the reason and replacement link when present. On an abnormal failure or actionable stall, report the owner, evidence, and required recovery action while continuing to follow when the host supports it.

If the current host cannot remain active or provide a wake-up/monitor mechanism, disclose that limitation before ending the turn, retain the issue key in the conversation, and give the exact follow-up command needed to resume. Never claim continuous tracking when no tracking mechanism is active.

## Follow Approvals and Decisions

Before changing anything, read the top-level requirement, relevant child issue, recent comments, metadata, Plan revision, review outcome, and current assignee.

For a human approval action, also:

1. Reject execution when `MULTICA_AGENT_ID` or `MULTICA_TASK_ID` indicates a daemon agent identity.
2. Read the authenticated user ID, for example through `user profile get --output json`.
3. Read the current squad roster and find exactly one `member_type=member`, `role=人工审批人` entry.
4. Require the authenticated user ID to equal that roster member ID.

If the authenticated identity cannot be verified, do not post an approval or decision command. Give the user the exact manual comment and target, continue monitoring for that comment when the host supports it, validate it after it appears, and then resume following automatically.

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

Never claim an issue was created, updated, started, or approved without a fresh read of the resulting state by its canonical service ID. If that read fails or the state does not match, report the operation as unverified or partially successful; do not present an issue key as completed creation evidence.

For a started requirement, the creation report is an intermediate progress update. Continue following instead of ending with only the new issue key and initial status.

For the human-facing lifecycle, status meanings, and ready-to-use prompts, read [operating-manual.md](references/operating-manual.md).
