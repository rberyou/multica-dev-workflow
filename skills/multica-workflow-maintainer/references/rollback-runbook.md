# Rollback Runbook

1. Stay on the new release and generate `plan --disable-operations`.
2. Active v3 top-level requirements block rollback unless they are frozen or the approved Plan explicitly includes `--allow-active-v3-degraded` and lists the degraded requirements.
3. Approve and Apply the digest to pause the Observer Autopilot and detach Reporter capability from development Agents.
4. Checkout the previous stable tag.
5. Generate, approve and Apply the ordinary rollback deployment Plan.
6. Verify the previous managed scope without deleting Incident history or idle control-plane objects.
