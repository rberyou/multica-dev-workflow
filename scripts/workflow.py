#!/usr/bin/env python3
"""CLI for planning and reconciling the Git-managed Multica workflow."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import uuid

from workflow_lib import (
    MulticaCLI,
    WorkflowError,
    apply_plan,
    build_plan,
    discover_multica,
    fetch_state,
    git_dirty,
    git_head,
    install_skills,
    match_managed,
    mutation_actions,
    plan_has_blockers,
    redact,
    repo_root,
    run_process,
    resolve_profile,
    resolve_workspace,
    save_plan,
    summarize_actions,
    utc_now,
    validate_repository,
    write_json,
)


def add_context_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--multica-bin")
    parser.add_argument("--profile")
    parser.add_argument("--workspace")
    parser.add_argument("--deployment-profile", default="quality")
    parser.add_argument("--runtime-map")


def context(args: argparse.Namespace, root: Path) -> tuple[MulticaCLI, dict]:
    binary = discover_multica(args.multica_bin)
    profile = resolve_profile(binary, args.profile)
    cli = MulticaCLI(binary=binary, profile=profile)
    workspace = resolve_workspace(cli, args.workspace)
    validate_repository(root, args.deployment_profile)
    return cli, workspace


def runtime_map_path(args: argparse.Namespace, root: Path) -> Path:
    if args.runtime_map:
        return Path(args.runtime_map).expanduser().resolve()
    return (root / ".multica/runtime-map.local.json").resolve()


def action_label(action: dict) -> str:
    kind = action.get("type", "UNKNOWN")
    key = action.get("key") or action.get("agent_key") or action.get("member_ref") or ""
    reason = action.get("reason")
    suffix = f": {reason}" if reason else ""
    return f"{kind:14} {key}{suffix}"


def write_console_safe(value: str, stream) -> None:
    encoding = getattr(stream, "encoding", None) or "utf-8"
    rendered = value.encode(encoding, errors="backslashreplace").decode(encoding)
    stream.write(rendered)


def print_plan(plan: dict, path: Path | None = None) -> None:
    print(json.dumps(summarize_actions(plan.get("actions", [])), ensure_ascii=False, indent=2))
    for action in plan.get("actions", []):
        if action.get("type") != "NO_CHANGE":
            print(action_label(action))
    if path:
        print(f"Plan file: {path}")
    print(f"Plan digest: {plan['plan_digest']}")
    if plan.get("draft"):
        print("DRAFT: this plan cannot be applied; commit the reviewed files and generate a new plan")
    else:
        print(f"Approval: APPROVE WORKFLOW PLAN {plan['plan_digest'][:12]}")


def command_doctor(args: argparse.Namespace, root: Path) -> int:
    manifest, profile_doc = validate_repository(root, args.deployment_profile)
    cli, workspace = context(args, root)
    required = [
        ["agent", "create", "--help"],
        ["agent", "update", "--help"],
        ["agent", "skills", "set", "--help"],
        ["squad", "create", "--help"],
        ["squad", "update", "--help"],
        ["squad", "member", "--help"],
        ["skill", "import", "--help"],
        ["project", "create", "--help"],
        ["project", "update", "--help"],
        ["autopilot", "create", "--help"],
        ["autopilot", "update", "--help"],
        ["autopilot", "trigger-add", "--help"],
        ["autopilot", "trigger-update", "--help"],
        ["runtime", "list", "--help"],
        ["user", "profile", "get", "--help"],
    ]
    for command in required:
        cli.text(command)
    state = fetch_state(cli)
    instruction_sizes = []
    for agent in manifest.get("agents", []):
        size = sum((root / relative).stat().st_size for relative in agent.get("instruction_files", []))
        instruction_sizes.append((agent["key"], size))
    squad_size = sum((root / relative).stat().st_size for relative in manifest["squad"].get("instruction_files", []))
    instruction_sizes.append(("squad", squad_size))
    if os.name == "nt":
        too_large = [(key, size) for key, size in instruction_sizes if size > 26000]
        if too_large:
            raise WorkflowError(f"instructions may exceed the Windows command-line limit: {too_large}")
    providers: dict[str, int] = {}
    for runtime in state.get("runtimes", []):
        if runtime.get("status") == "online":
            provider = str(runtime.get("provider", "unknown"))
            providers[provider] = providers.get(provider, 0) + 1
    print(f"Repository: {root}")
    print(f"Git HEAD: {git_head(root)}")
    print(f"Git dirty: {git_dirty(root)}")
    print(f"Multica CLI: {cli.binary}")
    print(f"Profile: {cli.profile or '<default>'}")
    print(f"Workspace: {workspace.get('name')} ({workspace.get('id')})")
    print(f"Workflow: {manifest['workflow']['version']} protocol={manifest['workflow']['protocol_revision']}")
    print(f"Deployment profile: {profile_doc.get('name')}")
    print(f"Online runtimes: {json.dumps(providers, ensure_ascii=False, sort_keys=True)}")
    print(f"Authenticated user: {state.get('user', {}).get('name')} ({state.get('user', {}).get('id')})")
    if os.environ.get("MULTICA_AGENT_ID") or os.environ.get("MULTICA_TASK_ID"):
        print("WARNING: running inside a daemon-managed agent identity; apply requires --allow-agent-identity")
    print("Doctor: OK")
    return 0


def command_export(args: argparse.Namespace, root: Path) -> int:
    cli, workspace = context(args, root)
    state = redact(fetch_state(cli))
    stamp = utc_now().replace(":", "").replace("-", "")
    slug = str(workspace.get("slug") or workspace.get("name") or workspace.get("id"))
    output = root / f"exports/{slug}/{stamp}/snapshot.json"
    write_json(
        output,
        {
            "exported_at": utc_now(),
            "profile": cli.profile,
            "workspace": workspace,
            "state": state,
        },
    )
    print(output)
    return 0


def command_secure_bindings(args: argparse.Namespace, root: Path) -> int:
    cli, workspace = context(args, root)
    manifest, _ = validate_repository(root, args.deployment_profile)
    state = fetch_state(cli)
    workflow_id = str(manifest["workflow"]["id"])
    desired_agents = {item["key"]: item for item in manifest.get("agents") or []}
    bindings = {}
    runtime_ids = set()
    for agent_key, security_profile in (manifest.get("secure_runtime", {}).get("agents") or {}).items():
        desired = desired_agents[agent_key]
        current, marked, errors = match_managed(
            state.get("agents", []),
            workflow_id,
            f"agent.{agent_key}",
            desired["name"],
            "instructions",
            desired.get("previous_names") or [],
        )
        if errors or not current or not marked:
            raise WorkflowError(
                f"secure Agent {agent_key} is missing or not managed: {errors or ['not found']}"
            )
        bindings[str(current["id"])] = security_profile
        runtime_id = str(current.get("runtime_id") or "")
        if not runtime_id:
            raise WorkflowError(f"secure Agent {agent_key} has no Runtime binding")
        runtime_ids.add(runtime_id)
    bootstrap_runtime_id = str(getattr(args, "bootstrap_runtime_id", None) or "")
    secure_phase = str((manifest.get("secure_runtime") or {}).get("phase") or "")
    if secure_phase != "enforced" and not bootstrap_runtime_id:
        raise WorkflowError(
            "secure runtime is only planned; pass --bootstrap-runtime-id during Phase 3 bootstrap"
        )
    if bootstrap_runtime_id:
        try:
            parsed_runtime_id = uuid.UUID(bootstrap_runtime_id)
        except ValueError as exc:
            raise WorkflowError("--bootstrap-runtime-id must be a Runtime UUID") from exc
        if parsed_runtime_id.int == 0:
            raise WorkflowError("--bootstrap-runtime-id cannot be the zero UUID")
        selected_runtime_id = str(parsed_runtime_id)
    else:
        if len(runtime_ids) != 1:
            raise WorkflowError(
                f"secure Agents must share exactly one dedicated Runtime; found {sorted(runtime_ids)}"
            )
        selected_runtime_id = next(iter(runtime_ids))
    output = Path(args.output).expanduser().resolve() if args.output else (root / "agent-bindings.local.json")
    write_json(
        output,
        {
            "schema_version": 1,
            "workspace_id": workspace["id"],
            "workflow_id": workflow_id,
            "workflow_version": manifest["workflow"]["version"],
            "runtime_id": selected_runtime_id,
            "bootstrap": bool(bootstrap_runtime_id),
            "agents": dict(sorted(bindings.items())),
        },
    )
    print(output)
    return 0


def build_from_args(args: argparse.Namespace, root: Path, write_archives: bool = True) -> tuple[dict, MulticaCLI, dict]:
    cli, workspace = context(args, root)
    plan = build_plan(
        root=root,
        cli=cli,
        workspace=workspace,
        deployment_profile=args.deployment_profile,
        runtime_map_path=runtime_map_path(args, root),
        adopt=bool(getattr(args, "adopt", False)),
        rebind_runtimes=bool(getattr(args, "rebind_runtimes", False)),
        disable_operations=bool(getattr(args, "disable_operations", False)),
        allow_active_v3_degraded=bool(getattr(args, "allow_active_v3_degraded", False)),
        write_archives=write_archives,
    )
    return plan, cli, workspace


def command_plan(args: argparse.Namespace, root: Path) -> int:
    if git_dirty(root) and not args.allow_dirty:
        raise WorkflowError("working tree is dirty; commit/review changes or pass --allow-dirty for a draft plan")
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
    if (os.environ.get("MULTICA_AGENT_ID") or os.environ.get("MULTICA_TASK_ID")) and not args.allow_agent_identity:
        raise WorkflowError("refusing to apply from a daemon-managed agent identity; pass --allow-agent-identity after explicit review")
    binary = discover_multica(args.multica_bin)
    profile = resolve_profile(binary, args.profile if args.profile is not None else plan_doc.get("profile"))
    cli = MulticaCLI(binary=binary, profile=profile)
    resolve_workspace(cli, str(plan_doc.get("workspace", {}).get("id")))
    journal = apply_plan(root, cli, plan_path, args.approve)
    print(json.dumps(journal, ensure_ascii=False, indent=2))

    verify_args = argparse.Namespace(
        multica_bin=binary,
        profile=profile,
        workspace=cli.workspace_id,
        deployment_profile=plan_doc["deployment_profile"],
        runtime_map=plan_doc["runtime_map_path"],
        adopt=False,
        rebind_runtimes=False,
        disable_operations=bool(plan_doc.get("disable_operations", False)),
        allow_active_v3_degraded=bool(plan_doc.get("allow_active_v3_degraded", False)),
    )
    verify_plan, _, _ = build_from_args(verify_args, root, write_archives=False)
    if plan_has_blockers(verify_plan) or mutation_actions(verify_plan):
        print_plan(verify_plan)
        raise WorkflowError("apply finished but verification still reports drift")
    print("Apply and verify: OK")
    return 0


def command_install_skills(args: argparse.Namespace, root: Path) -> int:
    target = Path(args.target).expanduser().resolve() if args.target else (Path.home() / ".agents/skills")
    results = install_skills(root, target, args.copy, args.replace_existing)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def command_observer(args: argparse.Namespace, root: Path) -> int:
    cli, workspace = context(args, root)
    script = root / "skills/multica-workflow-observer/scripts/observer.py"
    command = [
        sys.executable,
        str(script),
        "--multica-bin",
        cli.binary,
        "--workspace",
        str(workspace["id"]),
    ]
    if cli.profile:
        command.extend(["--profile", cli.profile])
    command.append(args.command)
    if args.command == "audit":
        command.extend(
            [
                "--scope",
                args.scope,
                "--max-issues",
                str(args.max_issues),
                "--backlog-hours",
                str(args.backlog_hours),
                "--health-max-age-minutes",
                str(args.health_max_age_minutes),
            ]
        )
        if args.report:
            command.append("--report")
        if args.coverage_issue:
            command.extend(["--coverage-issue", args.coverage_issue])
    elif args.command == "health":
        command.extend(["--max-age-minutes", str(args.max_age_minutes)])
        command.extend(
            ["--full-max-age-minutes", str(args.full_max_age_minutes)]
        )
    elif args.command == "scan":
        command.extend(
            [
                "--mode",
                args.mode,
                "--max-issues",
                str(args.max_issues),
                "--backlog-hours",
                str(args.backlog_hours),
                "--lease-minutes",
                str(args.lease_minutes),
            ]
        )
        if args.workflow_instance_id:
            command.extend(["--workflow-instance-id", args.workflow_instance_id])
    elif args.command == "register-project":
        command.extend(
            [
                "--project-id",
                args.project_id,
                "--workflow-instance-id",
                args.workflow_instance_id,
                "--protocol-revision",
                args.protocol_revision,
            ]
        )
        if args.development_squad_id:
            command.extend(["--development-squad-id", args.development_squad_id])
        if args.managed_agent_ids:
            command.extend(["--managed-agent-ids", args.managed_agent_ids])
        if args.disabled:
            command.append("--disabled")
    elif args.command == "bind-workflow-issue":
        command.extend(
            [
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
    elif args.command in {"report-anomaly", "report-incident"}:
        command.extend(
            [
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
        for option, value in [
            ("--source-requirement", args.source_requirement),
            ("--evidence", args.evidence),
            ("--entity", args.entity),
            ("--protocol-revision", args.protocol_revision),
            ("--reporter-agent-id", args.reporter_agent_id),
            ("--reporter-role", args.reporter_role),
        ]:
            if value:
                command.extend([option, value])
        if args.block_source:
            command.append("--block-source")
        if args.command == "report-anomaly" and args.no_wake:
            command.append("--no-wake")
    elif args.command == "triage":
        command.extend(["--incident", args.incident, "--verdict", args.verdict])
        if args.reason:
            command.extend(["--reason", args.reason])
    elif args.command == "prepare-maintenance-decision":
        command.extend(["--incident", args.incident])
    elif args.command == "record-maintenance-decision":
        command.extend(["--incident", args.incident])
        if args.comment_id:
            command.extend(["--comment-id", args.comment_id])
        if args.executor:
            command.extend(["--executor", args.executor])
    elif args.command == "verify-fix":
        command.extend(
            [
                "--incident",
                args.incident,
                "--result",
                args.result,
                "--evidence",
                args.evidence,
            ]
        )
        if args.deployed_version:
            command.extend(["--deployed-version", args.deployed_version])
        if args.deployment_target:
            command.extend(["--deployment-target", args.deployment_target])
    command.extend(["--output", args.output])
    result = run_process(command, cwd=root, check=False)
    if result.stdout:
        write_console_safe(result.stdout, sys.stdout)
    if result.stderr:
        write_console_safe(result.stderr, sys.stderr)
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

    secure_bindings = subparsers.add_parser("secure-bindings")
    add_context_args(secure_bindings)
    secure_bindings.add_argument("--output")
    secure_bindings.add_argument("--bootstrap-runtime-id")
    secure_bindings.set_defaults(func=command_secure_bindings)

    plan = subparsers.add_parser("plan")
    add_context_args(plan)
    plan.add_argument("--adopt", action="store_true")
    plan.add_argument("--rebind-runtimes", action="store_true")
    plan.add_argument("--disable-operations", action="store_true")
    plan.add_argument("--allow-active-v3-degraded", action="store_true")
    plan.add_argument("--allow-dirty", action="store_true")
    plan.set_defaults(func=command_plan)

    drift = subparsers.add_parser("drift")
    add_context_args(drift)
    drift.add_argument("--adopt", action="store_true")
    drift.add_argument("--rebind-runtimes", action="store_true")
    drift.add_argument("--disable-operations", action="store_true")
    drift.add_argument("--allow-active-v3-degraded", action="store_true")
    drift.set_defaults(func=command_drift)

    verify = subparsers.add_parser("verify")
    add_context_args(verify)
    verify.add_argument("--adopt", action="store_true")
    verify.add_argument("--rebind-runtimes", action="store_true")
    verify.add_argument("--disable-operations", action="store_true")
    verify.add_argument("--allow-active-v3-degraded", action="store_true")
    verify.set_defaults(func=command_verify)

    apply = subparsers.add_parser("apply")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--approve", required=True)
    apply.add_argument("--multica-bin")
    apply.add_argument("--profile")
    apply.add_argument("--allow-agent-identity", action="store_true")
    apply.set_defaults(func=command_apply)

    install = subparsers.add_parser("install-skills")
    install.add_argument("--target")
    install.add_argument("--copy", action="store_true")
    install.add_argument("--replace-existing", action="store_true")
    install.set_defaults(func=command_install_skills)

    audit = subparsers.add_parser("audit")
    add_context_args(audit)
    audit.add_argument("--scope", choices=["issues", "health", "all"], default="all")
    audit.add_argument("--report", action="store_true")
    audit.add_argument("--max-issues", type=int, default=5000)
    audit.add_argument("--backlog-hours", type=int, default=24)
    audit.add_argument("--health-max-age-minutes", type=int, default=135)
    audit.add_argument("--coverage-issue")
    audit.add_argument("--output", choices=["json"], default="json")
    audit.set_defaults(func=command_observer)

    register = subparsers.add_parser("register-project")
    add_context_args(register)
    register.add_argument("--project-id", required=True)
    register.add_argument("--workflow-instance-id", required=True)
    register.add_argument("--development-squad-id")
    register.add_argument("--managed-agent-ids")
    register.add_argument("--protocol-revision", default="v3")
    register.add_argument("--disabled", action="store_true")
    register.add_argument("--output", choices=["json"], default="json")
    register.set_defaults(func=command_observer)

    bind = subparsers.add_parser("bind-workflow-issue")
    add_context_args(bind)
    bind.add_argument("--issue", required=True)
    bind.add_argument("--object-type", required=True)
    bind.add_argument("--root-requirement-id")
    bind.add_argument("--created-by-role", required=True)
    bind.add_argument("--output", choices=["json"], default="json")
    bind.set_defaults(func=command_observer)

    def add_report_arguments(command: argparse.ArgumentParser) -> None:
        add_context_args(command)
        command.add_argument("--source-issue", required=True)
        command.add_argument("--source-requirement")
        command.add_argument("--rule-id", default="WF-SELF-REPORT-001")
        command.add_argument(
            "--severity",
            choices=["low", "medium", "high", "urgent"],
            default="medium",
        )
        command.add_argument("--summary", required=True)
        command.add_argument("--expected", required=True)
        command.add_argument("--actual", required=True)
        command.add_argument("--evidence")
        command.add_argument("--entity")
        command.add_argument("--protocol-revision")
        command.add_argument("--reporter-agent-id")
        command.add_argument("--reporter-role")
        command.add_argument("--block-source", action="store_true")
        command.add_argument("--output", choices=["json"], default="json")
        command.set_defaults(func=command_observer)

    report_anomaly = subparsers.add_parser("report-anomaly")
    add_report_arguments(report_anomaly)
    report_anomaly.add_argument("--no-wake", action="store_true")

    report_incident = subparsers.add_parser("report-incident")
    add_report_arguments(report_incident)

    scan = subparsers.add_parser("scan")
    add_context_args(scan)
    scan.add_argument("--mode", choices=["incremental", "full"], required=True)
    scan.add_argument("--workflow-instance-id")
    scan.add_argument("--max-issues", type=int, default=5000)
    scan.add_argument("--backlog-hours", type=int, default=24)
    scan.add_argument("--lease-minutes", type=int, default=30)
    scan.add_argument("--output", choices=["json"], default="json")
    scan.set_defaults(func=command_observer)

    triage = subparsers.add_parser("triage")
    add_context_args(triage)
    triage.add_argument("--incident", required=True)
    triage.add_argument(
        "--verdict",
        choices=[
            "CONFIRMED_WORKFLOW_BUG",
            "WORKFLOW_GAP",
            "USAGE_ERROR",
            "PROJECT_DEFECT",
            "RUNTIME_INCIDENT",
            "MULTICA_PRODUCT_DEFECT",
            "FALSE_POSITIVE",
            "DECISION_REQUIRED",
        ],
        required=True,
    )
    triage.add_argument("--reason")
    triage.add_argument("--output", choices=["json"], default="json")
    triage.set_defaults(func=command_observer)

    prepare_decision = subparsers.add_parser("prepare-maintenance-decision")
    add_context_args(prepare_decision)
    prepare_decision.add_argument("--incident", required=True)
    prepare_decision.add_argument("--output", choices=["json"], default="json")
    prepare_decision.set_defaults(func=command_observer)

    record_decision = subparsers.add_parser("record-maintenance-decision")
    add_context_args(record_decision)
    record_decision.add_argument("--incident", required=True)
    record_decision.add_argument("--comment-id")
    record_decision.add_argument("--executor")
    record_decision.add_argument("--output", choices=["json"], default="json")
    record_decision.set_defaults(func=command_observer)

    verify_fix_parser = subparsers.add_parser("verify-fix")
    add_context_args(verify_fix_parser)
    verify_fix_parser.add_argument("--incident", required=True)
    verify_fix_parser.add_argument(
        "--result", choices=["passed", "failed"], required=True
    )
    verify_fix_parser.add_argument("--evidence", required=True)
    verify_fix_parser.add_argument("--deployed-version")
    verify_fix_parser.add_argument("--deployment-target")
    verify_fix_parser.add_argument("--output", choices=["json"], default="json")
    verify_fix_parser.set_defaults(func=command_observer)

    health = subparsers.add_parser("health")
    add_context_args(health)
    health.add_argument("--max-age-minutes", type=int, default=135)
    health.add_argument("--full-max-age-minutes", type=int, default=1560)
    health.add_argument("--output", choices=["json"], default="json")
    health.set_defaults(func=command_observer)
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
