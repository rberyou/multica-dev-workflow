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
| `~/.agents/skills/` | Machine-global, cross-host Local Skills installed only as workflow-owned physical directory copies. |

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
| `doctor` | Read-only | Validate the checkout and CLI prerequisites, resolve profile and Workspace, load the selected Runtime map, report online provider counts, and check the resolved Local Skill root is safe and writable/creatable. |
| `export` | Local write only | Fetch current Workspace state, redact secrets, and save a diagnostic snapshot under `exports/`. |
| `plan` | Local write only | Compare source desired state with current Multica and Local Skill state and save one digest-bound Plan. It never mutates the Workspace or Skill root. |
| `drift` | Read-only | Perform the same comparison without saving a Plan. Exit `0` for no drift, `1` for mutation actions, and `2` for blockers. |
| `verify` | Read-only | Perform a fresh comparison and report `OK`, `DRIFT`, or `BLOCKED`; also display the latest local deployment record when present. |
| `apply` | Workspace and local write | Revalidate and execute the exact reviewed Plan, write a journal and deployment record, then run a fresh verification. |
| `install-skills` | Local write only | Repair/install local-target Skills using the same copy-only observation, ownership, staging, digest verification, and rollback logic as Apply. |

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

Apply accepts only the full Plan digest or its first 12 characters. It rechecks the clean source identity, desired-source hash, Runtime map hash, Workspace identity, observed Multica state, resolved Local Skill root, destination types, ownership, and content digests. Any change requires a fresh Plan and approval.

Do not confuse this gate with development approvals. `APPROVE PLAN vN` is posted on the current Plan approval target. `APPROVE REQUIREMENT vN` is posted only on the top-level Requirement after its revision/head/policy-bound final gate is open.

## Failure and Retirement Behavior

Stop on a missing CLI, authentication failure, Workspace or Runtime ambiguity, invalid approver roster, an unreviewed adoption, or any `BLOCKED` action.

If Apply stops before the Workspace phase finishes, retain the journal and generate a fresh Plan. If the Workspace phase finished and Local Skill publishing alone was interrupted, retrying the same approved Plan validates completed local actions and resumes the journal without repeating Workspace mutations. A fresh Plan can also reconcile the partial machine state. Retirement actions remove retired Agents from the managed Squad, delete dependent automations, reassign preserved Projects, archive the Agents, and delete retired Skills. Historical Projects and Issues remain.

Apply refuses `MULTICA_AGENT_ID` or `MULTICA_TASK_ID` by default. Use `--allow-agent-identity` only as an explicitly reviewed break-glass action.

A reviewed clean checkout on any branch may be deployed without a tag or GitHub Release. An extracted formal repository archive may also be deployed without `.git`; `release-manifest.json` verifies every published file. The deployment record binds the portable source identity, provenance commit, and Plan digest.

## Incident Wrappers

Repository wrappers are event-driven human-host commands; nothing invokes them on a schedule:

```text
python scripts/workflow.py bind-workflow-issue --workspace <workspace> --issue <issue> --object-type <type> --created-by-role <role>
python scripts/workflow.py report-incident --workspace <workspace> --source-issue <issue> --rule-id <stable-rule-id> --severity <low|medium|high|urgent> --summary <text> --expected <text> --actual <text> --evidence <redacted-evidence>
python scripts/workflow.py create-incident-fix-requirement --workspace <workspace> --incident <incident> --project <external-project> [--assignee-id <external-owner>]
python scripts/workflow.py link-incident-fix --workspace <workspace> --incident <incident> --requirement <requirement>
python scripts/workflow.py close-incident --workspace <workspace> --incident <incident> --result passed --evidence <text> --fix-reference-type <type> --fix-reference <immutable-reference> --deployment-verification-reference-type <type> --deployment-verification-reference <immutable-reference>
python scripts/workflow.py close-incident --workspace <workspace> --incident <legacy-incident> --result passed --evidence <text> --source-commit <40-hex> --deployment-plan-digest <64-hex>
python scripts/workflow.py close-incident --workspace <workspace> --incident <legacy-incident> --result passed --closure-mode independent_remediation --evidence <text> --fix-reference-type <type> --fix-reference <immutable-reference> --deployment-verification-reference-type <type> --deployment-verification-reference <immutable-reference>
```

The create wrapper requires an explicitly resolved external Project and never defaults to the managed Incident Project or development Squad. `report-incident` remains independent and does not create a fix Requirement. `independent_remediation` is a narrow closure only for an unchanged linked legacy ordinary Requirement whose status is `cancelled` while the Incident remains `in_fix`; it does not replace the fix or traverse descendants.

Managed Agents use the attached Incident Skill's direct commands because product repositories do not contain this workflow repository's `scripts/workflow.py`.

## Formal Release

```text
python scripts/release.py doctor
python scripts/release.py plan --version <version>
python scripts/release.py publish --plan <release-plan>
git fetch --tags
python scripts/release.py verify-tag --tag <v-version>
```

Formal Release is optional and separate from Workspace deployment. It requires a clean `main` checkout with matching VERSION, workflow version, active Skill versions, and Changelog heading. `publish` rebuilds assets and creates an allowlisted workflow archive containing only the manifest, schema, deployment profiles, runtime instructions, required deployment scripts, active Skills, version, dependency declaration, and `release-manifest.json`. GitHub metadata, tests, top-level development documentation, and release tooling are excluded. The command refuses a daemon-managed Agent identity and uses the authenticated human host's `gh release create`. The extracted workflow archive is then a deployable source without a Git clone.

## Local Skill Installation

```text
python scripts/workflow.py install-skills
```

The default target is `~/.agents/skills`, the machine-global root shared by Codex and OpenCode discovery. Installation always uses physical directory copies; the command never invokes symlink or Windows junction creation. Use `--target` for an explicit alternate root. `--replace-existing` is a compatibility option only: it may update a destination whose `SKILL.md` proves the exact Skill name, `managed_by=multica-dev-workflow`, and `workflow_id=development-delivery`; it never authorizes foreign replacement or adoption.

The normal `doctor -> plan -> approval -> apply -> verify` sequence already installs every manifest Skill with a `local` target. `install-skills` is an explicit repair/standalone entry, not a way around Plan approval for normal workflow publishing. Creation and updates copy into a temporary sibling, verify the exact file set and SHA-256 digest, switch through a rollback backup, and restore the prior owned destination if replacement fails. Owned symlinks/junctions are migrated to copies; unverifiable or foreign same-name targets block active installation. Retired targets are deleted only when the same ownership metadata is readable and valid; foreign retired names are preserved.
