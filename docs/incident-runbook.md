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

Managed Agents call the attached Skill directly with `bind-workflow-issue`, `report`, `link-fix`, and `close`. A human working from this repository may use the equivalent `workflow.py` wrappers: `bind-workflow-issue`, `report-incident`, `link-incident-fix`, and `close-incident`.

## Lifecycle

The durable `incident_status` metadata lifecycle is `open -> in_fix -> closed`. The corresponding Multica Issue statuses are `todo`, `in_progress`, and `done`.

1. Start from a protocol-v4 non-Incident source Issue. Report a redacted Incident. Reuse an active Incident with the same workspace/rule/entity dedupe key; after closure, create a new Incident with `recurrence_of`.
2. Use `--block-source` only for material correctness or integrity risk.
3. Create an ordinary protocol-v4 Requirement for the fix and link it with the direct `link-fix` command or host wrapper `link-incident-fix`. An existing Incident cannot be switched to another fix Requirement.
4. Run the normal Plan, independent review, implementation, Code Review, integration validation, and final approval flow.
5. Deploy the reviewed clean checkout to the affected workspace with a digest-approved workflow Plan.
6. Record the direct `close --result passed` command or host wrapper `close-incident --result passed` with concise redacted verification evidence, a full 40-character source commit, and a 64-character deployment Plan digest. A failed check keeps the Incident in `in_fix`; passed verification restores only source Issues still blocked by this Incident.

Never persist credentials, cookies, private keys, Authorization headers, raw environment values, or unnecessary user data.
