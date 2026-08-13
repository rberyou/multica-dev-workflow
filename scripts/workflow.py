#!/usr/bin/env python3
"""CLI for planning and deploying the packaged Multica workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from workflow_lib import (
    MulticaCLI,
    WorkflowError,
    apply_plan,
    build_plan,
    discover_multica,
    default_local_skill_root,
    fetch_state,
    install_skills,
    load_deployment_record,
    load_runtime_map,
    mutation_actions,
    plan_has_blockers,
    redact,
    repo_root,
    resolve_profile,
    resolve_workspace,
    run_process,
    save_plan,
    source_identity,
    summarize_actions,
    utc_now,
    validate_local_skill_root_access,
    validate_repository,
    write_json,
)


def add_context_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--multica-bin")
    parser.add_argument("--profile")
    parser.add_argument("--workspace")
    parser.add_argument("--deployment-profile", default="quality")
    parser.add_argument("--runtime-map")


def context(args: argparse.Namespace, root: Path) -> tuple[MulticaCLI, dict, dict, dict]:
    manifest, profile_doc = validate_repository(root, args.deployment_profile)
    source_identity(root)
    binary = discover_multica(args.multica_bin)
    profile = resolve_profile(binary, args.profile)
    cli = MulticaCLI(binary=binary, profile=profile)
    workspace = resolve_workspace(cli, args.workspace)
    return cli, workspace, manifest, profile_doc


def runtime_map_path(
    args: argparse.Namespace,
    workspace: dict,
    workflow_id: str,
    home: Path | None = None,
) -> Path:
    if args.runtime_map:
        return Path(args.runtime_map).expanduser().resolve()
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id or any(
        character in workflow_id for character in ("/", "\\")
    ):
        raise WorkflowError("workflow id is not safe for Runtime map selection")
    workspace_id = str(workspace.get("id") or "").strip()
    if not workspace_id:
        raise WorkflowError("resolved workspace has no id for Runtime map selection")
    if any(character in workspace_id for character in ("/", "\\")):
        raise WorkflowError("resolved workspace id is not safe for Runtime map selection")
    home_root = (home if home is not None else Path.home()).expanduser().resolve()
    return (
        home_root
        / ".multica"
        / "workflows"
        / workflow_id
        / "runtime-maps"
        / f"{workspace_id}.json"
    ).resolve()


def action_label(action: dict) -> str:
    kind = action.get("type", "UNKNOWN")
    key = action.get("key") or action.get("agent_key") or action.get("member_ref") or ""
    reason = action.get("reason")
    if action.get("scope") == "local_skill":
        current = action.get("current_type", "unknown")
        current_digest = action.get("current_digest") or "<none>"
        desired_digest = action.get("desired_digest") or "<none>"
        return (
            f"{kind:32} {action.get('skill')}: root={action.get('target_root')} "
            f"current={current} target_type={action.get('target_type', current)} "
            f"current_digest={current_digest} "
            f"desired_digest={desired_digest}"
            f"{f': {reason}' if reason else ''}"
        )
    return f"{kind:14} {key}{f': {reason}' if reason else ''}"


def print_plan(plan: dict, path: Path | None = None) -> None:
    print(json.dumps(summarize_actions(plan.get("actions", [])), ensure_ascii=False, indent=2))
    for action in plan.get("actions", []):
        if action.get("type") != "NO_CHANGE" or action.get("scope") == "local_skill":
            print(action_label(action))
    if path:
        print(f"Plan file: {path}")
    print(f"Plan digest: {plan['plan_digest']}")
    if plan.get("draft"):
        print("DRAFT: restore a reviewed immutable source and generate a new Plan before Apply")
    else:
        print(f"Approval: APPROVE WORKFLOW PLAN {plan['plan_digest'][:12]}")


def command_doctor(args: argparse.Namespace, root: Path) -> int:
    cli, workspace, manifest, profile_doc = context(args, root)
    selected_runtime_map_path = runtime_map_path(
        args, workspace, str(manifest["workflow"]["id"])
    )
    runtime_map = load_runtime_map(selected_runtime_map_path)
    local_skill_root = validate_local_skill_root_access(default_local_skill_root())
    required = [
        ["agent", "create", "--help"],
        ["agent", "update", "--help"],
        ["agent", "archive", "--help"],
        ["agent", "skills", "set", "--help"],
        ["squad", "create", "--help"],
        ["squad", "update", "--help"],
        ["squad", "member", "--help"],
        ["skill", "import", "--help"],
        ["skill", "delete", "--help"],
        ["project", "create", "--help"],
        ["project", "update", "--help"],
        ["autopilot", "list", "--help"],
        ["autopilot", "get", "--help"],
        ["autopilot", "delete", "--help"],
        ["runtime", "list", "--help"],
        ["user", "profile", "get", "--help"],
    ]
    for command in required:
        cli.text(command)
    state = fetch_state(cli)
    instruction_sizes = []
    for agent in manifest.get("agents", []):
        size = sum(
            (root / relative).stat().st_size
            for relative in agent.get("instruction_files", [])
        )
        instruction_sizes.append((agent["key"], size))
    squad_size = sum(
        (root / relative).stat().st_size
        for relative in manifest["squad"].get("instruction_files", [])
    )
    instruction_sizes.append(("squad", squad_size))
    if os.name == "nt":
        too_large = [(key, size) for key, size in instruction_sizes if size > 26000]
        if too_large:
            raise WorkflowError(
                f"instructions may exceed the Windows command-line limit: {too_large}"
            )
    providers: dict[str, int] = {}
    for runtime in state.get("runtimes", []):
        if runtime.get("status") == "online":
            provider = str(runtime.get("provider", "unknown"))
            providers[provider] = providers.get(provider, 0) + 1
    print(f"Source root: {root}")
    source, source_dirty = source_identity(root)
    print(f"Source: {source.get('type')} {source.get('id')}")
    if source.get("git_commit"):
        print(f"Source commit: {source.get('git_commit')}")
    if source.get("release_tag"):
        print(f"Release tag: {source.get('release_tag')}")
    print(f"Source dirty: {source_dirty}")
    print(f"Multica CLI: {cli.binary}")
    print(f"Profile: {cli.profile or '<default>'}")
    print(f"Workspace: {workspace.get('name')} ({workspace.get('id')})")
    print(
        f"Workflow: {manifest['workflow']['id']} "
        f"version={manifest['workflow']['version']} "
        f"protocol={manifest['workflow']['protocol_revision']}"
    )
    print(f"Deployment profile: {profile_doc.get('name')}")
    runtime_map_status = "present" if selected_runtime_map_path.is_file() else "missing"
    print(
        f"Runtime map: {selected_runtime_map_path} "
        f"({runtime_map_status}, {len(runtime_map.get('bindings') or {})} bindings)"
    )
    print(f"Local Skill root: {local_skill_root} (copy-only)")
    print(f"Online runtimes: {json.dumps(providers, ensure_ascii=False, sort_keys=True)}")
    print("Doctor: OK")
    return 0


def command_export(args: argparse.Namespace, root: Path) -> int:
    cli, workspace, _, _ = context(args, root)
    state = redact(fetch_state(cli))
    stamp = utc_now().replace(":", "").replace("-", "")
    slug = str(workspace.get("slug") or workspace.get("name") or workspace.get("id"))
    output = root / f"exports/{slug}/{stamp}/snapshot.json"
    write_json(
        output,
        {"exported_at": utc_now(), "profile": cli.profile, "workspace": workspace, "state": state},
    )
    print(output)
    return 0


def build_from_args(
    args: argparse.Namespace, root: Path, write_archives: bool = True
) -> tuple[dict, MulticaCLI, dict]:
    cli, workspace, manifest, _ = context(args, root)
    plan = build_plan(
        root=root,
        cli=cli,
        workspace=workspace,
        deployment_profile=args.deployment_profile,
        runtime_map_path=runtime_map_path(
            args, workspace, str(manifest["workflow"]["id"])
        ),
        adopt=bool(getattr(args, "adopt", False)),
        rebind_runtimes=bool(getattr(args, "rebind_runtimes", False)),
        write_archives=write_archives,
    )
    return plan, cli, workspace


def command_plan(args: argparse.Namespace, root: Path) -> int:
    _, source_dirty = source_identity(root)
    if source_dirty and not args.allow_dirty:
        raise WorkflowError(
            "deployment source is dirty; commit/review changes or pass --allow-dirty for a draft Plan"
        )
    plan, _, _ = build_from_args(args, root)
    path = save_plan(root, plan)
    print_plan(plan, path)
    return 2 if plan_has_blockers(plan) else 0


def command_drift(args: argparse.Namespace, root: Path) -> int:
    plan, _, _ = build_from_args(args, root, write_archives=False)
    print_plan(plan)
    if plan_has_blockers(plan):
        return 2
    return 1 if mutation_actions(plan) else 0


def command_verify(args: argparse.Namespace, root: Path) -> int:
    plan, _, _ = build_from_args(args, root, write_archives=False)
    print_plan(plan)
    deployment_record = load_deployment_record(
        root, str((plan.get("workspace") or {}).get("id") or "")
    )
    if deployment_record:
        print(
            "Deployment record: "
            + json.dumps(deployment_record, ensure_ascii=False, sort_keys=True)
        )
    if plan_has_blockers(plan):
        print("Verify: BLOCKED")
        return 2
    remaining = mutation_actions(plan)
    if remaining:
        print(f"Verify: DRIFT ({len(remaining)} mutation actions remain)")
        return 1
    print("Verify: OK")
    return 0


def command_apply(args: argparse.Namespace, root: Path) -> int:
    plan_path = Path(args.plan).expanduser().resolve()
    plan_doc = json.loads(plan_path.read_text(encoding="utf-8"))
    if (
        os.environ.get("MULTICA_AGENT_ID") or os.environ.get("MULTICA_TASK_ID")
    ) and not args.allow_agent_identity:
        raise WorkflowError(
            "refusing to Apply from a daemon-managed agent identity; "
            "pass --allow-agent-identity after explicit review"
        )
    binary = discover_multica(args.multica_bin)
    profile = resolve_profile(
        binary, args.profile if args.profile is not None else plan_doc.get("profile")
    )
    cli = MulticaCLI(binary=binary, profile=profile)
    resolve_workspace(cli, str(plan_doc.get("workspace", {}).get("id")))
    record = apply_plan(root, cli, plan_path, args.approve)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    verify_args = argparse.Namespace(
        multica_bin=binary,
        profile=profile,
        workspace=cli.workspace_id,
        deployment_profile=plan_doc["deployment_profile"],
        runtime_map=plan_doc["runtime_map_path"],
        adopt=False,
        rebind_runtimes=False,
    )
    verify_plan, _, _ = build_from_args(verify_args, root, write_archives=False)
    if plan_has_blockers(verify_plan) or mutation_actions(verify_plan):
        print_plan(verify_plan)
        raise WorkflowError("Apply finished but verification still reports drift")
    print("Apply and verify: OK")
    return 0


def command_install_skills(args: argparse.Namespace, root: Path) -> int:
    source_identity(root)
    target = (
        Path(args.target).expanduser().resolve()
        if args.target
        else default_local_skill_root()
    )
    results = install_skills(root, target, args.replace_existing)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def command_incidents(args: argparse.Namespace, root: Path) -> int:
    cli, workspace, _, _ = context(args, root)
    command = [
        sys.executable,
        str(root / "skills/multica-workflow-incidents/scripts/incidents.py"),
        "--multica-bin",
        cli.binary,
        "--workspace",
        str(workspace["id"]),
    ]
    if cli.profile:
        command.extend(["--profile", cli.profile])
    if args.command == "bind-workflow-issue":
        command.extend(
            [
                "bind-workflow-issue",
                "--issue",
                args.issue,
                "--object-type",
                args.object_type,
                "--created-by-role",
                args.created_by_role,
            ]
        )
        if args.root_requirement_id:
            command.extend(["--root-requirement-id", args.root_requirement_id])
    elif args.command == "report-incident":
        command.extend(
            [
                "report",
                "--source-issue",
                args.source_issue,
                "--rule-id",
                args.rule_id,
                "--severity",
                args.severity,
                "--summary",
                args.summary,
                "--expected",
                args.expected,
                "--actual",
                args.actual,
            ]
        )
        for key, value in [
            ("--evidence", args.evidence),
            ("--entity", args.entity),
            ("--dedupe-key", args.dedupe_key),
            ("--reporter-agent-id", args.reporter_agent_id),
            ("--reporter-role", args.reporter_role),
        ]:
            if value:
                command.extend([key, value])
        if args.block_source:
            command.append("--block-source")
    elif args.command == "create-incident-fix-requirement":
        command.extend(
            [
                "create-fix-requirement",
                "--incident",
                args.incident,
                "--project",
                args.project,
            ]
        )
        if args.assignee_id:
            command.extend(["--assignee-id", args.assignee_id])
    elif args.command == "link-incident-fix":
        command.extend(
            [
                "link-fix",
                "--incident",
                args.incident,
                "--requirement",
                args.requirement,
            ]
        )
    else:
        command.extend(
            [
                "close",
                "--incident",
                args.incident,
                "--result",
                args.result,
                "--evidence",
                args.evidence,
            ]
        )
        if args.source_commit:
            command.extend(["--source-commit", args.source_commit])
        if args.deployment_plan_digest:
            command.extend(["--deployment-plan-digest", args.deployment_plan_digest])
        for key, value in [
            ("--fix-reference-type", args.fix_reference_type),
            ("--fix-reference", args.fix_reference),
            (
                "--deployment-verification-reference-type",
                args.deployment_verification_reference_type,
            ),
            (
                "--deployment-verification-reference",
                args.deployment_verification_reference,
            ),
        ]:
            if value:
                command.extend([key, value])
    result = run_process(command, cwd=root, check=False)
    if result.stdout:
        sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode


def parser() -> argparse.ArgumentParser:
    root_parser = argparse.ArgumentParser(description=__doc__)
    subparsers = root_parser.add_subparsers(dest="command", required=True)

    doctor = subparsers.add_parser("doctor")
    add_context_args(doctor)
    doctor.set_defaults(func=command_doctor)

    export = subparsers.add_parser("export")
    add_context_args(export)
    export.set_defaults(func=command_export)

    for name, function in [
        ("plan", command_plan),
        ("drift", command_drift),
        ("verify", command_verify),
    ]:
        command = subparsers.add_parser(name)
        add_context_args(command)
        command.add_argument("--adopt", action="store_true")
        command.add_argument("--rebind-runtimes", action="store_true")
        if name == "plan":
            command.add_argument("--allow-dirty", action="store_true")
        command.set_defaults(func=function)

    apply = subparsers.add_parser("apply")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--approve", required=True)
    apply.add_argument("--multica-bin")
    apply.add_argument("--profile")
    apply.add_argument("--allow-agent-identity", action="store_true")
    apply.set_defaults(func=command_apply)

    install = subparsers.add_parser("install-skills")
    install.add_argument("--target")
    install.add_argument("--replace-existing", action="store_true")
    install.set_defaults(func=command_install_skills)

    bind = subparsers.add_parser("bind-workflow-issue")
    add_context_args(bind)
    bind.add_argument("--issue", required=True)
    bind.add_argument("--object-type", required=True)
    bind.add_argument("--root-requirement-id")
    bind.add_argument("--created-by-role", required=True)
    bind.set_defaults(func=command_incidents)

    report = subparsers.add_parser("report-incident")
    add_context_args(report)
    report.add_argument("--source-issue", required=True)
    report.add_argument("--rule-id", default="WF-SELF-REPORT-001")
    report.add_argument("--severity", choices=["low", "medium", "high", "urgent"], default="medium")
    report.add_argument("--summary", required=True)
    report.add_argument("--expected", required=True)
    report.add_argument("--actual", required=True)
    report.add_argument("--evidence")
    report.add_argument("--entity")
    report.add_argument("--dedupe-key")
    report.add_argument("--reporter-agent-id")
    report.add_argument("--reporter-role")
    report.add_argument("--block-source", action="store_true")
    report.set_defaults(func=command_incidents)

    create_fix = subparsers.add_parser("create-incident-fix-requirement")
    add_context_args(create_fix)
    create_fix.add_argument("--incident", required=True)
    create_fix.add_argument("--project", required=True)
    create_fix.add_argument("--assignee-id")
    create_fix.set_defaults(func=command_incidents)

    link = subparsers.add_parser("link-incident-fix")
    add_context_args(link)
    link.add_argument("--incident", required=True)
    link.add_argument("--requirement", required=True)
    link.set_defaults(func=command_incidents)

    close = subparsers.add_parser("close-incident")
    add_context_args(close)
    close.add_argument("--incident", required=True)
    close.add_argument("--result", choices=["passed", "failed"], required=True)
    close.add_argument("--evidence", required=True)
    close.add_argument("--source-commit")
    close.add_argument("--deployment-plan-digest")
    close.add_argument("--fix-reference-type")
    close.add_argument("--fix-reference")
    close.add_argument("--deployment-verification-reference-type")
    close.add_argument("--deployment-verification-reference")
    close.set_defaults(func=command_incidents)
    return root_parser


def main() -> int:
    args = parser().parse_args()
    try:
        root = repo_root(Path(__file__).resolve().parent.parent)
        return int(args.func(args, root))
    except WorkflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
