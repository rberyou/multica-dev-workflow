# Workflow Console and Daily Operation

RC4 reduces normal human interaction to decision-bearing gates. Maintainer and
Reviewer handoffs, changes requested, reimplementation, retesting, rereview, and
CI polling run through the original Maintenance Issue without repeated user
acknowledgements.

## Host Status

The console is host-only and read-only:

```text
python skills/multica-workflow-console/scripts/workflow_console.py status \
  --repo <checkout> \
  --workspace <workspace>
```

The packaged `workflow-console.exe` exposes the same `status` operation. It
refuses to run when Multica Agent task variables are present. Agent sandboxes do
not receive host `gh`, SSH, Git, browser, or Credential Manager state.

## Automatic Loop

Maintainer requests review with:

```text
python skills/multica-workflow-maintainer/scripts/maintenance_loop.py \
  request-review --issue <issue> --kind <plan|implementation|rollback|release> \
  --plan-revision <revision> --commit <full-sha>
```

Reviewer records `APPROVED`, `CHANGES_REQUESTED`, or `DECISION_REQUIRED` with:

```text
python skills/multica-workflow-maintainer/scripts/maintenance_loop.py \
  review --issue <issue> --verdict <verdict> \
  --plan-revision <revision> --commit <full-sha>
```

`CHANGES_REQUESTED` automatically returns the Issue to the Maintainer.
`APPROVED` advances to the next configured gate. `DECISION_REQUIRED` blocks and
assigns the durable human approver.

## Human Actions

Only these actions require the user:

1. Decision: comment `DECISION: <decision>` on the blocked Maintenance Change.
2. Plan approval: comment `APPROVE WORKFLOW PLAN <revision-or-digest>` at the
   Plan gate specified by the Issue.
3. Release approval: comment `APPROVE WORKFLOW RELEASE <digest>` on the selected
   Maintenance Change.
4. GitHub release approval: approve the waiting `workflow-release` Environment
   deployment in GitHub.

RC4 deliberately does not implement mutating `workflow approve-plan`,
`workflow decide`, or `workflow approve-release` host commands. Human approval
credentials remain outside Agent and service runtimes. A later release may add
those commands only with an independently reviewed human-presence and credential
boundary.

## Maintainer GitHub Commands

The Maintainer never runs `gh` for writes and never receives a raw token. From
its current task branch it may use:

```text
workflow-token-broker push-task-branch --branch <current-branch>
workflow-token-broker upsert-pr --branch <current-branch> --base main \
  --title-file <file> --body-file <file>
workflow-token-broker read-ci --branch <current-branch>
```

The Broker rejects non-current branches, main, tag-like refs, out-of-profile
prefixes, dangerous Git configuration, and Git metadata outside the secure
workspaces root.
