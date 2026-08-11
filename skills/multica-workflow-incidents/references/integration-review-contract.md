# Integration Review and Recovery Contract

Integration validation is owned by the active Squad's unique Integrator and independently reviewed by its unique Code Reviewer. The assignee and `original_owner_id` remain the Integrator; `reviewer_id` is the Code Reviewer. Both identities come from the current Workspace/Squad roster and must be different.

## Deterministic preflight

Normalize the current Issue, roster, dependency, lease, handoff, Review, and optional recovery evidence into a temporary UTF-8 JSON snapshot, then run:

```text
python <this-skill>/scripts/incidents.py integration-review \
  --action <prepare|start|handoff|approve|recover> \
  --snapshot <file>
```

This command is a zero-write preflight. It does not discover or call Multica, mutate the snapshot, or apply returned writes. Exit `0` means the transition is allowed, exit `1` means canonical evidence was rejected, and exit `2` means the snapshot or command is invalid. Apply only returned `metadata_updates`, `assignee_write`, or explicit blocker writes. Never infer approval, `done`, or final-gate writes from the action name.

Every action revalidates:

- Workspace, Squad, a declared-complete current roster, and its digest;
- exactly one active, non-archived Integrator and Code Reviewer;
- distinct owner and reviewer identities, with assignee preserved as owner;
- protocol-v4 integration-validation binding;
- Plan revision, frozen policy digest, base and reviewed commit SHAs;
- dependency contract and requirement-scope lease evidence;
- a fresh metadata-key inventory before adding compact records.

`prepare` creates or revalidates `integration_review_role_record`. `start` requires the same current tuple. `recover` is for a legacy blocked Issue: it returns corrected owner/reviewer/assignee writes plus `integration_review_recovery_record`, but no status or blocker writes. Recovery binds the active Incident, old and new role tuple, current/target lease, immutable evidence, and exact Incident blocker.

## Handoff and review epoch

Review handoff is valid only after `start`. It posts a comment on the integration-validation Issue from the Integrator and mentions the Code Reviewer by exact Agent ID. Inspect the mutation response and pass the exact handoff comment, trigger run, bounded attempt, and `trigger_outcomes` to `handoff`. Exactly one outcome for that Code Reviewer and trigger run must be present and must be `queued`, `coalesced`, or `deferred`; duplicate or conflicting outcomes are rejected.

Retry at most three attempts. Every retry snapshot sets `previous_attempts_complete=true` and carries the complete canonical `previous_attempts` list. Each prior entry has its sequential attempt, comment, trigger run, and a retryable `lost` or `busy` outcome; the current `attempt` equals the list length plus one. Duplicate comment/run evidence, incomplete history, a non-retryable prior result, or a reset counter is rejected. Before the limit, a lost or busy trigger returns `retry_required=true` and no transition writes. After the limit it returns explicit blocker writes (`waiting_on=integration_review_trigger`) plus the previous active status unless an Incident or incomplete Incident transition already owns the blocker namespace, in which case that tuple remains byte-for-byte unchanged.

Review-owned block and restore writes use the scalar `integration_review_block_transition_record`. Blocking records the plan, changes status to `blocked`, then completes the blocker fields; restoring clears the fields while blocked and restores the previous active status last. Every data write is followed by a record checkpoint. A retry resumes the exact prefix, and an opposite new action first finishes the recorded transition and then reruns. No role, approval, `done`, or final-gate write is returned while that prerequisite transition is being resumed.

A successful handoff updates the role record with a recomputable binding digest, digest-bound `review_epoch_id`, handoff comment, trigger run, outcome, and timestamp. Repeated `start` or `recover` preserves an already handed-off or approved epoch rather than regressing it. `approve` accepts only an Agent comment from the recorded Code Reviewer that:

- is later than the current handoff;
- carries `APPROVED`;
- binds the current review epoch, handoff comment, and trigger run;
- has not been consumed by an older epoch.

Old, late, duplicate replacement, unrelated, or pre-recovery comments are rejected. Replaying the exact same already-approved comment is idempotent; a new handoff cannot regress or replace an approved epoch.

## Incident blocker transition

Prepare or inspect a source transition with:

```text
python <this-skill>/scripts/incidents.py incident-transition --snapshot <file>
```

This command is also zero-write. It returns the remaining ordered writes for a versioned `workflow_block_transition_record`. The record binds Incident/source IDs, exact initial Incident-owned tuple, target tuple, recovery/role evidence, Plan/policy/SHA evidence, deployed source commit, deployment Plan digest, and retry progress.

Without a record, only the exact `blocked / waiting_on=workflow_fix / blocked_reason=workflow Incident <id>` tuple owned by that Incident is valid. A successor blocker is written first while status stays `blocked`; Incident owner/previous keys are cleared afterward. A safe restore clears Incident metadata while still blocked and writes the previous active status last. Every data write is followed by a record checkpoint, so a retry can reapply the last idempotent write after either the data write or checkpoint failed.

`close` preflights every blocked source before its first write and consumes the same state machine. It closes the Incident only after every source reaches its recorded target. A drifted or unrecorded successor blocker leaves the Incident `in_fix` and writes nothing.

An incomplete transition record reserves the blocker namespace even after the Incident owner field has been cleared by an earlier write. A completed record is terminal proof for idempotent `close`; later successor activity does not force the old Incident to overwrite or reacquire the source. If a different Incident later owns the exact `workflow_fix` tuple, its preflight may supersede the completed record and start a new transition.

For a recovered integration validation, transfer a wrong held owner through two validated transitions: held-to-released, then released-to-held for the Integrator. `recover` binds the final acquire current/target evidence. Incident close then binds the current role and recovery records, requires both live lease endpoints to contain the same completed acquire record and desired tuple while the mirror still preserves the Incident blocker, and requires the new Review epoch to be approved by the recovered Reviewer. It cannot close the Incident from owner/reviewer evidence that predates the deployed recovery, an unreviewed recovery, or a partially repaired authority/mirror lease.

The platform limit is 50 metadata keys. Role, recovery, blocker-transition, and lease-transition evidence remain one scalar key each and require a fresh capacity preflight before any write.
