# RC2 Compatibility Amendment

This amendment supersedes only the Multica Autopilot platform assumptions in the immutable v6 RC1 design record. It does not rewrite or replace `docs/design-plan-v6.md`.

Multica v0.4.2 no longer persists Autopilot priority. Starting with `v1.1.0-rc.2`, desired state, schema, reconciliation, generated Observer contracts, drift checks and verification do not write or compare Autopilot priority. Incident Issue priority and severity behavior are unchanged.

Autopilot detail responses are normalized from the real `assignee_id` and `execution_mode` fields while retaining canonical aliases. Root-level triggers are preserved. Typed member subscribers are accepted; explicitly non-member subscriber shapes remain visible as drift.

Subscriber updates use exactly one replacement mode: repeated `--subscriber` flags for a non-empty set, or `--clear-subscribers` for an empty set. Active creation does not issue a redundant update. Paused creation performs one minimal status update.

CLI `apply` retains its immediate post-mutation reconciliation check. Operators must also run a separate explicit `verify` as a fresh read before advancing to a release, deployment or Canary execution gate.

The one-time external pending-Incident release exception is limited to `v1.1.0-rc.2`. It binds the durable source, decision comment and control identities through bounded IDs and hashes, and cannot be reused after the pending Incident is linked or for a later release.
