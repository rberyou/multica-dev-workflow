---
name: multica-workflow-console
description: Read-only host console for inspecting Multica workflow health, blocked decisions, Plan gates, release gates, and Observer state without exposing host credentials to managed Agent runtimes.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 1.1.0-rc.4
---

# Multica Workflow Console

This RC4 Skill is host-only and read-only. It is not attached to any managed Multica Agent.

Use `scripts/workflow_console.py status` to inspect workflow health and active gates. The command refuses to run inside a Multica Agent task.

Mutating commands such as Plan approval, decisions, release approval and GitHub Environment approval remain manual in RC4. They are intentionally deferred until a later reviewed human-presence design.

Read `docs/workflow-console.md` in the workflow repository for the Phase 1 status checks, ordinary-development evidence handoff and exact human gate locations.
