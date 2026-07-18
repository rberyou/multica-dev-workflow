# Workflow Rollback Runbook

The generated Observer contract treats the reviewed disabled operations state as valid: the Observer Autopilot is paused while Reporter Skill assignments remain available for bounded incident reporting.

1. Stay on the current release and run `plan --disable-operations`.
2. If active v3 top-level requirements exist, the Plan blocks. Freeze them first, or deliberately regenerate with `--allow-active-v3-degraded` so the degraded set is visible and bound by the digest approval.
3. Review/approve/apply the digest to pause the Observer Autopilot while retaining Reporter capability on development Agents.
4. Checkout the previous stable tag.
5. Generate and approve the ordinary deployment Plan.
6. Apply and verify the old managed scope.

Do not delete or rewrite Incident history, tags, Releases, the Operations Project, control-plane Agents or Skills. Never roll back to a tainted release. The old reconciler ignores unrelated idle objects; roll-forward can adopt/resume them through managed markers.
