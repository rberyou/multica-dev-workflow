# Requirement Submission Procedure

## Contents

- Resolve the protocol binder
- Resolve Squad and Project
- Prepare and confirm the disposition
- Execute through the CLI
- Browser read-only fallback
- Verify and report the result

## Resolve the Protocol Binder

Locate `multica-workflow-incidents/scripts/incidents.py` in the same installed Skill root, an explicitly supplied Skill root, or the current workflow repository. Verify its `--help` output before mutation. Pass the resolved CLI path, profile, and Workspace explicitly.

If the binder is unavailable, do not create, update, reuse-and-start, bind, or approve a managed workflow Issue. Do not reproduce its metadata writes manually.

## Resolve Squad and Project

Resolve the Squad name from the explicit user value, `MULTICA_REQUIREMENT_SQUAD`, then `开发交付小队`. Require exactly one active exact-name match. Read the Squad and roster back; require a leader and exactly one `member_type=member`, `role=人工审批人` entry. Do not create or repair a Squad unless explicitly requested.

Resolve the Project in this order:

1. Use an explicit Multica Project named by the user.
2. Compare the current repository remote with Project resources.
3. Choose an active Project only when exactly one matches the repository or product name.
4. Ask the user when multiple candidates remain.

Never create a Project unless explicitly requested. Use resolved UUIDs only for the current operation; never write them into portable files.

## Prepare and Confirm the Disposition

Before asking for confirmation:

1. Resolve Workspace, Project, Squad, roster, repository, protocol binder, and likely duplicates through read-only calls.
2. Search active Issues using distinctive title keywords. Read every proposed reuse/update target in full and require it to be a top-level Issue in the resolved Project.
3. Ask focused questions about missing or conflicting user-visible behavior, acceptance criteria, scope, non-goals, compatibility, risk, and target environment. Leave implementation design to Plan.
4. Present the complete title and description, resolved targets, intended final/start status, protocol-v4 binding disposition, and duplicate disposition.
5. Label the result `Requirement Draft v<N>` and state that no Issue has been created yet.
6. Require explicit confirmation of the current revision. Prefer `CONFIRM REQUIREMENT v<N>`; accept natural language only when it names the same revision and unambiguously authorizes the displayed action.

The confirmation authorizes only the displayed top-level action: create new, reuse unchanged, or update the displayed fields, with the displayed queue/start mode. It does not authorize Plan approval, final approval, publishing, deployment, child Issues, or implementation.

If any material content, target, protocol disposition, duplicate disposition, or start mode changes, increment the draft revision, show the complete revised draft, and confirm again.

For a confirmed duplicate already active, never regress status. Require reuse-and-follow or explicit permission to create a separate queued Issue.

## Execute Through the CLI

Before each command, inspect `--help` and prefer structured `--output json`. Use the conceptual shape:

```text
<multica> [--profile <profile>] --workspace-id <workspace-id> <command> ...
```

If the installed CLI cannot preserve the safe `backlog -> bind -> verify -> start` flow, stop mutation and return to Draft or read-only Follow mode.

Execute the confirmed disposition:

1. Verify authentication with a read-only command.
2. Re-read the resolved Project, Squad, roster, proposed target, and metadata immediately before mutation.
3. Reject conflicting `workflow_id`, `protocol_revision`, `workflow_object_type`, or root binding. Reject stale confirmation or changed targets.
4. For a multiline create/update description, write one temporary UTF-8 file and use `--description-file`. This is required on Windows and preferred elsewhere. Do not create a temporary file for unchanged reuse.
5. Reuse an unchanged equivalent Issue, update only confirmed fields, or create exactly one new top-level Issue in `backlog`. Assign Project and Squad in the create call when supported.
6. Read the canonical target back and verify title, description, Project, assignee, empty parent, and status.
7. Run the Incident Skill's direct `bind-workflow-issue` command with `--object-type requirement` and the accurate external creator role.
8. Read metadata back and verify `managed_by`, `workflow_id`, `workflow_version`, `workflow_instance_id`, `workflow_object_type=requirement`, `root_requirement_id`, `created_by_role`, `protocol_revision=v4`, and `top_protocol_revision=v4`.
9. For Prepare to submit, change `backlog` to `todo` only after content and protocol verification. For Prepare to queue, leave a new Issue in `backlog`. Never downgrade an active reused Issue.
10. Perform a final read through the canonical service ID and revalidate Workspace, Project, assignee, content, parent, status, and protocol metadata.
11. In finally-style cleanup, remove only the temporary description file created for this operation.

Current CLI shapes, subject to help verification:

```text
<cli> [profile] workspace list --output json
<cli> [profile] --workspace-id <workspace-id> project list --output json
<cli> [profile] --workspace-id <workspace-id> squad list --output json
<cli> [profile] --workspace-id <workspace-id> squad member list <squad-id> --output json
<cli> [profile] --workspace-id <workspace-id> issue search "<keywords>" --output json
<cli> [profile] --workspace-id <workspace-id> issue create --title "<title>" --description-file "<utf8-file>" --status backlog --project "<project-id>" --assignee-id "<squad-id>" --output json
<cli> [profile] --workspace-id <workspace-id> issue get "<issue-key>" --output json
python <incidents-skill>/scripts/incidents.py --multica-bin <cli> [--profile <profile>] --workspace <workspace-id> bind-workflow-issue --issue "<issue-key>" --object-type requirement --created-by-role <role>
<cli> [profile] --workspace-id <workspace-id> issue metadata list "<issue-key>" --output json
<cli> [profile] --workspace-id <workspace-id> issue status "<issue-key>" todo --output json
```

## Browser Read-Only Fallback

Use an authenticated browser only to resolve Workspace/Project/Squad context, inspect duplicates, read an Issue chain, or follow progress when CLI reads are unavailable. Never create or materially update a managed workflow Issue through the browser alone.

## Verify and Report the Result

After mutation, report the canonical Issue key and link when available, selected Workspace and Project, resolved Squad, final status, verified protocol revision and binding, whether execution started, and every ambiguity or remaining action.

If creation, update, or binding partially succeeds, never create a second copy. Leave a new Issue in `backlog` when possible and repair the same canonical target. If final read-back fails or state does not match, report the operation as unverified or partially successful rather than claiming success.

For a started Requirement, treat the creation report as an intermediate update and enter Follow mode immediately.
