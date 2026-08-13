# Workflow Incident Runbook

Workflow Incidents are durable exception records, not a monitoring subsystem.

## When to Persist

Create or reuse an Incident only when at least one condition holds:

- the problem cannot be completely and safely fixed in the current task;
- it affects multiple Issues, tasks, projects, or future executions;
- it may recur and needs a stable deduplication key and evidence history;
- another owner or a human decision is required;
- continuing would risk correctness, approval integrity, security, privacy, or Git history;
- the fix must be deployed and checked later before the problem can be considered resolved.

Do not create an Incident for an ordinary code defect, requirement ambiguity, expected Plan revision, transient tool retry, or a workflow mistake already fixed and verified inside the current task.

## Who Calls the Script

`multica-workflow-incidents` is attached to every ordinary development Agent. The active Agent calls its deterministic script immediately after creating a managed Issue and when it discovers a durable workflow problem. A human-hosted Codex session may call the same commands. There is no scheduler, polling loop, dedicated role, or fallback patrol.

The script exists for operations that must behave identically every time: protocol binding, secret redaction, deduplication, metadata transitions, source blocking/restoration, fix linking, and closure evidence. Reasoning about whether a problem is worth recording remains with the active Agent or human.

Managed Agents call the attached Skill directly with `bind-workflow-issue`, `report`, `create-fix-requirement`, `link-fix`, and `close`. A human working from this repository may use the equivalent `workflow.py` wrappers: `bind-workflow-issue`, `report-incident`, `create-incident-fix-requirement`, `link-incident-fix`, and `close-incident`.

## Lifecycle

The durable `incident_status` metadata lifecycle is `open -> in_fix -> closed`. The corresponding Multica Issue statuses are `todo`, `in_progress`, and `done`.

1. Start from a protocol-v4 non-Incident source Issue. Report a redacted Incident. Reuse an active Incident with the same workspace/rule/entity dedupe key; after closure, create a new Incident with `recurrence_of`.
2. Use `--block-source` only for material correctness or integrity risk.
3. Leave `report` independent: it creates or reuses only the Incident. When a fix is justified, explicitly run `create-fix-requirement --incident <incident> --project <external-project> [--assignee-id <external-owner>]`. The Project is mandatory, cannot be the managed Incident Project or another managed workflow Project, and the optional assignee cannot be the managed development Squad or any member. If safety cannot be proven, creation fails closed.
4. The command creates one `backlog` external `incident_fix_requirement`, leaves it unassigned when no owner is supplied, and binds both sides with `fix_execution_mode=external`. It writes no development-tree root, protocol, Plan, Review, or final approval metadata. Stable creation markers and a workspace-wide candidate search recover a create/metadata partial failure; multiple candidates or conflicting bindings block instead of creating another object.
5. `link-fix` may safely complete an external binding whose reverse ID is empty. It also keeps compatibility with an already existing ordinary protocol-v4 Requirement. An Incident can never switch to a different fix Requirement.
6. The external owner or external process performs the correction and marks the fix Requirement `done`. This managed development Squad does not run Plan, implementation, Code Review, integration, or final approval for the external object.
7. A failed `close` records redacted non-empty evidence and keeps the Incident `in_fix`, waiting on `external_fix_owner` for external fixes or `ordinary_development_workflow` for legacy fixes. A passed external close requires `--fix-reference-type` plus an immutable `--fix-reference`, and `--deployment-verification-reference-type` plus `--deployment-verification-reference`. A `git_commit` fix reference must be a full 40-character commit; typed external artifact/version references are also allowed. A passed legacy close still requires the full source commit and 64-character deployment Plan digest.
8. Passed verification restores only source Issues whose blocking metadata is still owned by this Incident. Repeated close is idempotent, and a later report with the same dedupe key creates a recurrence.

Never persist credentials, cookies, private keys, Authorization headers, raw environment values, or unnecessary user data.
