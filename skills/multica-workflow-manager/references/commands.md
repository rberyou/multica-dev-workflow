# Workflow Manager Command Reference

## Contents

- Context selectors
- Local state layout
- Workspace command responsibilities
- Workspace deployment approval
- Failure and retirement behavior
- Incident wrappers
- Formal Release
- Local Skill installation

Install repository dependencies once with `python -m pip install -r requirements.txt`.

## Context Selectors

| Option | Meaning |
|---|---|
| `--profile <name>` | Select a Multica CLI configuration profile. It isolates server/auth configuration, daemon state, and known Workspaces. It is not a model profile. |
| `--workspace <id-or-slug>` | Select the target Multica Workspace. Workspace UUIDs and object UUIDs must remain local. |
| `--deployment-profile <name>` | Select the repository policy that maps logical Runtime bindings to providers, models, and thinking levels. |
| `--runtime-map <path>` | Override the default Workflow/Workspace-specific Runtime map path. Use only when explicitly required. |

## Local State Layout

Keep machine configuration in the current user's Home directory:

| Location | Ownership |
|---|---|
| `~/.multica/profiles/` | Multica server and authentication profiles. |
| `~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json` | Concrete Runtime selection shared by checkouts of the same Workflow on this machine. |

Keep execution state beside the selected Git checkout or extracted Release Bundle:

| Location | Ownership |
|---|---|
| `<source>/.multica/plans/` | Digest-bound Workspace deployment Plans. |
| `<source>/.multica/journals/` | Apply progress and recovery journals. |
| `<source>/.multica/deployments/` | Latest and immutable deployment evidence. |
| `<source>/.multica/release-plans/` | Formal GitHub Release Plans created from Git source. |
| `<source>/.multica/worktrees/` | Optional local worktrees created from Git source. |
| `<source>/exports/` | Redacted diagnostic Workspace snapshots. |

Neither area belongs in Git or a Release Bundle. Do not place source-local Plans or evidence in Home, and do not place Runtime UUID maps inside a source directory.

## Workspace Command Responsibilities

| Command | State effect | Responsibility |
|---|---|---|
| `doctor` | Read-only | Validate the checkout and CLI prerequisites, resolve profile and Workspace, load the selected Runtime map, and report online provider counts. |
| `export` | Local write only | Fetch current Workspace state, redact secrets, and save a diagnostic snapshot under `exports/`. |
| `plan` | Local write only | Compare Git desired state with current Multica state and save a digest-bound Plan. It never mutates the Workspace. |
| `drift` | Read-only | Perform the same comparison without saving a Plan. Exit `0` for no drift, `1` for mutation actions, and `2` for blockers. |
| `verify` | Read-only | Perform a fresh comparison and report `OK`, `DRIFT`, or `BLOCKED`; also display the latest local deployment record when present. |
| `apply` | Workspace and local write | Revalidate and execute the exact reviewed Plan, write a journal and deployment record, then run a fresh verification. |
| `install-skills` | Local write only | Install locally targeted Skills into the selected Skill root using links/junctions by default. |

Run the ordinary deployment sequence:

```text
python scripts/workflow.py doctor --workspace <id-or-slug> --deployment-profile quality
python scripts/workflow.py plan --workspace <id-or-slug> --deployment-profile quality
python scripts/workflow.py apply --plan .multica/plans/<plan>.json --approve <short-digest>
python scripts/workflow.py verify --workspace <id-or-slug> --deployment-profile quality
```

Run `export` before planning only when a redacted diagnostic snapshot is useful.

Use `--adopt` only after reviewing an existing unmarked same-name object. Use `--allow-dirty` only to inspect a draft Plan; draft Plans cannot be applied.

## Workspace Deployment Approval

When Codex operates the deployment, present the Plan actions and wait for the user to reply in the current Codex task:

```text
APPROVE WORKFLOW PLAN <short-digest>
```

This is not a Multica Issue or GitHub comment. If the user operates the terminal directly, reviewing the Plan and supplying the digest through `apply --approve` is the approval action. The digest is not a credential.

Apply accepts only the full Plan digest or its first 12 characters. It also rechecks the clean Git commit, desired-source hash, Runtime map hash, Workspace identity, and observed Multica state. Any change requires a fresh Plan and approval.

Do not confuse this gate with development approvals. `APPROVE PLAN vN` and `APPROVE REQUIREMENT vN` are comments on the corresponding Multica workflow Issues from the configured human approver.

## Failure and Retirement Behavior

Stop on a missing CLI, authentication failure, Workspace or Runtime ambiguity, invalid approver roster, an unreviewed adoption, or any `BLOCKED` action.

If Apply partially mutates state and stops, retain the journal and generate a fresh Plan. Never reuse the stale Plan. Retirement actions remove retired Agents from the managed Squad, delete dependent automations, reassign preserved Projects, archive the Agents, and delete retired Skills. Historical Projects and Issues remain.

Apply refuses `MULTICA_AGENT_ID` or `MULTICA_TASK_ID` by default. Use `--allow-agent-identity` only as an explicitly reviewed break-glass action.

A reviewed clean checkout on any branch may be deployed without a tag or GitHub Release. An extracted formal repository archive may also be deployed without `.git`; `release-manifest.json` verifies every published file. The deployment record binds the portable source identity, provenance commit, and Plan digest.

## Incident Wrappers

Repository wrappers are event-driven human-host commands; nothing invokes them on a schedule:

```text
python scripts/workflow.py bind-workflow-issue --workspace <workspace> --issue <issue> --object-type <type> --created-by-role <role>
python scripts/workflow.py report-incident --workspace <workspace> --source-issue <issue> --rule-id <stable-rule-id> --severity <low|medium|high|urgent> --summary <text> --expected <text> --actual <text> --evidence <redacted-evidence>
python scripts/workflow.py link-incident-fix --workspace <workspace> --incident <incident> --requirement <requirement>
python scripts/workflow.py close-incident --workspace <workspace> --incident <incident> --result <passed|failed> --evidence <text>
```

Managed Agents use the attached Incident Skill's direct commands because product repositories do not contain this workflow repository's `scripts/workflow.py`.

## Formal Release

```text
python scripts/release.py doctor
python scripts/release.py plan --version <version>
python scripts/release.py publish --plan <release-plan>
git fetch --tags
python scripts/release.py verify-tag --tag <v-version>
```

Formal Release is optional and separate from Workspace deployment. It requires a clean `main` checkout with matching VERSION, workflow version, active Skill versions, and Changelog heading. `publish` rebuilds assets, adds `release-manifest.json` to the repository archive, refuses a daemon-managed Agent identity, and uses the authenticated human host's `gh release create`. The extracted repository archive is then a deployable source without a Git clone.

## Local Skill Installation

```text
python scripts/workflow.py install-skills
```

The default target is `~/.agents/skills`. Prefer links or Windows junctions. Use `--copy` only when links are unavailable, `--target` for another Skill root, and `--replace-existing` only after reviewing the existing destination. Retired Observer/Maintainer destinations are removed only when their metadata proves this workflow owns them.
