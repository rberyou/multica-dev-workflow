# Workflow Console and Daily Operation

Status: Phase 1 host tooling.

The current console is host-only and read-only. Phase 1 does not deploy Workflow Maintainer or Maintenance Reviewer Agents, does not run their automatic loop, and does not require a Secure Runtime binding.

## Host Status

```text
python skills/multica-workflow-console/scripts/workflow_console.py status \
  --repo <checkout> \
  --workspace <workspace>
```

The console refuses to run when Multica Agent task variables are present. It reports repository, release and workflow state but does not approve, merge, publish, deploy or mutate a Maintenance Case.

## Phase 1 Human Actions

Human participation is limited to decision-bearing gates:

1. Approve or defer Incident maintenance with the exact digest-bound comment produced by Observer.
2. Approve the ordinary development Plan and final Requirement according to the normal development workflow.
3. Approve a release with `APPROVE WORKFLOW RELEASE <digest>` on the selected completed Requirement or approved Maintenance Case.
4. Approve the waiting `workflow-release` Environment deployment in GitHub.
5. Approve each separate Workspace deployment Plan.

The implementation and review loop uses the ordinary Integrator, Developer and Code Reviewer roles. For an Incident fix, the Integrator writes the final Requirement and integration-validation evidence back to the minimal Maintenance Case. Observer remains responsible for independent post-deployment verification and Incident closure.

## Future Components

`skills/multica-workflow-maintainer/`, the Maintainer/Reviewer instructions and `secure-runtime/` remain source for Phase 2 and Phase 3 development. They are not part of `workflow.json` desired state, default Skill installation, Phase 1 CI or Phase 1 release assets. Their validation is isolated in `.github/workflows/validate-future-components.yml`.

The historical `maintenance_loop.py`, Broker and Secure Runtime commands must not be used as active Phase 1 operating instructions.
