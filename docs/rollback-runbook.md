# Workflow Rollback Runbook

A rollback is another workspace deployment from a selected clean checkout.

1. Select the reviewed commit or tag that should become desired state.
2. Ensure the checkout is clean.
3. Run `doctor`, then generate a new workspace `plan`.
4. Review every mutation and approve the new short digest.
5. Run `apply` and a fresh `verify`.
6. If the rollback relates to an Incident, keep it open until the affected behavior is checked.

Do not reset or rewrite workspace Incident history. Do not reuse a Plan generated from a different checkout or observed workspace state.

If an Apply command fails after changing part of the workspace, retain its journal and generate a fresh Plan. The fresh Plan must describe only the remaining mutations; review and approve its new digest before retrying.
