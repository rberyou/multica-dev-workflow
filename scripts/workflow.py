#!/usr/bin/env python3
"""CLI for planning and reconciling the Git-managed Multica workflow."""

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
    fetch_state,
    git_dirty,
    git_head,
    install_skills,
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
    else:
        command.extend(["--max-age-minutes", str(args.max_age_minutes)])
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

    health = subparsers.add_parser("health")
    add_context_args(health)
    health.add_argument("--max-age-minutes", type=int, default=135)
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
