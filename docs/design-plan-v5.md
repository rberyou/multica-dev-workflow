# Multica Workflow as Code - Plan v5

## 1. Status

- Plan revision: `v5`
- Status: `in_review`
- Scope: design only; do not create a GitHub repository or mutate Multica before approval
- Approval command: `APPROVE WORKFLOW PLAN v5`

## 2. Objective

Create a Git-versioned, portable and idempotent way to manage the complete Multica development workflow, including:

- development squad definition and instructions;
- agent roles, models, thinking levels and concurrency;
- runtime selection without committing environment-specific UUIDs;
- human approver roster contract;
- local and Multica workspace skills;
- rebuild, update, drift detection and verification;
- versioned releases that external Codex/OpenCode agents can apply.

Git is the desired-state source of truth. Multica is the runtime state.

## 3. Non-Goals for v1

- Do not deploy from GitHub Actions directly into Multica.
- Do not store PATs, OAuth tokens, MCP secrets or agent custom environment values in Git.
- Do not install or upgrade Codex, OpenCode, Multica Desktop or Multica runtimes automatically.
- Do not call undocumented Multica HTTP APIs when the CLI lacks a required operation.
- Do not automatically delete or archive unmanaged Multica objects.
- Do not implement bidirectional live synchronization.

## 4. Repository

Recommended repository name: `multica-dev-workflow`.

Start as a private repository. The authenticated GitHub account can later make it public after the instructions and history have been checked for sensitive content.

```text
multica-dev-workflow/
├── README.md
├── VERSION
├── workflow.json
├── workflow.schema.json
├── instructions/
│   ├── common.md
│   ├── squad.md
│   └── roles/
│       ├── leader.md
│       ├── planner.md
│       ├── plan-reviewer.md
│       ├── integrator.md
│       ├── developer.md
│       └── code-reviewer.md
├── deployment-profiles/
│   ├── quality.json
│   ├── codex-only.json
│   └── opencode-only.json
├── skills/
│   ├── multica-requirement-intake/
│   └── multica-workflow-manager/
├── scripts/
│   ├── workflow.py
│   └── package_skills.py
├── tests/
│   ├── fixtures/
│   ├── test_manifest.py
│   ├── test_reconcile.py
│   └── test_portability.py
├── .github/
│   └── workflows/validate.yml
├── CHANGELOG.md
└── .gitignore
```

Use JSON rather than YAML for the canonical manifest in v1. JSON allows a dependency-free Python reconciler, deterministic serialization and direct JSON Schema validation. Long instructions remain readable Markdown files.

## 5. Declarative Manifest

The committed manifest contains stable logical keys and portable selectors. It must not contain Multica UUIDs or machine paths.

```json
{
  "$schema": "./workflow.schema.json",
  "schema_version": 1,
  "workflow": {
    "id": "development-delivery",
    "name": "开发交付小队",
    "version": "1.0.0",
    "protocol_revision": "v2",
    "approver_role": "人工审批人"
  },
  "skills": [
    {
      "key": "requirement-intake",
      "name": "multica-requirement-intake",
      "path": "skills/multica-requirement-intake",
      "targets": ["local", "workspace"],
      "attach_to": []
    }
  ],
  "agents": [
    {
      "key": "leader",
      "name": "开发队长",
      "description": "控制需求阶段、审批门禁和最终交付",
      "runtime_binding": "codex-quality",
      "instruction_files": [
        "instructions/common.md",
        "instructions/roles/leader.md"
      ],
      "max_concurrent_tasks": 1,
      "permission_mode": "public_to_workspace"
    }
  ],
  "squad": {
    "key": "development-delivery",
    "name": "开发交付小队",
    "leader": "leader",
    "instruction_files": ["instructions/squad.md"],
    "agent_members": [
      {"agent": "leader", "role": "leader"}
    ],
    "human_members": [
      {"selector": "current_user", "role": "人工审批人"}
    ]
  }
}
```

The actual manifest will contain all seven agents and both developer instances. Repeated instances may reference the same role instruction file while retaining distinct stable keys such as `developer-a` and `developer-b`.

`workflow.version` is the repository release version. `workflow.protocol_revision` represents the cross-role workflow contract currently described as workflow v2. Protocol revision is included in `common.md`; changing it intentionally updates every role. A release-version change alone does not.

## 6. Instruction Composition

Multica stores one flat instruction string per agent. The reconciler composes it in this order:

1. generated ownership header;
2. `instructions/common.md`;
3. role-specific Markdown files in manifest order.

Generated header:

```text
<!-- multica-workflow
workflow_id=development-delivery
object_key=agent.leader
spec_hash=<sha256>
-->
```

The hash is calculated from only the normalized effective fields of that object. Global workflow version, Git commit, resolved UUIDs and local machine bindings are excluded. Git commit and workflow version are stored in deployment output rather than every instruction header, so an unrelated release or repository commit does not trigger updates to every agent.

The same marker is added to squad instructions. It provides portable object ownership without relying on UUIDs or a local state file.

The current CLI accepts instructions as command arguments but has no `--instructions-file` flag. The reconciler must invoke the CLI directly with an argument array, never through shell interpolation, and must block if the platform command-line limit would be exceeded. It must not fall back to a private API.

## 7. Runtime Separation

Runtime requirements belong to a deployment profile. Exact runtime UUIDs belong to a local binding file.

Example committed deployment profile:

```json
{
  "name": "quality",
  "bindings": {
    "codex-quality": {
      "provider": "codex",
      "model": "gpt-5.5",
      "thinking_level": "xhigh",
      "required_status": "online"
    },
    "opencode-review": {
      "provider": "opencode",
      "model": "team-litellm/gpt-5.5",
      "thinking_level": "",
      "required_status": "online"
    }
  }
}
```

Example local, gitignored file:

```json
{
  "workspace": "T0",
  "bindings": {
    "codex-quality": {"runtime_id": "<local-runtime-uuid>"},
    "opencode-review": {"runtime_id": "<local-runtime-uuid>"}
  }
}
```

Resolution rules:

1. Use an explicit local runtime binding when present.
2. Otherwise query `multica runtime list --output json` and filter by provider, status and optional selector fields.
3. If one runtime matches, use it and offer to write the local binding file.
4. If multiple runtimes match, stop and require selection.
5. If none match, stop with `waiting_on=runtime_binding`; do not silently switch providers.

Runtime mutation policy:

- Existing managed agents preserve their current Runtime ID when it still satisfies the required provider and the user did not request rebinding.
- Local runtime maps are required for creating agents in a new workspace and for explicit runtime migrations.
- Updating instructions from a second computer does not rebind agents to that computer's Runtime by default.
- Changing an existing agent's Runtime requires `--rebind-runtimes`; the plan must show every affected role and old/new Runtime.
- A Runtime that is offline or incompatible blocks planning; it is never silently replaced.

The portable agent spec hash includes the logical Runtime binding, provider-specific model and thinking level, but not the resolved Runtime UUID. The effective plan separately records the approved Runtime decision. In preserve mode, the current compatible Runtime ID remains valid across computers.

Runtime drift levels:

- Without a local pin, `drift` validates provider/model/thinking compatibility and reports the concrete Runtime ID as informational.
- With a local pin, a changed Runtime ID is actionable drift.
- A strict cross-computer Runtime pin is outside v1 because it would require shared environment state separate from the portable workflow manifest.

`codex-only` and `opencode-only` profiles allow the same squad workflow to be rebuilt with different runtime families. Model names remain provider-specific in the selected profile.

## 8. Human Approver

The manifest stores only the role contract, never the member UUID.

For a new workspace, `selector=current_user` resolves through `multica user profile get --output json`. The plan must show which member will receive the `人工审批人` role.

For an existing workspace:

- preserve the unique existing roster member with that role;
- block when zero or multiple approvers exist unless an explicit adoption choice is provided;
- never infer the approver from squad creator, workspace owner or display name.

The squad leader continues to discover the approver UUID from the injected roster during requirement execution.

## 9. Object Identity and Adoption

Committed logical keys are stable identities. Multica UUIDs are runtime results.

Resolution order for managed objects:

1. existing object with the matching management marker and logical key;
2. exact-name unmarked object only when `--adopt` is explicitly supplied;
3. create a new object when no match exists.

If an unmarked exact-name object exists without `--adopt`, planning fails instead of overwriting it.

Renames require `previous_names` in the manifest or an explicit migration entry. The reconciler must not treat a rename as create-plus-archive automatically.

Local state under `.multica/state/` may cache resolved IDs and apply journals, but it is gitignored and never authoritative.

The entire repository-local `.multica/` directory, including plans, journals, deployment caches and Runtime maps, is gitignored.

Managed skills use frontmatter metadata containing `managed_by` and `workflow_id`. Import with overwrite is allowed only when the existing workspace skill carries the matching marker or the user explicitly approves skill adoption. A same-name unmarked skill is blocked rather than overwritten.

## 10. Reconciler Commands

The v1 reconciler is a Python standard-library CLI using JSON and `subprocess`. It invokes only documented `multica`, `git` and optional `gh` commands.

### `doctor`

Checks:

- Python version and repository structure;
- clean or explicitly allowed dirty Git state;
- Multica CLI discovery, version and authentication;
- profile and workspace resolution;
- runtime binding availability;
- current user and approver eligibility;
- required Multica CLI command surface;
- command-line instruction size limits.

It performs no mutation.

### `export`

Exports a read-only snapshot of current agents, squad, roster, skills and runtimes.

- Output goes to `exports/<workspace>/<timestamp>/`.
- It never overwrites canonical manifests or prompts.
- Secret custom environment values and MCP secrets are never exported.
- Runtime, agent, squad and member UUIDs may appear only in snapshot files, which are gitignored by default.
- `export --bootstrap` may generate candidate portable files in a separate review directory.

### `plan`

Compares desired Git state with current Multica state and writes an immutable plan file:

```text
.multica/plans/<timestamp>-<short-digest>.json
```

Each action records:

- `CREATE`, `ADOPT`, `UPDATE`, `ATTACH_SKILL`, `ADD_MEMBER`, `SET_ROLE`, `NO_CHANGE` or `BLOCKED`;
- logical key and resolved current object ID;
- current and desired spec hashes;
- changed fields without secret values;
- source commit, workflow version and runtime-map hash;
- precondition hash of the observed Multica state.

The external management Skill must present this plan to the user and wait for the exact command:

```text
APPROVE WORKFLOW PLAN <short-digest>
```

### `apply`

`apply` accepts a previously generated plan file and approval digest.

Before mutation it verifies:

- Git HEAD still equals the plan's source commit;
- the working tree is clean unless explicitly allowed;
- workflow and runtime-map hashes still match;
- Multica precondition hashes have not drifted;
- the approval digest matches the plan.

If any check fails, abort and require a new plan.

Apply order:

1. package and import/update workspace skills;
2. create or update agents;
3. reconcile agent-skill assignments;
4. create or update squad instructions and leader;
5. reconcile agent roster roles;
6. reconcile the human approver role;
7. run full verification;
8. write an apply journal and deployment record.

Squad creation is multi-step in the current CLI. A partial failure is recovered by re-running planning and applying the remaining idempotent actions. The reconciler does not attempt a destructive transaction rollback.

Runtime updates are omitted unless the plan was generated with `--rebind-runtimes` or an agent is being created.

Skill assignment policy:

- The manifest manages only skills carrying this workflow's management marker.
- Desired managed skills are added without removing unrelated existing skills.
- When the CLI requires `agent skills set`, the reconciler sends the union of current unmanaged assignments and desired managed assignments.
- Removing a previously managed skill assignment produces an explicit `DETACH_SKILL` plan action. Apply removes only that marked managed assignment and preserves unrelated skills.

Roster policy:

- Required managed agent roles and the unique human approver role are reconciled.
- Unmanaged extra roster members are reported but not removed in v1.
- An extra member that duplicates a managed role or creates multiple human approvers blocks planning.
- Non-conflicting extra members produce a warning and remain unchanged.

### `verify`

Checks every managed object against the desired hash and verifies:

- agent names, descriptions, instructions, runtime IDs, models, thinking levels, visibility and concurrency;
- workspace skill content and agent skill assignments;
- squad leader, instructions and roster roles;
- exactly one human approver;
- no missing managed role;
- object-level management markers and spec hashes match the desired state.

If a local deployment record is available, `verify` reports its source commit and plan digest. The record is useful audit evidence but is not required for another computer to verify the workspace.

### `drift`

Runs the same comparison as `plan` but never creates an apply plan. It reports UI changes, missing objects and unexpected changes to managed fields.

Git remains authoritative. To keep a legitimate UI hotfix, run `export --bootstrap`, review the generated candidate changes, and submit them through a Git PR.

### `install-skills`

Installs local skills from the checkout into provider discovery directories by link when supported and copy otherwise.

It records the install mode and source checkout path locally. It never writes a machine path into committed files.

### Destructive commands

No `prune`, `destroy` or automatic archive command is included in v1. Decommissioning will be designed separately after create/update behavior is proven.

## 11. Skill Distribution

There are two distribution targets.

### Local Codex/OpenCode

The repository is cloned or updated with Git:

```text
gh repo clone <owner>/multica-dev-workflow
git pull --ff-only
python scripts/workflow.py install-skills
```

Release-channel update rules:

- `stable`: run `git fetch --tags`, select the requested or latest reviewed SemVer tag, and checkout that exact tag.
- `main`: checkout `main` and use `git pull --ff-only`.
- Never move from one release to another and apply without showing the Git changelog/diff and generating a new Multica plan.

The preferred mode is a link from the provider discovery directory to the repository checkout, so `git pull` updates the Skill immediately for new Agent sessions. On Windows, use a directory junction when ordinary symlinks are unavailable. Copy mode is a fallback and must record that `install-skills` needs to run again after updates.

Use `~/.agents/skills` as the canonical shared target when supported. Create provider-specific links only when discovery verification shows they are required, avoiding duplicate copies of the same Skill.

Install targets may include `~/.agents/skills`, `$CODEX_HOME/skills` and `~/.config/opencode/skills` as applicable.

### Multica Workspace

The reconciler packages each skill from the exact checkout into a `.zip` and runs documented local archive import with overwrite behavior. Local archives are preferred over GitHub URL import because they work with private repositories and bind deployment to the reviewed commit.

GitHub Releases also publish the skill archives and a full workflow bundle for manual installation.

## 12. Versioning

- `schema_version`: manifest parser compatibility.
- `workflow.version`: SemVer for behavior and instruction changes.
- Git commit: exact source state.
- Git tag: stable release such as `v1.0.0`.
- Spec hash: normalized desired state of each managed object.
- Deployment record: workspace, profile, deployment profile, Git tag/commit, plan digest, applied actor and timestamp.

Recommended policy:

- patch: wording fixes with no workflow behavior change;
- minor: compatible role, instruction or validation additions;
- major: changed approval gates, issue hierarchy, branch strategy or destructive migration.

`main` contains reviewed changes. Rebuilds should default to a stable tag; following `main` requires an explicit `--channel main` choice.

## 13. GitHub Workflow

GitHub Actions validates but does not deploy.

Required checks:

- JSON Schema validation;
- all instruction and skill references resolve;
- logical keys and names are unique;
- squad leader and roster references exist;
- exactly one human approver contract exists;
- no UUIDs, absolute user paths or token-like values in portable files;
- generated instructions contain required Plan and final approval gates;
- reconciler unit tests on Windows, Linux and macOS;
- deterministic skill archive and manifest hash tests;
- Markdown link checks;
- secret scanning.
- release archives include SHA256 checksums and deterministic file ordering/timestamps.

The reconciler remains standard-library-only. CI may install development-only validators such as `check-jsonschema` or `ajv`; those tools are not runtime dependencies for rebuilding a squad.

PR review is required before merging to `main`. Tags and releases are created only from a green `main` commit.

## 14. Management Skill Contract

Create a second skill named `multica-workflow-manager`.

It instructs external Codex/OpenCode agents to:

1. locate or clone the workflow repository;
2. fetch tags and select the requested release channel;
3. run `doctor` and stop on ambiguity;
4. run `plan` and present the complete action summary;
5. wait for `APPROVE WORKFLOW PLAN <digest>`;
6. run `apply` using that exact plan;
7. run `verify` and report deployment record and residual drift;
8. never edit Multica objects ad hoc when the reconciler can manage them.

The skill itself remains small. The versioned repository and deterministic reconciler carry the actual workflow definition.

## 15. Migration of the Existing Squad

Migration is adoption, not immediate recreation.

1. Export the current T0 agents, squad, roster and instructions as a redacted snapshot.
2. Split common and role-specific instruction text into committed Markdown files.
3. Create the full manifest and quality deployment profile without UUIDs.
4. Generate a local runtime map for the chosen Codex and OpenCode runtimes.
5. Package the existing `multica-requirement-intake` skill into the repository.
6. Run `plan --adopt`; expected actions are adoption markers, skill import/assignment and any intentional normalization.
7. Review and approve the adoption plan.
8. Apply and verify until a second `plan` reports only `NO_CHANGE`.
9. Tag the resulting state as `v1.0.0`.
10. From that point onward, make workflow changes through Git PRs and re-apply reviewed plans.

## 16. Failure and Recovery

- CLI unavailable or unauthenticated: `doctor` fails without mutation.
- Multiple workspaces or runtimes: plan is blocked until an explicit selection exists.
- Same-name unmanaged object: blocked unless `--adopt` is supplied.
- Same-name unmarked workspace skill: blocked unless explicit skill adoption is supplied.
- Git or Multica state changes after review: apply aborts and requires re-plan.
- Partial create/update: journal completed actions, re-query current state, re-plan and continue idempotently.
- Skill import succeeds but agent assignment fails: retain the imported skill and complete assignment on the next plan.
- Previous workflow version is needed: checkout the previous tag, generate a new rollback plan and apply it as an ordinary reviewed update.
- Secret field cannot be reconstructed: preserve the existing field and report it as externally managed; never clear it silently.

## 17. Acceptance Scenarios

- A new computer can clone the repository, resolve its own Runtime IDs and rebuild the squad without editing committed files.
- The same workspace can be managed from two computers without copying machine-specific UUIDs into Git.
- Updating the same workspace from another computer preserves existing Runtime bindings unless rebinding was explicitly approved.
- `plan` followed by no external change can be applied; any intervening drift invalidates the plan.
- Applying the same desired version twice produces no mutations on the second run.
- A Codex-only or OpenCode-only deployment profile can rebuild all roles with provider-correct model names.
- An existing same-name unmanaged agent is never overwritten without explicit adoption.
- Updating role instructions in Git produces a precise field-level plan and a new spec hash.
- Pulling a new Git tag updates local skills and the Multica workspace skill from the same reviewed commit.
- Existing unrelated Agent Skill assignments are preserved during reconciliation.
- Removing a managed Agent Skill assignment removes only that managed assignment after an explicit reviewed plan.
- Unmanaged extra roster members are reported and never silently removed.
- No approver UUID is committed, and the rebuilt squad has exactly one roster member with role `人工审批人`.
- GitHub Actions can validate and package the repository without Multica credentials.

## 18. Recommended Implementation Decisions

- Repository: private `multica-dev-workflow`.
- Canonical format: JSON plus JSON Schema.
- Reconciler: Python standard library for v1.
- Deployment default: in-place create/update/adopt, never destructive.
- Skill workspace sync: local zip import from the reviewed checkout.
- Default release channel: stable Git tag.
- Default deployment profile: `quality`.
- Runtime binding: gitignored local JSON generated by `doctor`; existing bindings are preserved unless `--rebind-runtimes` is approved.
- Existing squad migration: adopt current objects, then require a no-change plan before tagging `v1.0.0`.

## 19. Approval Gate

No repository, GitHub release, management skill, reconciler or Multica mutation should be created before this Plan completes review and receives:

```text
APPROVE WORKFLOW PLAN v5
```

## 20. Review History

### Plan v1 Review

Result: `CHANGES_REQUESTED`.

Findings corrected in v2:

1. Removed `source_commit` from every generated instruction header to prevent unrelated Git commits from causing full-agent churn.
2. Added preserve-by-default Runtime semantics and explicit `--rebind-runtimes` to prevent two computers from rebinding the same squad back and forth.
3. Added managed Skill markers and an adoption gate before overwrite can affect an existing same-name workspace Skill.
4. Changed local Skill installation to link/junction-first so `git pull` updates the installed Skill without an extra copy step.

### Plan v2 Review

Result: `CHANGES_REQUESTED`.

Findings corrected in v3:

1. Removed global `workflow_version` from every managed object header so releasing one role change does not update unrelated agents.
2. Defined object spec hashes as portable values that exclude resolved Runtime UUIDs and other machine-local bindings.
3. Made local deployment records optional audit evidence rather than a prerequisite for verification from another computer.

### Plan v3 Review

Result: `CHANGES_REQUESTED`.

Findings corrected in v4:

1. Split release `workflow.version` from `protocol_revision`; only a protocol change intentionally updates every role through `common.md`.
2. Added non-destructive Agent Skill reconciliation so `agent skills set` preserves unrelated existing assignments.
3. Added non-destructive roster semantics: conflicting extras block, while non-conflicting unmanaged members are reported and preserved.
4. Added explicit Skill deployment targets so local-only management skills are not imported into the Multica workspace unnecessarily.

### Plan v4 Review

Result: `CHANGES_REQUESTED`.

Findings corrected in v5:

1. Added explicit stable-tag and `main` update semantics; a detached stable release is no longer described as updating through plain `git pull`.
2. Added reviewed `DETACH_SKILL` actions that remove only workflow-managed assignments while preserving unrelated skills.
3. Defined compatible versus pinned Runtime drift so cross-computer verification does not pretend that an uncommitted Runtime UUID is globally authoritative.
4. Added deterministic release archives and SHA256 checksums to the CI contract.
