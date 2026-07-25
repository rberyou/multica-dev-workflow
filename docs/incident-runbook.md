# Workflow Incident Runbook

1. Determine whether the defect is workflow-level rather than product code or requirement ambiguity.
2. Choose severity and block the source only when correctness, approval, security, privacy or Git history is at risk.
3. Use `multica-workflow-observer` Reporter Mode to create or reuse one durable Observation.
4. Preserve links and compact redacted evidence; never copy secrets or unredacted user content.
5. Observer processes pending Observations, applies deterministic rules, and creates or reuses one Incident per root cause.
6. Confirmed defects receive a digest-bound maintenance decision request. Only the registered human approver may approve or defer it.
7. Approval creates one minimal Maintenance Case. In Phase 1, the ordinary development workflow performs the fix; Maintainer and Reviewer automation remain disabled.
8. Record Maintenance Case progress in order: `in-development`, `fix-ready`, `release-recorded`, then `deployment-recorded`. The deployment record must bind the released source commit, approved Workspace Plan digest and completed apply journal.
9. Keep the Incident open through deployment and independent Observer verification. Only the assigned Observer identity may record `verify-fix`; a host call does not substitute for independent verification.
10. Same-release unresolved recurrence reuses the Incident; later/post-fix recurrence links with `recurrence_of`.

Observer scheduler outage requires an external `workflow.py health` check; the scheduler cannot fully monitor itself.

The Observation Inbox is authoritative even when source metadata cannot be written. Incremental and full scans retry failed Observations without aborting the rest of the scan, maintain per-project cursors, and serialize runs with an expiring lease. After five failed attempts an Observation enters `quarantined`; it remains durable and produces a trackable operations finding instead of retrying forever. A new payload with the same fingerprint may re-enter `pending` for another bounded attempt cycle.

Every Incident keeps a bounded, fingerprinted evidence log. Human-facing comments and escalation observe the 24-hour cooldown, bypassed for urgency, severity increases, newly affected requirements, first deterministic confirmation, or expanded blocked scope.
