# Workflow Console

The console is a host-only read-only convenience command:

```text
python skills/multica-workflow-console/scripts/workflow_console.py status --repo <checkout> --workspace <workspace>
```

If `--repo` is omitted, the command searches the current directory and its parents, then `MULTICA_WORKFLOW_REPO`. `--profile` and `--multica-bin` are optional host-local overrides.

It validates the checkout with `workflow.py doctor` and then runs `workflow.py drift` against the current workspace. Exit status is zero only when both checks pass and no drift exists.

The console refuses to run when daemon Agent task variables are present. It does not approve a Plan, mutate Multica, publish a release, or manage Incidents. Incident commands are invoked explicitly at the event where a workflow problem is found.
