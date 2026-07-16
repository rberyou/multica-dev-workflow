# Workflow Rollback Runbook

The generated Observer contract treats the reviewed disabled operations state as valid: the Observer Autopilot is paused and the Observer Skill remains only on the Observer Agent. Any partial pause or detach still reports drift.

1. Stay on the current release and run `plan --disable-operations`.
2. If active v3 top-level requirements exist, the Plan blocks. Freeze them first, or deliberately regenerate with `--allow-active-v3-degraded` so the degraded set is visible and bound by the digest approval.
3. Review/approve/apply the digest to pause the Observer Autopilot and detach Reporter capability from development Agents.
4. Checkout the previous stable tag.
5. Generate and approve the ordinary deployment Plan.
6. Apply and verify the old managed scope.

Do not delete Incident history, the Operations Project, control-plane Agents or Skills. The old reconciler ignores these unrelated idle objects; roll-forward can adopt/resume them through managed markers.
