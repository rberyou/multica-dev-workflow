# Incident Contract

An Incident is a durable record for a workflow problem that must survive the current task. Ordinary code defects fixed inside the same task do not require an Incident.

Required metadata:

- `managed_by=multica-dev-workflow`
- `workflow_object_type=incident`
- `workflow_id=development-delivery`
- `workflow_version`
- `workflow_instance_id`
- `protocol_revision=v4`
- `incident_dedupe_key`
- `incident_rule_id`
- `incident_status`
- `incident_severity`
- `source_issue_id`
- `source_requirement_id`
- `reporter_agent_id`
- `incident_evidence_log`

The script also maintains affected and blocked source lists, `fix_requirement_id`, verification evidence, recurrence links, and deployment bindings when those states exist.

Lifecycle:

`incident_status` uses `open -> in_fix -> closed`; the Multica Issue uses `todo -> in_progress -> done`.

A source must already be a protocol-v4 workflow Issue and cannot itself be an Incident. The dedupe key binds workspace, workflow, protocol, rule ID, and the selected entity. Repeated evidence is fingerprint-deduplicated, evidence history is bounded, and severity may increase but never decrease.

`--block-source` refuses to replace another Incident's blocking relationship and remembers the previous status for restoration. `link-fix` accepts one protocol-v4 ordinary Requirement and refuses replacement. Passed closure requires that Requirement to be `done`, a full source commit, and a deployment Plan digest. Restoration clears only blocking metadata still owned by the closing Incident. Failed verification leaves the Incident in `in_fix`.

A new report reuses an active Incident with the same dedupe key. A report after closure creates a new Incident linked by `recurrence_of`.
