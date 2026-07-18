# Workflow Incident Runbook

1. Determine whether the defect is workflow-level rather than product code or requirement ambiguity.
2. Choose severity and block the source only when correctness, approval, security, privacy or Git history is at risk.
3. Use `multica-workflow-observer` Reporter Mode to create or reuse one Incident.
4. Preserve links and compact redacted evidence; never copy secrets or unredacted user content.
5. Observer triage records one allowed verdict. Confirmed defects move to `waiting_on=maintenance_intake`; Observer does not create a maintenance tree.
6. A durable human or explicitly authorized external Maintainer selects an existing batch or starts one Maintenance Change. Plan, Implementation, Canary and Rollout stages are created lazily after their preceding gates.
7. During `maintenance_intake_mode=human_gated`, `automatic_expansion=false`, or a stabilization freeze, automatic expansion and status promotion fail closed.
8. Keep the Incident open through fix, RC, Canary, release, affected-Workspace rollout and Observer verification.
9. Same-release unresolved recurrence reuses the Incident and selected Maintenance batch; later/post-fix recurrence links with `recurrence_of`.

Observer scheduler outage requires an external `workflow.py health` check; the scheduler cannot fully monitor itself.

Incident creation first writes a stable pending-scan index and one redacted pending payload containing the original Reporter identity, then marks the source pending and embeds a stable dedupe fingerprint in the title. The scheduled audit scans both pending markers and the stable index without requiring an active Issue or local workflow metadata, so it can recover a partially created Incident even when the pending-marker write fails. Successful source linkage clears the payload and index before clearing the pending flag. Every duplicate persists a bounded rolling evidence log and source linkage metadata. Human-facing comments and subscriber escalation observe the 24-hour cooldown, which is bypassed for urgency, severity increases, newly affected requirements, first deterministic confirmation or expanded blocked scope.
