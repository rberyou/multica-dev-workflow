# RC4 Bootstrap and Rollback

The first secure-runtime deployment cannot be performed by the insecure Agent
Runtime it is replacing. RC4 uses the reviewed external-patch/human-push path.
No long-lived bootstrap credential is introduced.

## Prerequisites

The human owner performs these actions outside Agent runtimes:

1. Commit and push the independently reviewed RC4 patch.
2. Create a Maintainer GitHub App limited to this repository with exactly:
   Actions read, Contents write, Metadata read, Pull requests write.
3. Create a Dispatcher GitHub App limited to this repository with exactly:
   Actions write, Contents read, Metadata read. Keep its private key outside
   Agent runtimes and GitHub Actions; use it only to mint short-lived human-host
   dispatch tokens.
4. Create a Publisher GitHub App limited to this repository with exactly:
   Contents write and Metadata read.
5. Store the Publisher App ID in `WORKFLOW_PUBLISHER_APP_ID` and its private key
   in `WORKFLOW_PUBLISHER_PRIVATE_KEY` on the protected `workflow-release`
   Environment.
6. Create `workflow-release-tags` for `refs/tags/v*`; creation, update, and
   deletion are restricted, and the Publisher App is the sole always-bypass.
7. Keep the Environment on `main`, require an isolated human reviewer, prevent
   self-review, and disable administrator bypass when GitHub exposes the option.
8. Record both reviewed App installations, the Environment, and Ruleset readback in
   `docs/release-control-evidence.json`.
9. Prepare a dedicated Multica profile directory and dedicated Codex
   `auth.json`. Do not reuse the host user's Codex home.

## Phase 1: Start the Isolated Runtime

Generate provisional bindings from the existing managed Agent identities. The
temporary UUID is only a startup placeholder and is explicitly marked
`bootstrap`:

```text
python scripts/workflow.py secure-bindings \
  --bootstrap-runtime-id <new-random-uuid> \
  --output agent-bindings.bootstrap.local.json
```

In an elevated PowerShell session, extract the reviewed secure-runtime asset and
run `install.ps1` with:

- the extracted `bin` directory;
- the reviewed repository checkout as `WorkflowBundleRoot`;
- the real `multica.exe`, real `codex.exe`, and `git.exe` paths;
- the dedicated Multica profile and Codex auth paths;
- the Maintainer App ID, installation ID, and private key;
- the provisional bindings file.

The installer creates the service, applies ACLs and firewall rules, and starts
the daemon. Reinstall uses a sibling rollback directory and does not delete the
previous installation until the new Broker and Multica daemon report ready.
`doctor.ps1` is expected to remain blocked on
`agent.bindings.final` during this phase.

## Phase 2: Rebind and Finalize

After the new Multica Runtime is online:

1. Generate and review a normal workflow deployment Plan using
   `--rebind-runtimes` and the intended deployment profile.
2. Obtain the exact `APPROVE WORKFLOW PLAN <digest>` approval.
3. Apply and verify the Plan from the human-controlled host context.
4. Generate final bindings without `--bootstrap-runtime-id`:

```text
python scripts/workflow.py secure-bindings \
  --output agent-bindings.final.local.json
```

5. Install the final file and restart the service:

```text
powershell -File secure-runtime/install/windows/update-bindings.ps1 \
  -AgentBindings agent-bindings.final.local.json
```

6. Run `doctor.ps1`. Do not start maintenance tasks until every check is ready.
7. Run one Maintainer negative probe and one tokenless Reviewer negative probe.
8. Keep Observer paused until Canary validation reports no false positives.

## Publisher Activation

Before `release.py doctor`, `approval-block` or `apply`, the human owner mints a
short-lived Dispatcher installation token outside Agent runtimes and exports it
only as `GH_TOKEN` for those commands. The commands verify the exact App ID,
installation ID, selected repository and permission set before continuing. The
token is removed immediately after dispatch. Normal host `gh` and SSH credentials
do not need to be removed.

Release mutation remains separate from the Maintainer App. The release workflow:

1. validates the Release Request with the read-only built-in token;
2. records a digest-bound publish gate after Environment approval;
3. mints the Publisher token only after that gate;
4. verifies the exact App installation and permissions;
5. creates the annotated tag through the Git database REST API;
6. verifies tag provenance with the read-only token;
7. publishes the exact approved assets with the Publisher token.

The Publisher private key never enters a Multica Agent or host Console process.

## Rollback

If installation or validation fails:

1. Keep Observer paused and block new maintenance tasks.
2. Run `uninstall.ps1` without `-PurgeCredentials` to disable the service and
   firewall rules while preserving forensic material.
3. Generate a reviewed workflow Plan that rebinds affected Agents to the last
   approved Runtime. Apply only after the exact Plan digest is approved.
4. Restore the prior reviewed secure-runtime asset and local configuration, or
   repair RC4 and rerun both bootstrap phases.
5. Use `-PurgeCredentials` only after forensic review and explicit human
   approval; it removes central credentials and residual task homes. Revoke the
   Maintainer App installation and rotate Codex auth.
6. Never reset or force-push repository history. Never delete, move, or replace
   a published `v*` tag. Release recovery reuses the original Request digest and
   requires a new protected Environment approval.
