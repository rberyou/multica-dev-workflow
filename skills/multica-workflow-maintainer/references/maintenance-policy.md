# Maintenance Policy

- Git is desired state; Multica is runtime state.
- Never prototype a fix by editing managed Multica objects directly.
- Maintainer and Reviewer identities are durable Multica Agent IDs; a shared GitHub login does not prove independence.
- Every code/instruction change invalidates the old Review.
- Protocol changes require an RC, isolated canary, Observer verification and rollback rehearsal.
- Existing v2 Issues remain on v2 core semantics; missing protocol metadata means v2.
