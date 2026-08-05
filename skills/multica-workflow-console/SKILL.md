---
name: multica-workflow-console
description: Check packaged workflow validity and current Multica drift through a read-only host console. Use when the user asks for workflow status, drift, deployment readiness, or a non-mutating health check from a Git checkout or Release Bundle.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.3
---

# Multica Workflow Console

This Skill is host-only, read-only, and is not attached to managed Agents.

Run from the Skill directory or pass the script by absolute path:

```text
python scripts/workflow_console.py status --repo <workflow-source> --workspace <workspace>
```

The command locates a Git checkout or extracted Release Bundle from `--repo`, the current directory and its parents, or `MULTICA_WORKFLOW_REPO`. It accepts optional `--multica-bin` and `--profile` overrides, runs `workflow.py doctor`, then returns the exit status from a fresh `workflow.py drift` call.

Exit `0` means the source is valid and no mutation remains, `1` means drift remains, and `2` means validation or reconciliation is blocked. The command refuses daemon-managed Agent tasks and never approves, applies, publishes, or manages Incidents.
