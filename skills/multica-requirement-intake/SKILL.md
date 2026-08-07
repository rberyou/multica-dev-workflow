---
name: multica-requirement-intake
description: Clarify, confirm, create, submit, and continuously follow top-level development requirements in Multica for a configured development squad. Use when the user asks Codex or OpenCode to “创建需求”, “发布到 Multica”, “交给开发小队”, inspect or approve a Plan, answer a decision block, follow a T-* issue, or perform final requirement approval. Enforces confirmation before creation, portable runtime discovery, duplicate checks, project and squad resolution, continuous progress tracking, and human approval gates.
metadata:
  managed_by: multica-dev-workflow
  workflow_id: development-delivery
  version: 2.0.0-dev.5
---

# Multica Requirement Intake

Use this Skill as the portable entry point between the user and a Multica development squad.

## Core Contract

- Create only the top-level Requirement. Leave Plan, Implementation, design, split, and development-task Issues to the squad.
- Treat an initial request to create or submit as authorization for clarification and read-only discovery only. Never mutate from the initial description alone.
- Present a complete versioned `Requirement Draft v<N>` with target Workspace, Project, Squad, start mode, protocol binding, and duplicate disposition. Obtain explicit confirmation of that exact revision before creating, reusing-and-starting, or materially updating an Issue.
- Create safely in `backlog`, verify content and canonical identity, bind and verify protocol-v4 metadata through `multica-workflow-incidents`, then move to `todo` only when the confirmed mode requires starting.
- Assign the top-level Requirement to the resolved Squad, never directly to its leader or another Agent.
- Keep the Requirement solution-neutral. Preserve user-supplied constraints and hypotheses as Plan inputs, not approved design.
- Never set, cache, copy, guess, or request `human_approver_id`. The squad leader resolves the unique roster member whose role is `人工审批人`.
- After starting a Requirement, keep following its complete Issue chain until terminal state or the user explicitly stops tracking.
- Never infer approval. Validate the current gate, revision, independent review, authenticated human identity, and approver roster before posting an authorized approval or decision.
- Post final approval only on the top-level Requirement. Require root `in_review`, Plan and Implementation `done`, integration validation `done`, and an open revision/head/policy-bound final approval gate. A child or early approval is invalid and must not create approval metadata.
- Never claim creation, update, start, binding, decision, or approval without a fresh canonical service read proving the resulting state.

Portable defaults:

```text
squad_name: 开发交付小队
approver_role: 人工审批人
```

Allow an explicit squad-name override. `MULTICA_REQUIREMENT_SQUAD` may provide a machine-local default, but always resolve it uniquely in the selected Workspace.

## Select One Mode

- **Draft**: clarify and produce a versioned draft only. Do not mutate Multica.
- **Prepare to submit**: clarify, check duplicates, confirm the exact draft, create or reuse safely, bind protocol v4, move to `todo`, and follow continuously.
- **Prepare to queue**: perform the same confirmation and binding flow but leave a new Requirement in `backlog`. Never downgrade an already-active reused Issue.
- **Follow**: read the complete Issue chain, current gate, evidence, and required human action. Do not mutate unless requested.
- **Approve or decide**: act only on an explicit decision for the current revision after identity and gate validation, then resume following.

Silence, “看起来可以”, approval of another draft, or approval of a Plan is not Requirement-creation confirmation. Prefer `CONFIRM REQUIREMENT v<N>` for creation confirmation.

## Execution Order

1. Select the mode from the user's wording before loading detailed References.
2. For Draft mode, prepare the complete Requirement using testable acceptance criteria, scope, non-goals, constraints, risks, and unresolved Plan questions. Resolve Multica context only when the user asks to include or validate a target.
3. For submit or queue modes, resolve the CLI, profile, Workspace, protocol binder, Squad, roster, Project, repository context, and duplicates without guessing; then execute the confirmed transactional procedure and verify the final canonical state.
4. For Follow, approval, or decision modes, resolve the authenticated context and current Issue chain before reading or mutating the active gate.
5. For started or already-active Requirements, remain active through approvals, decisions, Review Loops, integration, and terminal reporting.
6. If the host cannot remain active or provide a wake-up mechanism, disclose the limitation and give the exact command needed to resume. Never claim tracking is active when it is not.

## Guardrails

- Inspect CLI `--help` before mutation; never invent commands, flags, profile names, Workspace IDs, Squad IDs, Project IDs, or undocumented HTTP calls.
- Do not install software, alter permanent `PATH`, switch the user's default Workspace, or write machine-specific values into the Skill unless explicitly requested.
- Use the browser only for read-only discovery and Follow fallback. Do not create or materially update a managed Requirement through browser-only mutation because deterministic protocol binding is mandatory.
- If the CLI or Incident binder is unavailable, allow Draft and read-only Follow modes only.
- Stop on ambiguous Workspace, Project, Squad, duplicate disposition, invalid approver roster, conflicting protocol metadata, stale confirmation, or an unverifiable post-mutation state.
- Never persist credentials, cookies, private keys, authorization headers, raw environment values, or unredacted private data.

## Reference Routing

- Read [requirement-template.md](references/requirement-template.md) whenever drafting or materially revising Requirement content.
- Read [portable-setup.md](references/portable-setup.md) when discovering the CLI/profile/Workspace, using a new computer, installing the companion Incident Skill, or diagnosing unavailable tooling.
- Read [submission-procedure.md](references/submission-procedure.md) only for duplicate resolution, Squad/Project resolution, confirmation, create/reuse/update, protocol binding, queue, or start operations.
- Read [follow-and-approvals.md](references/follow-and-approvals.md) only when following progress, answering a decision block, posting Plan/final approval, or reporting terminal delivery.
- Read [operating-manual.md](references/operating-manual.md) when explaining the lifecycle, status meanings, user responsibilities, failure recovery, or ready-to-use prompts.
- Read every reference named by the active mode; do not load unrelated references.
