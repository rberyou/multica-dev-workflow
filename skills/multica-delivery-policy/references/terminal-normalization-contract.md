# Fixed Terminal Normalization Contract

`terminal-normalization` and `superseded-task-release` are deterministic zero-write validators. They never call Multica or apply their returned writes.

## Approved authority

The fixed operation uses a schema-v2 canonical manifest. The current Plan evidence must match its Plan identity, parent, revision, policy, workflow instance, approved top-level Requirement UUID, `v2.sha256` manifest identity, and `sha256:` immutable aggregate. Full pre/post immutable Requirement entries must be byte-equivalent and individually match the approved snapshot digests; their aggregate must match the approved manifest aggregate.

The portable source encodes only schemas, canonicalization, and comparisons. Concrete Issue identifiers, UUIDs, policy digests, historical values, and paths remain in reviewed non-portable Plan/deployment evidence.

## Endpoint transition and attestation

The endpoint set and order come only from the approved manifest. Each fresh `transition` call accepts one canonical prefix and returns at most one write from:

- `workspace_lease_state`
- `workspace_lease_owner_issue_id`
- `workspace_lease_owner_agent_id`
- `workspace_lease_terminal_normalization_record`

It never returns status, blocker, Incident, approval, Review, Git, test, delivery, or final-gate writes. Capacity is projected before the first record write. Once all endpoints are terminal, `attest` validates the deployed source, deployment Plan digest, pre/post snapshot digests, endpoint records and tuples, immutable evidence, approved root, Plan, policy, and workflow. It returns only `terminal_normalization_evidence_record` on the approved root.

## Superseded task release

`superseded-task-release` is not a general lease transition. Its input must bind the exact task-scope held target and original owner authorized by the Plan, the exact old and replacement branches, closed PR evidence, a clean registered worktree at the expected path/branch/head, unchanged blocker bytes, and independently approved merged source.

The only allowed keys are the lease state, two owner IDs, and `workspace_lease_superseded_release_record`. The fixed sequence is record checkpoint 0, released state, checkpoint 1, empty owner Issue, checkpoint 2, empty owner Agent, checkpoint 3. Each fresh call returns one next write; terminal calls return `already_complete` with no action. Non-canonical prefixes, drift, tampering, capacity failure, or namespace expansion return no writes.
