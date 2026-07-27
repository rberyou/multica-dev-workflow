---
name: multica-workflow-console
description: Check repository validity and current Multica workflow drift through a read-only host console. Use when the user asks for workflow status, drift, deployment readiness, or a non-mutating health check from a workflow checkout.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.2
---

# Multica Workflow Console

This Skill is host-only, read-only, and is not attached to managed Agents.

Run from the Skill directory or pass the script by absolute path:

```text
python scripts/workflow_console.py status --repo <workflow-checkout> --workspace <workspace>
```

The command locates the repository from `--repo`, the current directory and its parents, or `MULTICA_WORKFLOW_REPO`. It accepts optional `--multica-bin` and `--profile` overrides, runs `workflow.py doctor`, then returns the exit status from a fresh `workflow.py drift` call.

Exit `0` means the checkout is valid and no mutation remains, `1` means drift remains, and `2` means validation or reconciliation is blocked. The command refuses daemon-managed Agent tasks and never approves, applies, publishes, or manages Incidents.
