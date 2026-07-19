# Maintenance Policy

- Git is desired state; Multica is runtime state.
- Never prototype a fix by editing managed Multica objects directly.
- Maintainer and Reviewer identities are durable Multica Agent IDs; a shared GitHub login does not prove independence.
- Maintainer and Reviewer must run through the dedicated Secure Agent Launcher. Reviewer has no GitHub token; Maintainer receives only Broker RPC capabilities, never a raw installation token.
- Non-decision Review loops are automatic on the original Issue. Human interaction is limited to decisions, Plan approval, release approval and protected Environment approval.
- Every code/instruction change invalidates the old Review.
- Protocol changes require an RC, isolated canary, Observer verification and rollback rehearsal.
- Existing v2 Issues remain on v2 core semantics; missing protocol metadata means v2.
- OpenCode and direct host Codex are not valid maintenance runtimes in RC4.
