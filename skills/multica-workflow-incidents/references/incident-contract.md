# Incident Contract

An Incident is a durable record for a problem owned by the development workflow. Durability alone is insufficient: OS, Codex/model Runtime, Multica daemon, network, Shell, sandbox, external-tool, and host-environment failures are not workflow Incidents and must not create an external `incident_fix_requirement`.

Allowed workflow conditions include rule/protocol conflicts, state-machine or metadata-integrity failures, approval/Review gate failures, workflow source or deployment drift, and missing managed Agents, Skills, or workflow configuration. `report` requires `condition_class=workflow` and rejects any missing or non-workflow class before reading or writing Multica state.

Required active Incident metadata:

- `managed_by=multica-dev-workflow`
- `workflow_object_type=incident`
- `workflow_id=development-delivery`
- `workflow_version`, `workflow_instance_id`, and `protocol_revision=v4`
- `incident_dedupe_key`, `incident_rule_id`, and `incident_condition_class=workflow`
- `incident_status`, `incident_severity`, `source_issue_id`, and `source_requirement_id`
- `reporter_agent_id` and `incident_evidence_log`

The active lifecycle is `incident_status: open -> in_fix -> closed` and Multica status `todo -> in_progress -> done`. A source must be a protocol-v4 non-Incident Issue. The dedupe key binds workspace, workflow, protocol, rule ID, and entity. Repeated evidence is fingerprint-deduplicated and bounded; severity only increases. A new report reuses one active Incident, while a report after any closed/cancelled record creates a new Incident with `recurrence_of`.

`--block-source` refuses another Incident's ownership and remembers the previous status. `report` never creates a fix. `create-fix-requirement` requires an explicit external Project, rejects managed Projects and development-Squad assignees, and creates or recovers one `backlog` external `incident_fix_requirement`. External records use `fix_execution_mode=external` and `workflow_incident_id` without development-tree protocol, Plan, Review, or approval metadata.

`link-fix` recovers that external binding or accepts one distinct existing protocol-v4 ordinary Requirement for legacy compatibility; an Incident source, root, affected, or blocked Issue cannot also be its fix. New create/link operations require `incident_condition_class=workflow`; a legitimate active legacy Incident with no class must be explicitly re-reported under the workflow classification first, while already linked legacy fixes retain closure compatibility. Bindings cannot be replaced. Standard external closure requires a `done` fix, redacted evidence, and typed immutable fix/deployment-verification references. Standard legacy closure requires a `done` fix, full source commit, and deployment Plan digest. `independent_remediation` is limited to an unchanged cancelled legacy fix while its Incident remains `in_fix`; it preserves `fix_requirement_id` and requires typed immutable fix/deployment evidence. Closure writes `incident_status=closed` last and a repeated close converges a failed final Issue-status update to `done` without duplicating verification.

## Administrative Retraction

`retract` applies only when `fix_requirement_id` is absent or empty and the record was misclassified as a workflow Incident. Supported reasons map deterministically to source waiting conditions:

- `misclassified_non_workflow_runtime_condition -> runtime`
- `misclassified_non_workflow_environment_condition -> environment`
- `misclassified_non_workflow_platform_condition -> platform`

The command validates every referenced source before writing. It clears `workflow_blocked_by_incident_id` and previous-status metadata only when that relation still points to the retracted Incident. Such a source remains `blocked` with the mapped non-workflow `waiting_on`; the command never restores its prior status, replaces the Issue, or migrates a lease. It clears `workflow_incident_id` only when that link still points to the same Incident and never changes another Incident's ownership.

The retracted record converges to Issue status `cancelled`, `incident_status=closed`, `incident_result=not_applicable`, `incident_closure_mode=administrative_retraction`, an allowed `incident_retraction_reason`, the mapped non-workflow `incident_condition_class`, `blocked_source_issue_ids=[]`, and empty `waiting_on`. Redacted evidence, actor, and timestamp remain on the Incident; affected-source and original evidence history remain audit data. No fix or deployment reference is created. Repeating the command is idempotent. The legacy T-136 shape is accepted when it already has the required cancelled/closed/not-applicable/retraction fields, an empty blocked-source list, and no fix Requirement.

Ordinary passed closure and administrative retraction both touch only directly listed source relationships they still own; either path does not traverse or automatically change descendants. Failed verification remains `in_fix` with mode-appropriate `waiting_on`.
