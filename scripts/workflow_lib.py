#!/usr/bin/env python3
"""Core reconciliation logic for the Git-managed Multica workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
from typing import Any, Iterable

from jsonschema import Draft202012Validator

from package_skills import build_archive, package_hash


MANAGED_BY = "multica-dev-workflow"
MARKER_RE = re.compile(r"\A<!-- multica-workflow\r?\n(?P<body>.*?)\r?\n-->\r?\n?", re.DOTALL)
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
WORKFLOW_VERSION_LITERAL_RE = re.compile(
    r"\bworkflow_version=([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\b"
)


class WorkflowError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise WorkflowError(f"missing file: {path}") from exc
    except json.JSONDecodeError as exc:
        raise WorkflowError(f"invalid JSON in {path}: {exc}") from exc


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run_process(args: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise WorkflowError(f"command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def discover_multica(explicit: str | None = None) -> str:
    candidates: list[Path] = []
    for value in [explicit, os.environ.get("MULTICA_BIN"), shutil.which("multica"), shutil.which("multica.exe")]:
        if value:
            candidates.append(Path(value).expanduser())

    home = Path.home()
    system = platform.system().lower()
    if system == "windows":
        appdata = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        candidates.append(appdata / "Multica/bin/multica.exe")
        programs = local / "Programs"
        if programs.is_dir():
            candidates.extend(programs.glob("*/resources/app.asar.unpacked/resources/bin/multica.exe"))
    elif system == "darwin":
        candidates.extend(
            [
                home / "Library/Application Support/Multica/bin/multica",
                Path("/Applications/Multica.app/Contents/Resources/app.asar.unpacked/resources/bin/multica"),
                Path("/opt/homebrew/bin/multica"),
                Path("/usr/local/bin/multica"),
            ]
        )
    else:
        candidates.extend(
            [
                home / ".config/Multica/bin/multica",
                Path("/opt/Multica/resources/app.asar.unpacked/resources/bin/multica"),
                Path("/usr/lib/multica/resources/app.asar.unpacked/resources/bin/multica"),
            ]
        )

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen or not candidate.is_file():
            continue
        seen.add(key)
        result = run_process([key, "version", "--output", "json"], check=False)
        if result.returncode == 0:
            return key
    raise WorkflowError("Multica CLI not found; set MULTICA_BIN, add it to PATH, or install Multica Desktop")


def parse_config_show(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip().lower().replace(" ", "_")] = value.strip()
    return result


def resolve_profile(binary: str, explicit: str | None = None) -> str:
    requested = explicit or os.environ.get("MULTICA_REQUIREMENT_PROFILE")
    if requested:
        result = run_process([binary, "--profile", requested, "config", "show"])
        config = parse_config_show(result.stdout)
        if config.get("server_url") in {None, "", "(not set)"}:
            raise WorkflowError(f"Multica profile {requested!r} has no server_url")
        return requested

    default = parse_config_show(run_process([binary, "config", "show"]).stdout)
    if default.get("server_url") not in {None, "", "(not set)"}:
        return ""

    profile_root = Path.home() / ".multica/profiles"
    configured: list[str] = []
    if profile_root.is_dir():
        for directory in sorted(path for path in profile_root.iterdir() if path.is_dir()):
            result = run_process([binary, "--profile", directory.name, "config", "show"], check=False)
            if result.returncode != 0:
                continue
            config = parse_config_show(result.stdout)
            if config.get("server_url") not in {None, "", "(not set)"}:
                configured.append(directory.name)
    if len(configured) == 1:
        return configured[0]
    if not configured:
        raise WorkflowError("no configured Multica profile; run multica setup/login first")
    raise WorkflowError(f"multiple configured Multica profiles: {', '.join(configured)}; pass --profile")


@dataclass
class MulticaCLI:
    binary: str
    profile: str
    workspace_id: str = ""

    def command(self, args: Iterable[str], include_workspace: bool = True) -> list[str]:
        command = [self.binary]
        if self.profile:
            command.extend(["--profile", self.profile])
        if include_workspace and self.workspace_id:
            command.extend(["--workspace-id", self.workspace_id])
        command.extend(str(item) for item in args)
        return command

    def text(self, args: Iterable[str], include_workspace: bool = True, check: bool = True) -> str:
        return run_process(self.command(args, include_workspace), check=check).stdout

    def json(self, args: Iterable[str], include_workspace: bool = True) -> Any:
        output = self.text(args, include_workspace).strip()
        if not output:
            return None
        try:
            return json.loads(output)
        except json.JSONDecodeError as exc:
            raise WorkflowError(f"Multica command returned invalid JSON: {' '.join(self.command(args, include_workspace))}\n{output}") from exc


def resolve_workspace(cli: MulticaCLI, explicit: str | None = None) -> dict[str, Any]:
    workspaces = cli.json(["workspace", "list", "--output", "json"], include_workspace=False) or []
    requested = explicit or os.environ.get("MULTICA_WORKSPACE_ID")
    if not requested:
        config_args = [cli.binary]
        if cli.profile:
            config_args.extend(["--profile", cli.profile])
        config_args.extend(["config", "show"])
        config = parse_config_show(run_process(config_args).stdout)
        configured_id = config.get("workspace_id")
        if configured_id not in {None, "", "(not set)"}:
            requested = configured_id
    if requested:
        lowered = requested.lower()
        matches = [
            ws
            for ws in workspaces
            if lowered in {str(ws.get("id", "")).lower(), str(ws.get("slug", "")).lower(), str(ws.get("name", "")).lower()}
            or str(ws.get("id", "")).replace("-", "").lower().startswith(lowered.replace("-", ""))
        ]
        if len(matches) == 1:
            cli.workspace_id = str(matches[0]["id"])
            return matches[0]
        if not matches:
            raise WorkflowError(f"workspace {requested!r} not found")
        raise WorkflowError(f"workspace {requested!r} is ambiguous")
    if len(workspaces) == 1:
        cli.workspace_id = str(workspaces[0]["id"])
        return workspaces[0]
    if not workspaces:
        raise WorkflowError("authenticated account has no accessible Multica workspace")
    labels = [str(ws.get("slug") or ws.get("name") or ws.get("id")) for ws in workspaces]
    raise WorkflowError(f"multiple workspaces available: {', '.join(labels)}; pass --workspace")


def repo_root(start: Path | None = None) -> Path:
    current = (start or Path.cwd()).resolve()
    for candidate in [current, *current.parents]:
        if (candidate / "workflow.json").is_file() and (candidate / "scripts/workflow.py").is_file():
            return candidate
    raise WorkflowError("workflow repository not found; expected workflow.json and scripts/workflow.py")


def git_head(root: Path) -> str:
    result = run_process(["git", "rev-parse", "HEAD"], cwd=root, check=False)
    return result.stdout.strip() if result.returncode == 0 else "UNCOMMITTED"


def git_dirty(root: Path) -> bool:
    result = run_process(["git", "status", "--porcelain"], cwd=root)
    return bool(result.stdout.strip())


def runtime_instruction_versions(
    root: Path, manifest: dict[str, Any]
) -> dict[str, list[str]]:
    relative_paths = {
        str(relative)
        for agent in manifest.get("agents", [])
        for relative in agent.get("instruction_files", [])
    }
    relative_paths.update(
        str(relative)
        for relative in (manifest.get("squad") or {}).get("instruction_files", [])
    )
    result: dict[str, list[str]] = {}
    for relative in sorted(relative_paths):
        path = root / relative
        if path.is_file():
            result[relative] = WORKFLOW_VERSION_LITERAL_RE.findall(
                path.read_text(encoding="utf-8")
            )
    return result


def validate_repository(root: Path, deployment_profile: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_json(root / "workflow.json")
    schema = read_json(root / "workflow.schema.json")
    profile = read_json(root / f"deployment-profiles/{deployment_profile}.json")
    Draft202012Validator.check_schema(schema)
    schema_errors = sorted(
        Draft202012Validator(schema).iter_errors(manifest),
        key=lambda item: tuple(str(part) for part in item.absolute_path),
    )
    if schema_errors:
        rendered = []
        for item in schema_errors:
            location = ".".join(str(part) for part in item.absolute_path) or "<root>"
            rendered.append(f"workflow.json schema {location}: {item.message}")
        raise WorkflowError("repository validation failed:\n- " + "\n- ".join(rendered))
    errors: list[str] = []
    if manifest.get("schema_version") != 2:
        errors.append("schema_version must be 2")
    workflow = manifest.get("workflow") or {}
    for field in ["id", "name", "version", "protocol_revision", "approver_role"]:
        if not workflow.get(field):
            errors.append(f"workflow.{field} is required")
    version_file = (root / "VERSION").read_text(encoding="utf-8").strip() if (root / "VERSION").is_file() else ""
    if version_file != workflow.get("version"):
        errors.append("VERSION must match workflow.version")
    for relative, versions in runtime_instruction_versions(root, manifest).items():
        stale = sorted({value for value in versions if value != workflow.get("version")})
        if stale:
            errors.append(
                f"runtime instruction {relative} has stale workflow_version literals: {stale}"
            )
    agents = manifest.get("agents") or []
    agent_keys = [agent.get("key") for agent in agents]
    agent_names = [agent.get("name") for agent in agents]
    if len(set(agent_keys)) != len(agent_keys):
        errors.append("agent keys must be unique")
    if len(set(agent_names)) != len(agent_names):
        errors.append("agent names must be unique")
    bindings = profile.get("bindings") or {}
    for agent in agents:
        if agent.get("runtime_binding") not in bindings:
            errors.append(f"agent {agent.get('key')} references missing runtime binding {agent.get('runtime_binding')}")
        for relative in agent.get("instruction_files") or []:
            if not (root / relative).is_file():
                errors.append(f"agent {agent.get('key')} references missing instruction file {relative}")
    squad = manifest.get("squad") or {}
    if squad.get("leader") not in agent_keys:
        errors.append("squad leader must reference an agent key")
    for member in squad.get("agent_members") or []:
        if member.get("agent") not in agent_keys:
            errors.append(f"squad member references missing agent {member.get('agent')}")
    human_members = squad.get("human_members") or []
    if len(human_members) != 1 or human_members[0].get("role") != workflow.get("approver_role"):
        errors.append("squad must define exactly one human approver contract")
    for relative in squad.get("instruction_files") or []:
        if not (root / relative).is_file():
            errors.append(f"squad references missing instruction file {relative}")
    skill_keys = [skill.get("key") for skill in manifest.get("skills") or []]
    skill_names = [skill.get("name") for skill in manifest.get("skills") or []]
    if len(set(skill_keys)) != len(skill_keys) or len(set(skill_names)) != len(skill_names):
        errors.append("skill keys and names must be unique")
    for skill in manifest.get("skills") or []:
        directory = root / str(skill.get("path", ""))
        if not (directory / "SKILL.md").is_file():
            errors.append(f"skill {skill.get('key')} is missing SKILL.md")
        unknown = set(skill.get("attach_to") or []) - set(agent_keys)
        if unknown:
            errors.append(f"skill {skill.get('key')} attaches to unknown agents: {sorted(unknown)}")
    projects = manifest.get("projects") or []
    project_keys = [project.get("key") for project in projects]
    project_titles = [project.get("title") for project in projects]
    if len(set(project_keys)) != len(project_keys) or len(set(project_titles)) != len(project_titles):
        errors.append("project keys and titles must be unique")
    for project in projects:
        if project.get("lead") not in agent_keys:
            errors.append(f"project {project.get('key')} references missing lead agent {project.get('lead')}")
    autopilots = manifest.get("autopilots") or []
    autopilot_keys = [autopilot.get("key") for autopilot in autopilots]
    autopilot_titles = [autopilot.get("title") for autopilot in autopilots]
    if len(set(autopilot_keys)) != len(autopilot_keys) or len(set(autopilot_titles)) != len(autopilot_titles):
        errors.append("autopilot keys and titles must be unique")
    project_key_set = set(project_keys)
    for autopilot in autopilots:
        if autopilot.get("agent") not in agent_keys:
            errors.append(f"autopilot {autopilot.get('key')} references missing agent {autopilot.get('agent')}")
        if autopilot.get("project") not in project_key_set:
            errors.append(f"autopilot {autopilot.get('key')} references missing project {autopilot.get('project')}")
        trigger_keys = [trigger.get("key") for trigger in autopilot.get("triggers") or []]
        trigger_labels = [trigger.get("label") for trigger in autopilot.get("triggers") or []]
        if len(set(trigger_keys)) != len(trigger_keys) or len(set(trigger_labels)) != len(trigger_labels):
            errors.append(f"autopilot {autopilot.get('key')} trigger keys and labels must be unique")
    operations = manifest.get("operations")
    if not isinstance(operations, dict):
        errors.append("operations is required for schema_version 2")
    else:
        for field in ["project", "observer_agent", "observer_skill", "reporter_agents", "autopilot"]:
            if field not in operations:
                errors.append(f"operations.{field} is required")
        if operations.get("project") not in project_key_set:
            errors.append("operations.project must reference a managed project")
        if operations.get("observer_agent") not in agent_keys:
            errors.append("operations.observer_agent must reference a managed agent")
        if operations.get("observer_skill") not in skill_keys:
            errors.append("operations.observer_skill must reference a managed skill")
        unknown_reporters = set(operations.get("reporter_agents") or []) - set(agent_keys)
        if unknown_reporters:
            errors.append(f"operations.reporter_agents reference unknown agents: {sorted(unknown_reporters)}")
        if operations.get("autopilot") not in set(autopilot_keys):
            errors.append("operations.autopilot must reference a managed autopilot")
        if operations.get("maintenance_intake_mode") != "human_gated":
            errors.append("operations.maintenance_intake_mode must be human_gated")
        if operations.get("automatic_expansion") is not False:
            errors.append("operations.automatic_expansion must be false")
        reporter_agents = set(operations.get("reporter_agents") or [])
        squad_agents = {
            str(member.get("agent") or "")
            for member in squad.get("agent_members") or []
            if member.get("agent")
        }
        if reporter_agents != squad_agents:
            errors.append(
                "operations.reporter_agents must exactly match squad.agent_members agents"
            )
        observer_agent = str(operations.get("observer_agent") or "")
        observer_skill = next(
            (
                skill
                for skill in manifest.get("skills") or []
                if skill.get("key") == operations.get("observer_skill")
            ),
            None,
        )
        required_observer_attachments = reporter_agents | {observer_agent}
        if observer_skill:
            if "workspace" not in set(observer_skill.get("targets") or []):
                errors.append("operations.observer_skill must target workspace")
            if set(observer_skill.get("attach_to") or []) != required_observer_attachments:
                errors.append(
                    "operations.observer_skill attach_to must exactly match reporters plus observer_agent"
                )
        managed_autopilot = next(
            (
                item
                for item in autopilots
                if item.get("key") == operations.get("autopilot")
            ),
            None,
        )
        if managed_autopilot and (
            managed_autopilot.get("agent") != observer_agent
            or managed_autopilot.get("project") != operations.get("project")
        ):
            errors.append(
                "operations.autopilot must use operations.observer_agent and operations.project"
            )
    portable_files = [
        root / "workflow.json",
        *root.glob("deployment-profiles/*.json"),
        *root.glob("instructions/**/*.md"),
        *root.glob("skills/**/*.md"),
        *root.glob("skills/**/*.yaml"),
        *root.glob("skills/**/*.py"),
        *root.glob("docs/*.md"),
        *root.glob(".github/**/*.yml"),
        *root.glob(".github/**/*.md"),
    ]
    for path in portable_files:
        text = path.read_text(encoding="utf-8")
        if UUID_RE.search(text):
            errors.append(f"portable file contains a concrete UUID: {path.relative_to(root)}")
        if re.search(r"(?:[A-Za-z]:\\Users\\[^<\\]+|/Users/[^<\s/]+|/home/[^<\s/]+)", text):
            errors.append(f"portable file contains a user-specific path: {path.relative_to(root)}")
        if re.search(r"\b(?:mul|gho)_[A-Za-z0-9_-]{8,}\b", text):
            errors.append(f"portable file contains a token-like value: {path.relative_to(root)}")
    if errors:
        raise WorkflowError("repository validation failed:\n- " + "\n- ".join(errors))
    return manifest, profile


def desired_source_hash(root: Path, manifest: dict[str, Any], profile: dict[str, Any]) -> str:
    instructions: dict[str, str] = {}
    for agent in manifest.get("agents", []):
        for relative in agent.get("instruction_files", []):
            instructions[relative] = (root / relative).read_text(encoding="utf-8")
    for relative in manifest.get("squad", {}).get("instruction_files", []):
        instructions[relative] = (root / relative).read_text(encoding="utf-8")
    skills = {
        skill["key"]: package_hash(root / skill["path"])
        for skill in manifest.get("skills", [])
    }
    return sha256_value(
        {
            "manifest": manifest,
            "deployment_profile": profile,
            "instructions": instructions,
            "skills": skills,
            "version": (root / "VERSION").read_text(encoding="utf-8").strip(),
        }
    )


def instruction_body(root: Path, files: list[str]) -> str:
    parts = [root.joinpath(relative).read_text(encoding="utf-8").strip() for relative in files]
    return "\n\n".join(part for part in parts if part).strip() + "\n"


def parse_marker(instructions: str | None) -> dict[str, str] | None:
    if not instructions:
        return None
    match = MARKER_RE.match(instructions)
    if not match:
        return None
    values: dict[str, str] = {}
    for line in match.group("body").splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def strip_marker(instructions: str | None) -> str:
    if not instructions:
        return ""
    return MARKER_RE.sub("", instructions, count=1).strip() + "\n"


def render_marker(workflow_id: str, object_key: str, spec_hash: str, body: str) -> str:
    return (
        "<!-- multica-workflow\n"
        f"managed_by={MANAGED_BY}\n"
        f"workflow_id={workflow_id}\n"
        f"object_key={object_key}\n"
        f"spec_hash={spec_hash}\n"
        "-->\n"
        + body
    )


def normalize_permission(agent: dict[str, Any], workspace_id: str) -> str:
    mode = agent.get("permission_mode")
    if mode == "public_to":
        targets = agent.get("invocation_targets") or []
        if any(item.get("target_type") == "workspace" and item.get("target_id") == workspace_id for item in targets):
            return "public_to_workspace"
    return "private"


def agent_spec(
    key: str,
    name: str,
    description: str,
    body: str,
    provider: str,
    model: str,
    thinking: str,
    concurrency: int,
    permission: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "name": name,
        "description": description,
        "instructions": body,
        "runtime_provider": provider,
        "model": model,
        "thinking_level": thinking,
        "max_concurrent_tasks": concurrency,
        "permission_mode": permission,
    }


def project_spec(
    key: str,
    title: str,
    description: str,
    lead: str,
    status: str,
    icon: str,
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "description": description,
        "lead": lead,
        "status": status,
        "icon": icon,
    }


def autopilot_spec(
    key: str,
    title: str,
    description: str,
    agent: str,
    mode: str,
    project: str,
    status: str,
    issue_title_template: str,
    subscriber_ids: list[str],
) -> dict[str, Any]:
    return {
        "key": key,
        "title": title,
        "description": description,
        "agent": agent,
        "mode": mode,
        "project": project,
        "status": status,
        "issue_title_template": issue_title_template,
        "subscriber_ids": sorted(subscriber_ids),
    }


def normalized_subscriber_ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            subscriber_type = str(
                item.get("user_type")
                or item.get("member_type")
                or item.get("subscriber_type")
                or item.get("type")
                or ""
            ).lower()
            if subscriber_type and subscriber_type not in {"member", "user"}:
                continue
            identifier = item.get("id") or item.get("user_id") or item.get("member_id") or item.get("subscriber_id")
            if identifier:
                result.append(str(identifier))
    return sorted(set(result))


def normalized_triggers(value: Any) -> list[dict[str, Any]]:
    triggers = _as_list(value, "triggers")
    return [
        {
            "id": trigger.get("id"),
            "kind": trigger.get("kind") or trigger.get("type"),
            "label": trigger.get("label") or "",
            "enabled": bool(trigger.get("enabled", True)),
            "cron": trigger_cron(trigger),
            "timezone": trigger.get("timezone") or "UTC",
        }
        for trigger in triggers
    ]


def trigger_cron(trigger: dict[str, Any]) -> Any:
    return trigger.get("cron") or trigger.get("cron_expression") or trigger.get("schedule") or ""


def normalized_autopilot_detail(value: Any, fallback: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        return fallback
    nested = value.get("autopilot")
    if isinstance(nested, dict):
        detail = {**fallback, **nested}
        for field in ["triggers", "subscribers"]:
            if field in value:
                detail[field] = value[field]
    else:
        detail = {**fallback, **value}
    payload = nested if isinstance(nested, dict) else value
    if isinstance(payload, dict) and payload.get("assignee_id"):
        detail["agent_id"] = payload["assignee_id"]
    elif not detail.get("agent_id") and detail.get("assignee_id"):
        detail["agent_id"] = detail["assignee_id"]
    if isinstance(payload, dict) and payload.get("execution_mode"):
        detail["mode"] = payload["execution_mode"]
    elif not detail.get("mode") and detail.get("execution_mode"):
        detail["mode"] = detail["execution_mode"]
    return detail


def autopilot_state_items(state: dict[str, Any]) -> list[dict[str, Any]]:
    details = state.get("autopilot_details") or {}
    return [
        details.get(str(item.get("id") or ""), item)
        for item in state.get("autopilots", [])
    ]


def manifest_agent_name(manifest: dict[str, Any], agent_key: str) -> str:
    for agent in manifest.get("agents", []):
        if agent.get("key") == agent_key:
            return str(agent.get("name") or "")
    raise WorkflowError(f"manifest agent not found: {agent_key}")


def desired_skill_attachments(
    manifest: dict[str, Any], disable_operations: bool
) -> dict[str, set[str]]:
    desired: dict[str, set[str]] = {agent["key"]: set() for agent in manifest.get("agents", [])}
    for skill in manifest.get("skills", []):
        if "workspace" not in skill.get("targets", []):
            continue
        for agent_key in skill.get("attach_to", []):
            desired.setdefault(agent_key, set()).add(skill["key"])
    return desired


def deep_find(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value[key]
        for child in value.values():
            found = deep_find(child, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for child in value:
            found = deep_find(child, key)
            if found is not None:
                return found
    elif isinstance(value, str):
        match = re.search(rf"(?m)^\s*{re.escape(key)}:\s*[\"']?([^\s\"']+)", value)
        if match:
            return match.group(1)
    return None


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            lowered = key.lower()
            if any(term in lowered for term in ["token", "secret", "password", "cookie", "custom_env", "mcp_config"]):
                empty = child is None or child is False or child == 0 or child == ""
                result[key] = child if empty else "<redacted>"
            else:
                result[key] = redact(child)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _as_list(value: Any, possible_key: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        nested = value.get(possible_key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, dict)]
    return []


def list_active_v3_requirements(
    cli: MulticaCLI, workflow_id: str, max_issues: int = 5000
) -> list[dict[str, Any]]:
    result = []
    offset = 0
    page_size = 100
    while offset < max_issues:
        limit = min(page_size, max_issues - offset)
        page = _as_list(
            cli.json(
                [
                    "issue",
                    "list",
                    "--metadata",
                    f"workflow_id={workflow_id}",
                    "--metadata",
                    "protocol_revision=v3",
                    "--limit",
                    str(limit),
                    "--offset",
                    str(offset),
                    "--output",
                    "json",
                ]
            ),
            "issues",
        )
        result.extend(
            {
                "id": item.get("id"),
                "identifier": item.get("identifier"),
                "status": item.get("status"),
            }
            for item in page
            if str(item.get("status") or "") in {"backlog", "todo", "in_progress", "in_review", "blocked"}
            and not item.get("parent_issue_id")
        )
        if len(page) < limit:
            return result
        offset += len(page)
    raise WorkflowError(f"active v3 requirement scan exceeded max_issues={max_issues}")


def fetch_state(
    cli: MulticaCLI, include_active_v3: bool = False, workflow_id: str = ""
) -> dict[str, Any]:
    agents = _as_list(cli.json(["agent", "list", "--output", "json"]), "agents")
    squads = _as_list(cli.json(["squad", "list", "--output", "json"]), "squads")
    skills = _as_list(cli.json(["skill", "list", "--output", "json"]), "skills")
    projects = _as_list(cli.json(["project", "list", "--output", "json"]), "projects")
    autopilots = _as_list(cli.json(["autopilot", "list", "--output", "json"]), "autopilots")
    runtimes = _as_list(cli.json(["runtime", "list", "--output", "json"]), "runtimes")
    user = cli.json(["user", "profile", "get", "--output", "json"]) or {}
    squad_members: dict[str, list[dict[str, Any]]] = {}
    for squad in squads:
        squad_id = str(squad.get("id", ""))
        if squad_id:
            squad_members[squad_id] = _as_list(
                cli.json(["squad", "member", "list", squad_id, "--output", "json"]), "members"
            )
    skill_details: dict[str, dict[str, Any]] = {}
    for skill in skills:
        skill_id = str(skill.get("id", ""))
        if skill_id:
            detail = cli.json(["skill", "get", skill_id, "--output", "json"]) or skill
            if isinstance(detail, dict):
                skill_details[skill_id] = detail
    agent_skills: dict[str, list[dict[str, Any]]] = {}
    for agent in agents:
        agent_id = str(agent.get("id", ""))
        if agent_id:
            value = cli.json(["agent", "skills", "list", agent_id, "--output", "json"])
            agent_skills[agent_id] = _as_list(value, "skills")
    autopilot_details: dict[str, dict[str, Any]] = {}
    for autopilot in autopilots:
        autopilot_id = str(autopilot.get("id", ""))
        if autopilot_id:
            raw_detail = cli.json(["autopilot", "get", autopilot_id, "--output", "json"]) or autopilot
            detail = normalized_autopilot_detail(raw_detail, autopilot)
            if isinstance(detail, dict):
                autopilot_details[autopilot_id] = detail
    result = {
        "agents": agents,
        "squads": squads,
        "skills": skills,
        "projects": projects,
        "autopilots": autopilots,
        "autopilot_details": autopilot_details,
        "runtimes": runtimes,
        "user": user,
        "squad_members": squad_members,
        "skill_details": skill_details,
        "agent_skills": agent_skills,
    }
    if include_active_v3:
        result["active_v3_requirements"] = list_active_v3_requirements(cli, workflow_id)
    return result


def observed_hash(state: dict[str, Any]) -> str:
    agents = [
        {
            key: agent.get(key)
            for key in [
                "id",
                "name",
                "description",
                "instructions",
                "runtime_id",
                "model",
                "thinking_level",
                "max_concurrent_tasks",
                "permission_mode",
                "invocation_targets",
            ]
        }
        for agent in state.get("agents", [])
    ]
    squads = [
        {key: squad.get(key) for key in ["id", "name", "description", "instructions", "leader_id"]}
        for squad in state.get("squads", [])
    ]
    skills = [
        {
            "id": skill.get("id"),
            "name": skill.get("name"),
            "package_hash": deep_find(state.get("skill_details", {}).get(str(skill.get("id")), {}), "package_hash"),
            "managed_by": deep_find(state.get("skill_details", {}).get(str(skill.get("id")), {}), "managed_by"),
        }
        for skill in state.get("skills", [])
    ]
    projects = [
        {
            key: project.get(key)
            for key in ["id", "title", "description", "lead_id", "lead_type", "status", "icon"]
        }
        for project in state.get("projects", [])
    ]
    autopilots = []
    for autopilot in state.get("autopilots", []):
        detail = state.get("autopilot_details", {}).get(str(autopilot.get("id")), autopilot)
        detail = normalized_autopilot_detail(detail, autopilot)
        autopilots.append(
            {
                **{
                    key: detail.get(key)
                    for key in [
                        "id",
                        "title",
                        "description",
                        "agent_id",
                        "mode",
                        "project_id",
                        "status",
                        "issue_title_template",
                    ]
                },
                "subscriber_ids": normalized_subscriber_ids(
                    detail.get("subscribers") or detail.get("subscriber_ids")
                ),
                "triggers": sorted(
                    normalized_triggers(detail.get("triggers")),
                    key=lambda item: (str(item.get("label")), str(item.get("id"))),
                ),
            }
        )
    runtimes = [
        {key: runtime.get(key) for key in ["id", "provider", "status", "custom_name", "daemon_id"]}
        for runtime in state.get("runtimes", [])
    ]
    compact = {
        "agents": sorted(agents, key=lambda item: str(item.get("id"))),
        "squads": sorted(squads, key=lambda item: str(item.get("id"))),
        "skills": sorted(skills, key=lambda item: str(item.get("id"))),
        "projects": sorted(projects, key=lambda item: str(item.get("id"))),
        "autopilots": sorted(autopilots, key=lambda item: str(item.get("id"))),
        "runtimes": sorted(runtimes, key=lambda item: str(item.get("id"))),
        "user_id": state.get("user", {}).get("id"),
        "squad_members": {
            key: sorted(value, key=lambda item: (str(item.get("member_type")), str(item.get("member_id"))))
            for key, value in sorted(state.get("squad_members", {}).items())
        },
        "agent_skills": {
            key: sorted([str(item.get("id") or item.get("skill_id")) for item in value])
            for key, value in sorted(state.get("agent_skills", {}).items())
        },
        "active_v3_requirements": sorted(
            state.get("active_v3_requirements", []),
            key=lambda item: str(item.get("id") or item.get("identifier")),
        ),
    }
    return sha256_value(compact)


def load_runtime_map(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {"bindings": {}}
    value = read_json(path)
    if not isinstance(value, dict) or not isinstance(value.get("bindings", {}), dict):
        raise WorkflowError(f"invalid runtime map: {path}")
    return value


def runtime_index(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(item.get("id")): item for item in state.get("runtimes", []) if item.get("id")}


def match_managed(
    items: list[dict[str, Any]],
    workflow_id: str,
    object_key: str,
    name: str,
    instruction_field: str,
    previous_names: list[str] | None = None,
    name_field: str = "name",
) -> tuple[dict[str, Any] | None, bool, list[str]]:
    marked = []
    name_matches = []
    accepted_names = {name, *(previous_names or [])}
    for item in items:
        marker = parse_marker(str(item.get(instruction_field) or ""))
        if marker and marker.get("managed_by") == MANAGED_BY and marker.get("workflow_id") == workflow_id and marker.get("object_key") == object_key:
            marked.append(item)
        if item.get(name_field) in accepted_names:
            name_matches.append(item)
    errors: list[str] = []
    if len(marked) > 1:
        errors.append(f"multiple managed objects match {object_key}")
        return None, False, errors
    if marked:
        collisions = [item for item in name_matches if item.get("id") != marked[0].get("id")]
        if collisions:
            errors.append(f"managed object {object_key} conflicts with another object using an accepted name")
            return None, False, errors
        return marked[0], True, errors
    if len(name_matches) > 1:
        errors.append(f"multiple objects have name {name!r}")
        return None, False, errors
    if name_matches:
        return name_matches[0], False, errors
    return None, False, errors


def resolve_human_approver_id(
    state: dict[str, Any], manifest: dict[str, Any], workflow_id: str
) -> tuple[str | None, str | None]:
    human_members = manifest.get("squad", {}).get("human_members") or []
    contract = human_members[0] if human_members else {}
    selector = contract.get("selector")
    if selector == "current_user":
        user_id = str(state.get("user", {}).get("id") or "")
        return (user_id, None) if user_id else (None, "authenticated user ID unavailable")

    squad = manifest.get("squad") or {}
    current, _, errors = match_managed(
        state.get("squads", []),
        workflow_id,
        f"squad.{squad.get('key')}",
        str(squad.get("name") or ""),
        "instructions",
    )
    if errors:
        return None, "; ".join(errors)
    if not current:
        return None, "managed squad is unavailable for human approver resolution"
    role = manifest.get("workflow", {}).get("approver_role")
    matches = [
        item
        for item in state.get("squad_members", {}).get(str(current.get("id")), [])
        if item.get("member_type") == "member" and item.get("role") == role
    ]
    if len(matches) != 1:
        return None, f"expected one human approver with role {role}, found {len(matches)}"
    return str(matches[0].get("member_id")), None


def _runtime_choice(
    binding_key: str,
    binding: dict[str, Any],
    runtime_map: dict[str, Any],
    runtimes: list[dict[str, Any]],
    current: dict[str, Any] | None,
    runtime_by_id: dict[str, dict[str, Any]],
    rebind: bool,
) -> tuple[str | None, str | None]:
    provider = str(binding.get("provider", ""))
    if current and not rebind:
        current_runtime = runtime_by_id.get(str(current.get("runtime_id", "")))
        if current_runtime and current_runtime.get("provider") == provider:
            if current_runtime.get("status") != binding.get("required_status", "online"):
                return None, f"current Runtime for {current.get('name')} is {current_runtime.get('status')}"
            return str(current_runtime.get("id")), None
        return None, f"existing agent {current.get('name')} uses an incompatible Runtime; pass --rebind-runtimes"

    local = (runtime_map.get("bindings") or {}).get(binding_key) or {}
    runtime_id = local.get("runtime_id")
    if runtime_id:
        runtime = runtime_by_id.get(str(runtime_id))
        if not runtime:
            return None, f"runtime map binding {binding_key} references a missing Runtime"
        if runtime.get("provider") != provider:
            return None, f"runtime map binding {binding_key} provider mismatch"
        if runtime.get("status") != binding.get("required_status", "online"):
            return None, f"runtime map binding {binding_key} is {runtime.get('status')}"
        return str(runtime_id), None

    matches = [
        item
        for item in runtimes
        if item.get("provider") == provider and item.get("status") == binding.get("required_status", "online")
    ]
    if len(matches) == 1:
        return str(matches[0].get("id")), None
    if not matches:
        return None, f"no online Runtime matches provider {provider} for binding {binding_key}"
    return None, f"multiple Runtimes match binding {binding_key}; create a local runtime map"


def build_plan(
    root: Path,
    cli: MulticaCLI,
    workspace: dict[str, Any],
    deployment_profile: str,
    runtime_map_path: Path,
    adopt: bool,
    rebind_runtimes: bool,
    disable_operations: bool = False,
    allow_active_v3_degraded: bool = False,
    write_archives: bool = True,
) -> dict[str, Any]:
    if allow_active_v3_degraded and not disable_operations:
        raise WorkflowError("--allow-active-v3-degraded requires --disable-operations")
    manifest, profile = validate_repository(root, deployment_profile)
    workflow = manifest["workflow"]
    workflow_id = str(workflow["id"])
    state = fetch_state(cli, include_active_v3=disable_operations, workflow_id=workflow_id)
    actions: list[dict[str, Any]] = []
    runtime_map = load_runtime_map(runtime_map_path)
    runtimes = state.get("runtimes", [])
    runtime_by_id = runtime_index(state)

    active_v3 = state.get("active_v3_requirements", [])
    if disable_operations and active_v3:
        identifiers = [str(item.get("identifier") or item.get("id")) for item in active_v3]
        if allow_active_v3_degraded:
            actions.append(
                {
                    "type": "WARNING",
                    "key": "active-v3-rollback",
                    "reason": f"explicit degraded rollback with active v3 requirements: {identifiers}",
                }
            )
        else:
            actions.append(
                {
                    "type": "BLOCKED",
                    "key": "active-v3-rollback",
                    "reason": (
                        f"active v3 requirements must be frozen or explicitly approved with "
                        f"--allow-active-v3-degraded: {identifiers}"
                    ),
                }
            )

    desired_skill_by_key: dict[str, dict[str, Any]] = {}
    current_skills_by_name: dict[str, list[dict[str, Any]]] = {}
    for item in state.get("skills", []):
        current_skills_by_name.setdefault(str(item.get("name")), []).append(item)
    for skill in manifest.get("skills", []):
        skill_dir = root / skill["path"]
        desired_hash = package_hash(skill_dir)
        archive = root / f"build/skills/{skill['name']}-{desired_hash[:12]}.zip"
        if write_archives and "workspace" in skill.get("targets", []):
            built_hash = build_archive(skill_dir, archive)
            if built_hash != desired_hash:
                raise WorkflowError(f"skill hash mismatch while packaging {skill['name']}")
        desired = {**skill, "package_hash": desired_hash, "archive": str(archive)}
        desired_skill_by_key[skill["key"]] = desired
        if "workspace" not in skill.get("targets", []):
            continue
        name_matches = current_skills_by_name.get(skill["name"], [])
        if len(name_matches) > 1:
            actions.append({"type": "BLOCKED", "key": skill["key"], "reason": f"multiple workspace skills have name {skill['name']}"})
            continue
        current = name_matches[0] if name_matches else None
        if not current:
            actions.append({"type": "CREATE_SKILL", "key": skill["key"], "name": skill["name"], "desired": desired})
            continue
        detail = state.get("skill_details", {}).get(str(current.get("id")), current)
        managed = deep_find(detail, "managed_by") == MANAGED_BY and deep_find(detail, "workflow_id") == workflow_id
        current_hash = deep_find(detail, "package_hash")
        if not managed:
            if not adopt:
                actions.append({"type": "BLOCKED", "key": skill["key"], "reason": f"same-name unmarked workspace skill {skill['name']} requires --adopt"})
            else:
                actions.append({"type": "ADOPT_SKILL", "key": skill["key"], "name": skill["name"], "current_id": current.get("id"), "desired": desired})
        elif current_hash != desired_hash:
            actions.append({"type": "UPDATE_SKILL", "key": skill["key"], "name": skill["name"], "current_id": current.get("id"), "desired": desired})
        else:
            actions.append({"type": "NO_CHANGE", "key": f"skill.{skill['key']}"})

    current_agents = state.get("agents", [])
    desired_agents: dict[str, dict[str, Any]] = {}
    current_agents_by_key: dict[str, dict[str, Any]] = {}
    inherited_runtime_candidates: dict[str, set[str]] = {}
    for agent in manifest.get("agents", []):
        object_key = f"agent.{agent['key']}"
        current, marked, errors = match_managed(
            current_agents,
            workflow_id,
            object_key,
            agent["name"],
            "instructions",
            agent.get("previous_names") or [],
        )
        if errors or not current or not marked:
            continue
        binding = profile["bindings"].get(agent["runtime_binding"], {})
        runtime = runtime_by_id.get(str(current.get("runtime_id") or ""))
        if runtime and runtime.get("provider") == binding.get("provider") and runtime.get("status") == binding.get("required_status", "online"):
            inherited_runtime_candidates.setdefault(agent["runtime_binding"], set()).add(str(runtime["id"]))
    inherited_runtime_bindings = {
        key: next(iter(values)) for key, values in inherited_runtime_candidates.items() if len(values) == 1
    }
    for agent in manifest.get("agents", []):
        object_key = f"agent.{agent['key']}"
        current, marked, errors = match_managed(
            current_agents,
            workflow_id,
            object_key,
            agent["name"],
            "instructions",
            agent.get("previous_names") or [],
        )
        for error in errors:
            actions.append({"type": "BLOCKED", "key": agent["key"], "reason": error})
        if errors:
            continue
        if current and not marked and not adopt:
            actions.append({"type": "BLOCKED", "key": agent["key"], "reason": f"same-name unmarked agent {agent['name']} requires --adopt"})
            continue
        if current:
            current_agents_by_key[agent["key"]] = current
        binding = profile["bindings"][agent["runtime_binding"]]
        effective_runtime_map = runtime_map
        if not (runtime_map.get("bindings") or {}).get(agent["runtime_binding"]):
            inherited_id = inherited_runtime_bindings.get(agent["runtime_binding"])
            if inherited_id:
                effective_runtime_map = {
                    **runtime_map,
                    "bindings": {
                        **(runtime_map.get("bindings") or {}),
                        agent["runtime_binding"]: {"runtime_id": inherited_id},
                    },
                }
        selected_runtime, runtime_error = _runtime_choice(
            agent["runtime_binding"], binding, effective_runtime_map, runtimes, current, runtime_by_id, rebind_runtimes
        )
        if runtime_error:
            actions.append({"type": "BLOCKED", "key": agent["key"], "reason": runtime_error})
            continue
        body = instruction_body(root, agent["instruction_files"])
        spec = agent_spec(
            agent["key"],
            agent["name"],
            agent.get("description", ""),
            body,
            binding.get("provider", ""),
            binding.get("model", ""),
            binding.get("thinking_level", ""),
            int(agent.get("max_concurrent_tasks", 1)),
            agent.get("permission_mode", "private"),
        )
        spec_hash = sha256_value(spec)
        desired = {
            **agent,
            "runtime_id": selected_runtime,
            "provider": binding.get("provider", ""),
            "model": binding.get("model", ""),
            "thinking_level": binding.get("thinking_level", ""),
            "instructions": render_marker(workflow_id, object_key, spec_hash, body),
            "spec_hash": spec_hash,
            "set_runtime": bool(not current or rebind_runtimes),
        }
        desired_agents[agent["key"]] = desired
        if not current:
            actions.append({"type": "CREATE_AGENT", "key": agent["key"], "desired": desired})
            continue
        current_runtime = runtime_by_id.get(str(current.get("runtime_id", "")), {})
        current_spec = agent_spec(
            agent["key"],
            str(current.get("name", "")),
            str(current.get("description", "")),
            strip_marker(str(current.get("instructions") or "")),
            str(current_runtime.get("provider", "")),
            str(current.get("model") or ""),
            str(current.get("thinking_level") or ""),
            int(current.get("max_concurrent_tasks") or 0),
            normalize_permission(current, cli.workspace_id),
        )
        current_hash = sha256_value(current_spec)
        runtime_changed = bool(rebind_runtimes and str(current.get("runtime_id")) != str(selected_runtime))
        if current_hash == spec_hash and marked and not runtime_changed:
            actions.append({"type": "NO_CHANGE", "key": object_key})
        else:
            action_type = "ADOPT_AGENT" if not marked else "UPDATE_AGENT"
            actions.append(
                {
                    "type": action_type,
                    "key": agent["key"],
                    "current_id": current.get("id"),
                    "current_hash": current_hash,
                    "desired_hash": spec_hash,
                    "desired": desired,
                }
            )

    # Agent skill assignment reconciliation is non-destructive for unrelated skills.
    skill_by_id = {str(item.get("id")): item for item in state.get("skills", []) if item.get("id")}
    desired_attached = desired_skill_attachments(manifest, disable_operations)
    operations = manifest.get("operations") or {}
    for agent_key in desired_attached:
        current_agent = current_agents_by_key.get(agent_key)
        current_assignments = state.get("agent_skills", {}).get(str(current_agent.get("id")), []) if current_agent else []
        current_ids = {str(item.get("id") or item.get("skill_id")) for item in current_assignments}
        current_managed_keys = set()
        for skill_id in current_ids:
            detail = state.get("skill_details", {}).get(skill_id, skill_by_id.get(skill_id, {}))
            if deep_find(detail, "managed_by") == MANAGED_BY and deep_find(detail, "workflow_id") == workflow_id:
                name = str(skill_by_id.get(skill_id, {}).get("name") or detail.get("name") or "")
                for skill_key, desired_skill in desired_skill_by_key.items():
                    if desired_skill["name"] == name:
                        current_managed_keys.add(skill_key)
        for skill_key in sorted(desired_attached.get(agent_key, set()) - current_managed_keys):
            actions.append({"type": "ATTACH_SKILL", "agent_key": agent_key, "skill_key": skill_key})
        for skill_key in sorted(current_managed_keys - desired_attached.get(agent_key, set())):
            actions.append({"type": "DETACH_SKILL", "agent_key": agent_key, "skill_key": skill_key})

    current_projects_by_key: dict[str, dict[str, Any]] = {}
    for project in manifest.get("projects", []):
        lead_key = project["lead"]
        lead_name = manifest_agent_name(manifest, lead_key)
        lead_current = current_agents_by_key.get(lead_key)
        exact_lead_name_matches = [
            item for item in state.get("agents", []) if str(item.get("name") or "") == lead_name
        ]
        lead_collisions = [
            item
            for item in exact_lead_name_matches
            if not lead_current or str(item.get("id")) != str(lead_current.get("id"))
        ]
        if lead_collisions:
            actions.append(
                {
                    "type": "BLOCKED",
                    "key": project["key"],
                    "reason": f"project lead name is not unique for {lead_key}",
                }
            )
            continue
        object_key = f"project.{project['key']}"
        current, marked, errors = match_managed(
            state.get("projects", []),
            workflow_id,
            object_key,
            project["title"],
            "description",
            project.get("previous_titles") or [],
            name_field="title",
        )
        for error in errors:
            actions.append({"type": "BLOCKED", "key": project["key"], "reason": error})
        if errors:
            continue
        if current and not marked and not adopt:
            actions.append(
                {
                    "type": "BLOCKED",
                    "key": project["key"],
                    "reason": f"same-title unmarked project {project['title']} requires --adopt",
                }
            )
            continue
        if current:
            current_projects_by_key[project["key"]] = current
        body = str(project.get("description") or "").strip() + "\n"
        spec = project_spec(
            project["key"],
            project["title"],
            body,
            project["lead"],
            project["status"],
            str(project.get("icon") or ""),
        )
        spec_hash = sha256_value(spec)
        desired = {
            **project,
            "description": render_marker(workflow_id, object_key, spec_hash, body),
            "spec_hash": spec_hash,
        }
        if not current:
            actions.append({"type": "CREATE_PROJECT", "key": project["key"], "desired": desired})
            continue
        lead_current = current_agents_by_key.get(project["lead"])
        current_spec = project_spec(
            project["key"],
            str(current.get("title") or ""),
            strip_marker(str(current.get("description") or "")),
            project["lead"]
            if lead_current and str(current.get("lead_id")) == str(lead_current.get("id"))
            else str(current.get("lead_id") or ""),
            str(current.get("status") or ""),
            str(current.get("icon") or ""),
        )
        current_hash = sha256_value(current_spec)
        if current_hash == spec_hash and marked:
            actions.append({"type": "NO_CHANGE", "key": object_key})
        else:
            actions.append(
                {
                    "type": "ADOPT_PROJECT" if not marked else "UPDATE_PROJECT",
                    "key": project["key"],
                    "current_id": current.get("id"),
                    "current_hash": current_hash,
                    "desired_hash": spec_hash,
                    "desired": desired,
                }
            )

    approver_id, approver_error = resolve_human_approver_id(state, manifest, workflow_id)
    current_autopilots_by_key: dict[str, dict[str, Any]] = {}
    for autopilot in manifest.get("autopilots", []):
        object_key = f"autopilot.{autopilot['key']}"
        current, marked, errors = match_managed(
            autopilot_state_items(state),
            workflow_id,
            object_key,
            autopilot["title"],
            "description",
            autopilot.get("previous_titles") or [],
            name_field="title",
        )
        for error in errors:
            actions.append({"type": "BLOCKED", "key": autopilot["key"], "reason": error})
        if errors:
            continue
        if current and not marked and not adopt:
            actions.append(
                {
                    "type": "BLOCKED",
                    "key": autopilot["key"],
                    "reason": f"same-title unmarked autopilot {autopilot['title']} requires --adopt",
                }
            )
            continue
        if current:
            current_autopilots_by_key[autopilot["key"]] = current

        subscriber_ids: list[str] = []
        subscriber_error = None
        for selector in autopilot.get("subscribers") or []:
            if selector == "current_user":
                value = str(state.get("user", {}).get("id") or "")
                if not value:
                    subscriber_error = "authenticated user ID unavailable for Autopilot subscriber"
                    break
                subscriber_ids.append(value)
            elif selector == "human_approver":
                if approver_error or not approver_id:
                    subscriber_error = approver_error or "human approver unavailable"
                    break
                subscriber_ids.append(approver_id)
        if subscriber_error:
            actions.append({"type": "BLOCKED", "key": autopilot["key"], "reason": subscriber_error})
            continue

        effective_status = str(autopilot["status"])
        if disable_operations and operations.get("autopilot") == autopilot["key"]:
            effective_status = "paused"
        body = str(autopilot.get("description") or "").strip() + "\n"
        spec = autopilot_spec(
            autopilot["key"],
            autopilot["title"],
            body,
            autopilot["agent"],
            autopilot["mode"],
            autopilot["project"],
            effective_status,
            str(autopilot.get("issue_title_template") or ""),
            subscriber_ids,
        )
        spec_hash = sha256_value(spec)
        desired = {
            **autopilot,
            "status": effective_status,
            "description": render_marker(workflow_id, object_key, spec_hash, body),
            "subscriber_ids": sorted(set(subscriber_ids)),
            "spec_hash": spec_hash,
        }
        if not current:
            actions.append({"type": "CREATE_AUTOPILOT", "key": autopilot["key"], "desired": desired})
        else:
            detail = state.get("autopilot_details", {}).get(str(current.get("id")), current)
            detail = normalized_autopilot_detail(detail, current)
            required_fields = ["title", "description", "agent_id", "mode", "project_id", "status", "triggers"]
            missing_fields = [field for field in required_fields if field not in detail]
            if "subscribers" not in detail and "subscriber_ids" not in detail:
                missing_fields.append("subscribers")
            if missing_fields:
                actions.append(
                    {
                        "type": "BLOCKED",
                        "key": autopilot["key"],
                        "reason": f"Autopilot get cannot verify managed fields: {sorted(missing_fields)}",
                    }
                )
                continue
            agent_current = current_agents_by_key.get(autopilot["agent"])
            project_current = current_projects_by_key.get(autopilot["project"])
            current_spec = autopilot_spec(
                autopilot["key"],
                str(detail.get("title") or current.get("title") or ""),
                strip_marker(str(detail.get("description") or current.get("description") or "")),
                autopilot["agent"]
                if agent_current and str(detail.get("agent_id")) == str(agent_current.get("id"))
                else str(detail.get("agent_id") or ""),
                str(detail.get("mode") or ""),
                autopilot["project"]
                if project_current and str(detail.get("project_id")) == str(project_current.get("id"))
                else str(detail.get("project_id") or ""),
                str(detail.get("status") or ""),
                str(detail.get("issue_title_template") or ""),
                normalized_subscriber_ids(detail.get("subscribers") or detail.get("subscriber_ids")),
            )
            current_hash = sha256_value(current_spec)
            current_marker = parse_marker(str(detail.get("description") or "")) or {}
            if (
                current_hash == spec_hash
                and marked
                and current_marker.get("spec_hash") == spec_hash
            ):
                actions.append({"type": "NO_CHANGE", "key": object_key})
            else:
                actions.append(
                    {
                        "type": "ADOPT_AUTOPILOT" if not marked else "UPDATE_AUTOPILOT",
                        "key": autopilot["key"],
                        "current_id": current.get("id"),
                        "current_hash": current_hash,
                        "desired_hash": spec_hash,
                        "desired": desired,
                    }
                )

        current_detail = (
            state.get("autopilot_details", {}).get(str(current.get("id")), current) if current else {}
        )
        current_triggers = normalized_triggers(current_detail.get("triggers"))
        for trigger in autopilot.get("triggers") or []:
            matches = [item for item in current_triggers if item.get("label") == trigger["label"]]
            if len(matches) > 1:
                actions.append(
                    {
                        "type": "BLOCKED",
                        "key": f"{autopilot['key']}.{trigger['key']}",
                        "reason": f"multiple Autopilot triggers use label {trigger['label']}",
                    }
                )
                continue
            if not matches:
                actions.append(
                    {
                        "type": "ADD_AUTOPILOT_TRIGGER",
                        "key": f"{autopilot['key']}.{trigger['key']}",
                        "autopilot_key": autopilot["key"],
                        "desired": trigger,
                    }
                )
                continue
            existing_trigger = matches[0]
            if existing_trigger.get("kind") != trigger.get("kind"):
                actions.append(
                    {
                        "type": "BLOCKED",
                        "key": f"{autopilot['key']}.{trigger['key']}",
                        "reason": "managed Autopilot trigger kind replacement requires explicit redesign",
                    }
                )
                continue
            desired_trigger = {
                "kind": trigger.get("kind"),
                "label": trigger.get("label"),
                "enabled": bool(trigger.get("enabled", True)),
                "cron": trigger.get("cron") or "",
                "timezone": trigger.get("timezone") or "UTC",
            }
            current_trigger = {key: existing_trigger.get(key) for key in desired_trigger}
            if current_trigger != desired_trigger:
                if not existing_trigger.get("id"):
                    actions.append(
                        {
                            "type": "BLOCKED",
                            "key": f"{autopilot['key']}.{trigger['key']}",
                            "reason": (
                                "managed Autopilot trigger differs from desired state but "
                                "the CLI omitted its trigger ID"
                            ),
                        }
                    )
                    continue
                actions.append(
                    {
                        "type": "UPDATE_AUTOPILOT_TRIGGER",
                        "key": f"{autopilot['key']}.{trigger['key']}",
                        "autopilot_key": autopilot["key"],
                        "current_id": existing_trigger.get("id"),
                        "desired": trigger,
                    }
                )

    squad = manifest["squad"]
    squad_object_key = f"squad.{squad['key']}"
    current_squad, squad_marked, squad_errors = match_managed(
        state.get("squads", []), workflow_id, squad_object_key, squad["name"], "instructions"
    )
    squad_blocked = bool(squad_errors)
    for error in squad_errors:
        actions.append({"type": "BLOCKED", "key": squad["key"], "reason": error})
    if current_squad and not squad_marked and not adopt:
        actions.append({"type": "BLOCKED", "key": squad["key"], "reason": f"same-name unmarked squad {squad['name']} requires --adopt"})
        squad_blocked = True
        current_squad = None
    squad_body = instruction_body(root, squad["instruction_files"])
    squad_spec = {
        "key": squad["key"],
        "name": squad["name"],
        "description": squad.get("description", ""),
        "instructions": squad_body,
        "leader": squad["leader"],
    }
    squad_hash = sha256_value(squad_spec)
    desired_squad = {
        **squad,
        "instructions": render_marker(workflow_id, squad_object_key, squad_hash, squad_body),
        "spec_hash": squad_hash,
    }
    if not current_squad and not squad_blocked:
        actions.append({"type": "CREATE_SQUAD", "key": squad["key"], "desired": desired_squad})
    elif current_squad:
        leader_current = current_agents_by_key.get(squad["leader"])
        current_spec = {
            "key": squad["key"],
            "name": current_squad.get("name", ""),
            "description": current_squad.get("description", ""),
            "instructions": strip_marker(str(current_squad.get("instructions") or "")),
            "leader": squad["leader"] if leader_current and current_squad.get("leader_id") == leader_current.get("id") else str(current_squad.get("leader_id")),
        }
        current_hash = sha256_value(current_spec)
        if current_hash == squad_hash and squad_marked:
            actions.append({"type": "NO_CHANGE", "key": squad_object_key})
        else:
            actions.append(
                {
                    "type": "ADOPT_SQUAD" if not squad_marked else "UPDATE_SQUAD",
                    "key": squad["key"],
                    "current_id": current_squad.get("id"),
                    "current_hash": current_hash,
                    "desired_hash": squad_hash,
                    "desired": desired_squad,
                }
            )

    if current_squad or not squad_blocked:
        members = state.get("squad_members", {}).get(str(current_squad.get("id")), []) if current_squad else []
        expected_roles = {member["role"] for member in squad.get("agent_members", [])}
        approver_role = workflow["approver_role"]
        expected_agent_ids = {
            key: str(value.get("id")) for key, value in current_agents_by_key.items() if value.get("id")
        }
        for member in squad.get("agent_members", []):
            agent_key = member["agent"]
            agent_id = expected_agent_ids.get(agent_key)
            if not agent_id:
                actions.append({"type": "ADD_MEMBER", "member_type": "agent", "member_ref": agent_key, "role": member["role"]})
                continue
            current_member = next(
                (item for item in members if item.get("member_type") == "agent" and str(item.get("member_id")) == agent_id),
                None,
            )
            if not current_member:
                actions.append({"type": "ADD_MEMBER", "member_type": "agent", "member_ref": agent_key, "role": member["role"]})
            elif current_member.get("role") != member["role"]:
                actions.append({"type": "SET_ROLE", "member_type": "agent", "member_ref": agent_key, "role": member["role"]})
        approvers = [
            item for item in members if item.get("member_type") == "member" and item.get("role") == approver_role
        ]
        if len(approvers) > 1:
            actions.append({"type": "BLOCKED", "key": "human-approver", "reason": "multiple human approvers exist in squad roster"})
        elif not approvers:
            user_id = str(state.get("user", {}).get("id", ""))
            if not user_id:
                actions.append({"type": "BLOCKED", "key": "human-approver", "reason": "authenticated user ID unavailable"})
            else:
                existing_user = next(
                    (item for item in members if item.get("member_type") == "member" and str(item.get("member_id")) == user_id),
                    None,
                )
                actions.append(
                    {
                        "type": "SET_ROLE" if existing_user else "ADD_MEMBER",
                        "member_type": "member",
                        "member_ref": "current_user",
                        "role": approver_role,
                    }
                )
        for member in members:
            role = str(member.get("role") or "")
            if member.get("member_type") == "member" and role == approver_role:
                continue
            if member.get("member_type") == "agent" and str(member.get("member_id")) in expected_agent_ids.values():
                continue
            if role in expected_roles or role == approver_role:
                actions.append({"type": "BLOCKED", "key": "roster", "reason": f"unmanaged roster member conflicts with managed role {role}"})
            else:
                actions.append({"type": "WARNING", "key": "roster", "reason": f"preserving unmanaged roster member with role {role or '<empty>'}"})

    source_commit = git_head(root)
    manifest_hash = desired_source_hash(root, manifest, profile)
    runtime_map_hash = sha256_value(runtime_map)
    plan = {
        "schema_version": 1,
        "created_at": utc_now(),
        "source_commit": source_commit,
        "draft": source_commit == "UNCOMMITTED" or git_dirty(root),
        "workflow_version": workflow["version"],
        "workflow_id": workflow_id,
        "profile": cli.profile,
        "workspace": {"id": workspace.get("id"), "name": workspace.get("name"), "slug": workspace.get("slug")},
        "deployment_profile": deployment_profile,
        "runtime_map_path": str(runtime_map_path),
        "runtime_map_hash": runtime_map_hash,
        "manifest_hash": manifest_hash,
        "observed_hash": observed_hash(state),
        "adopt": adopt,
        "rebind_runtimes": rebind_runtimes,
        "disable_operations": disable_operations,
        "allow_active_v3_degraded": allow_active_v3_degraded,
        "active_v3_requirements": active_v3,
        "actions": actions,
    }
    plan["plan_digest"] = sha256_value(plan)
    return plan


def summarize_actions(actions: list[dict[str, Any]]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for action in actions:
        summary[action["type"]] = summary.get(action["type"], 0) + 1
    return dict(sorted(summary.items()))


def plan_has_blockers(plan: dict[str, Any]) -> bool:
    return any(action.get("type") == "BLOCKED" for action in plan.get("actions", []))


def mutation_actions(plan: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        action
        for action in plan.get("actions", [])
        if action.get("type") not in {"NO_CHANGE", "WARNING", "BLOCKED"}
    ]


def save_plan(root: Path, plan: dict[str, Any]) -> Path:
    short = str(plan["plan_digest"])[:12]
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = root / f".multica/plans/{stamp}-{short}.json"
    write_json(path, plan)
    return path


def find_by_managed_key(
    items: list[dict[str, Any]],
    workflow_id: str,
    object_key: str,
    name: str,
    field: str,
    previous_names: list[str] | None = None,
    name_field: str = "name",
) -> dict[str, Any] | None:
    current, _, errors = match_managed(
        items, workflow_id, object_key, name, field, previous_names, name_field=name_field
    )
    if errors:
        raise WorkflowError("; ".join(errors))
    return current


def _permission_args(permission: str) -> list[str]:
    if permission == "public_to_workspace":
        return ["--permission-mode", "public_to", "--public-to-workspace"]
    return ["--permission-mode", "private"]


def _agent_create_args(desired: dict[str, Any]) -> list[str]:
    args = [
        "agent",
        "create",
        "--name",
        desired["name"],
        "--description",
        desired.get("description", ""),
        "--instructions",
        desired["instructions"],
        "--runtime-id",
        desired["runtime_id"],
        "--model",
        desired.get("model", ""),
        "--max-concurrent-tasks",
        str(desired.get("max_concurrent_tasks", 1)),
        "--output",
        "json",
    ]
    if desired.get("thinking_level"):
        args.extend(["--thinking-level", desired["thinking_level"]])
    args.extend(_permission_args(desired.get("permission_mode", "private")))
    return args


def _agent_update_args(agent_id: str, desired: dict[str, Any]) -> list[str]:
    args = [
        "agent",
        "update",
        agent_id,
        "--name",
        desired["name"],
        "--description",
        desired.get("description", ""),
        "--instructions",
        desired["instructions"],
        "--model",
        desired.get("model", ""),
        "--thinking-level",
        desired.get("thinking_level", ""),
        "--max-concurrent-tasks",
        str(desired.get("max_concurrent_tasks", 1)),
        "--output",
        "json",
    ]
    if desired.get("set_runtime"):
        args.extend(["--runtime-id", desired["runtime_id"]])
    args.extend(_permission_args(desired.get("permission_mode", "private")))
    return args


def _project_create_args(desired: dict[str, Any], lead_name: str) -> list[str]:
    args = [
        "project",
        "create",
        "--title",
        desired["title"],
        "--description",
        desired.get("description", ""),
        "--lead",
        lead_name,
        "--status",
        desired.get("status", "in_progress"),
        "--output",
        "json",
    ]
    if desired.get("icon"):
        args.extend(["--icon", desired["icon"]])
    return args


def _project_update_args(project_id: str, desired: dict[str, Any], lead_name: str) -> list[str]:
    args = [
        "project",
        "update",
        project_id,
        "--title",
        desired["title"],
        "--description",
        desired.get("description", ""),
        "--lead",
        lead_name,
        "--status",
        desired.get("status", "in_progress"),
        "--output",
        "json",
    ]
    if desired.get("icon"):
        args.extend(["--icon", desired["icon"]])
    return args


def _autopilot_create_args(
    desired: dict[str, Any], agent_id: str, project_id: str
) -> list[str]:
    args = [
        "autopilot",
        "create",
        "--agent",
        agent_id,
        "--description",
        desired.get("description", ""),
        "--mode",
        desired["mode"],
        "--title",
        desired["title"],
        "--project",
        project_id,
        "--output",
        "json",
    ]
    if desired.get("issue_title_template"):
        args.extend(["--issue-title-template", desired["issue_title_template"]])
    for subscriber_id in desired.get("subscriber_ids") or []:
        args.extend(["--subscriber", subscriber_id])
    return args


def _autopilot_update_args(
    autopilot_id: str, desired: dict[str, Any], agent_id: str, project_id: str
) -> list[str]:
    args = [
        "autopilot",
        "update",
        autopilot_id,
        "--agent",
        agent_id,
        "--description",
        desired.get("description", ""),
        "--mode",
        desired["mode"],
        "--title",
        desired["title"],
        "--project",
        project_id,
        "--status",
        desired.get("status", "active"),
        "--output",
        "json",
    ]
    if desired.get("issue_title_template"):
        args.extend(["--issue-title-template", desired["issue_title_template"]])
    subscriber_ids = desired.get("subscriber_ids") or []
    if subscriber_ids:
        for subscriber_id in subscriber_ids:
            args.extend(["--subscriber", subscriber_id])
    else:
        args.append("--clear-subscribers")
    return args


def _autopilot_status_update_args(autopilot_id: str, status: str) -> list[str]:
    return [
        "autopilot",
        "update",
        autopilot_id,
        "--status",
        status,
        "--output",
        "json",
    ]


def _trigger_add_args(autopilot_id: str, desired: dict[str, Any]) -> list[str]:
    args = [
        "autopilot",
        "trigger-add",
        autopilot_id,
        "--kind",
        desired["kind"],
        "--label",
        desired["label"],
        "--output",
        "json",
    ]
    if desired["kind"] == "schedule":
        args.extend(["--cron", desired["cron"], "--timezone", desired["timezone"]])
    return args


def _trigger_update_args(
    autopilot_id: str, trigger_id: str, desired: dict[str, Any]
) -> list[str]:
    args = [
        "autopilot",
        "trigger-update",
        autopilot_id,
        trigger_id,
        "--label",
        desired["label"],
        f"--enabled={'true' if desired.get('enabled', True) else 'false'}",
        "--output",
        "json",
    ]
    if desired["kind"] == "schedule":
        args.extend(["--cron", desired["cron"], "--timezone", desired["timezone"]])
    return args


def _refresh_maps(cli: MulticaCLI, manifest: dict[str, Any]) -> tuple[dict[str, str], dict[str, str], dict[str, Any]]:
    state = fetch_state(cli)
    workflow_id = manifest["workflow"]["id"]
    agent_ids: dict[str, str] = {}
    for agent in manifest["agents"]:
        current = find_by_managed_key(
            state["agents"],
            workflow_id,
            f"agent.{agent['key']}",
            agent["name"],
            "instructions",
            agent.get("previous_names") or [],
        )
        if current and current.get("id"):
            agent_ids[agent["key"]] = str(current["id"])
    skill_ids: dict[str, str] = {}
    for skill in manifest["skills"]:
        if "workspace" not in skill.get("targets", []):
            continue
        matches = [
            item for item in state["skills"] if item.get("name") == skill["name"]
        ]
        if len(matches) != 1:
            raise WorkflowError(
                f"expected one workspace Skill named {skill['name']} after apply; found {len(matches)}"
            )
        current = matches[0]
        detail = state.get("skill_details", {}).get(str(current.get("id") or ""), current)
        if (
            deep_find(detail, "managed_by") != MANAGED_BY
            or deep_find(detail, "workflow_id") != workflow_id
        ):
            raise WorkflowError(
                f"workspace Skill {skill['name']} is not managed after apply"
            )
        if not current.get("id"):
            raise WorkflowError(f"workspace Skill {skill['name']} has no ID after apply")
        skill_ids[skill["key"]] = str(current["id"])
    return agent_ids, skill_ids, state


def apply_plan(root: Path, cli: MulticaCLI, plan_path: Path, approval: str) -> dict[str, Any]:
    plan = read_json(plan_path)
    expected_digest = str(plan.get("plan_digest", ""))
    payload = {key: value for key, value in plan.items() if key != "plan_digest"}
    actual_digest = sha256_value(payload)
    if actual_digest != expected_digest:
        raise WorkflowError("plan file digest is invalid or the plan was modified")
    if approval not in {expected_digest, expected_digest[:12]}:
        raise WorkflowError("approval digest does not match the plan")
    if plan.get("draft"):
        raise WorkflowError("draft plans created from an uncommitted or dirty worktree cannot be applied")
    if plan_has_blockers(plan):
        raise WorkflowError("plan contains BLOCKED actions")
    if git_head(root) != plan.get("source_commit"):
        raise WorkflowError("Git HEAD changed after planning; generate a new plan")
    if git_dirty(root):
        raise WorkflowError("working tree is dirty; apply requires the exact reviewed checkout")
    manifest, profile = validate_repository(root, str(plan["deployment_profile"]))
    if desired_source_hash(root, manifest, profile) != plan.get("manifest_hash"):
        raise WorkflowError("manifest or deployment profile changed after planning")
    runtime_map = load_runtime_map(Path(plan["runtime_map_path"]))
    if sha256_value(runtime_map) != plan.get("runtime_map_hash"):
        raise WorkflowError("runtime map changed after planning")
    current_state = fetch_state(
        cli,
        include_active_v3=bool(plan.get("disable_operations", False)),
        workflow_id=str(plan.get("workflow_id") or ""),
    )
    if observed_hash(current_state) != plan.get("observed_hash"):
        raise WorkflowError("Multica state changed after planning; generate a new plan")

    journal = {
        "started_at": utc_now(),
        "plan_digest": expected_digest,
        "source_commit": plan["source_commit"],
        "workspace": plan["workspace"],
        "completed": [],
    }
    journal_path = root / f".multica/journals/{expected_digest[:12]}.json"
    workflow_id = manifest["workflow"]["id"]
    reporter_agent_keys = set((manifest.get("operations") or {}).get("reporter_agents") or [])
    defer_reporter_enablement = not bool(plan.get("disable_operations", False))
    deferred_agent_actions = []

    for action in plan.get("actions", []):
        action_type = action.get("type")
        if action_type in {"NO_CHANGE", "WARNING"}:
            continue
        if (
            defer_reporter_enablement
            and action_type in {"CREATE_AGENT", "ADOPT_AGENT", "UPDATE_AGENT"}
            and action.get("key") in reporter_agent_keys
        ):
            deferred_agent_actions.append(action)
            continue
        handled = False
        if action_type in {"CREATE_SKILL", "ADOPT_SKILL", "UPDATE_SKILL"}:
            desired = action["desired"]
            cli.json(
                [
                    "skill",
                    "import",
                    "--file",
                    desired["archive"],
                    "--on-conflict",
                    "overwrite" if action_type != "CREATE_SKILL" else "fail",
                    "--output",
                    "json",
                ]
            )
            handled = True
        elif action_type == "CREATE_AGENT":
            cli.json(_agent_create_args(action["desired"]))
            handled = True
        elif action_type in {"ADOPT_AGENT", "UPDATE_AGENT"}:
            cli.json(_agent_update_args(str(action["current_id"]), action["desired"]))
            handled = True
        if handled:
            journal["completed"].append({"type": action_type, "key": action.get("key"), "at": utc_now()})
            write_json(journal_path, journal)

    agent_ids, skill_ids, state = _refresh_maps(cli, manifest)

    # Apply skill assignments once per affected agent, preserving unrelated skills.
    affected_agents = {
        action["agent_key"]
        for action in plan.get("actions", [])
        if action.get("type") in {"ATTACH_SKILL", "DETACH_SKILL"}
    }
    desired_attached = desired_skill_attachments(
        manifest, bool(plan.get("disable_operations", False))
    )
    operations = manifest.get("operations") or {}
    deferred_reporters = (
        set(operations.get("reporter_agents") or []) & affected_agents
        if not bool(plan.get("disable_operations", False))
        else set()
    )

    def apply_agent_skill_assignments(
        agent_keys: set[str], current_agent_ids: dict[str, str], current_skill_ids: dict[str, str], current_state: dict[str, Any]
    ) -> None:
        managed_skill_ids = set(current_skill_ids.values())
        for agent_key in sorted(agent_keys):
            agent_id = current_agent_ids[agent_key]
            current = current_state.get("agent_skills", {}).get(agent_id, [])
            current_ids = {str(item.get("id") or item.get("skill_id")) for item in current}
            unmanaged = current_ids - managed_skill_ids
            desired_ids = {
                current_skill_ids[key]
                for key in desired_attached.get(agent_key, set())
                if key in current_skill_ids
            }
            union = sorted(unmanaged | desired_ids)
            cli.json(["agent", "skills", "set", agent_id, "--skill-ids", ",".join(union), "--output", "json"])
            journal["completed"].append({"type": "SET_AGENT_SKILLS", "key": agent_key, "at": utc_now()})
            write_json(journal_path, journal)

    apply_agent_skill_assignments(affected_agents - deferred_reporters, agent_ids, skill_ids, state)

    agent_ids, _, state = _refresh_maps(cli, manifest)
    project_actions = [
        action
        for action in plan.get("actions", [])
        if action.get("type") in {"CREATE_PROJECT", "ADOPT_PROJECT", "UPDATE_PROJECT"}
    ]
    for action in project_actions:
        desired = action["desired"]
        lead_id = agent_ids[desired["lead"]]
        exact_name_matches = [
            item for item in state.get("agents", []) if item.get("name") == manifest_agent_name(manifest, desired["lead"])
        ]
        if len(exact_name_matches) != 1 or str(exact_name_matches[0].get("id")) != lead_id:
            raise WorkflowError(f"project lead name is not unique for {desired['lead']}")
        lead_name = str(exact_name_matches[0]["name"])
        if action["type"] == "CREATE_PROJECT":
            current = cli.json(_project_create_args(desired, lead_name))
            project_id = str(current.get("id") or "")
        else:
            project_id = str(action["current_id"])
        updated = cli.json(_project_update_args(project_id, desired, lead_name))
        if str(updated.get("lead_id") or "") != lead_id:
            raise WorkflowError(f"project lead verification failed for {desired['key']}")
        journal["completed"].append({"type": action["type"], "key": action.get("key"), "at": utc_now()})
        write_json(journal_path, journal)

    agent_ids, _, state = _refresh_maps(cli, manifest)
    project_ids: dict[str, str] = {}
    for project in manifest.get("projects", []):
        current = find_by_managed_key(
            state.get("projects", []),
            workflow_id,
            f"project.{project['key']}",
            project["title"],
            "description",
            project.get("previous_titles") or [],
            name_field="title",
        )
        if current and current.get("id"):
            project_ids[project["key"]] = str(current["id"])

    autopilot_actions = [
        action
        for action in plan.get("actions", [])
        if action.get("type") in {"CREATE_AUTOPILOT", "ADOPT_AUTOPILOT", "UPDATE_AUTOPILOT"}
    ]
    for action in autopilot_actions:
        desired = action["desired"]
        agent_id = agent_ids[desired["agent"]]
        project_id = project_ids[desired["project"]]
        if action["type"] == "CREATE_AUTOPILOT":
            raw_current = cli.json(_autopilot_create_args(desired, agent_id, project_id))
            current = normalized_autopilot_detail(raw_current, {})
            autopilot_id = str(current.get("id") or "")
            if not autopilot_id:
                raise WorkflowError(f"Autopilot create did not return an ID for {desired['key']}")
            if desired.get("status", "active") != "active":
                cli.json(
                    _autopilot_status_update_args(
                        autopilot_id, desired.get("status", "active")
                    )
                )
        else:
            autopilot_id = str(action["current_id"])
            cli.json(_autopilot_update_args(autopilot_id, desired, agent_id, project_id))
        journal["completed"].append({"type": action["type"], "key": action.get("key"), "at": utc_now()})
        write_json(journal_path, journal)

    agent_ids, _, state = _refresh_maps(cli, manifest)
    autopilot_ids: dict[str, str] = {}
    for autopilot in manifest.get("autopilots", []):
        current = find_by_managed_key(
            autopilot_state_items(state),
            workflow_id,
            f"autopilot.{autopilot['key']}",
            autopilot["title"],
            "description",
            autopilot.get("previous_titles") or [],
            name_field="title",
        )
        if current and current.get("id"):
            autopilot_ids[autopilot["key"]] = str(current["id"])

    for action in plan.get("actions", []):
        if action.get("type") not in {"ADD_AUTOPILOT_TRIGGER", "UPDATE_AUTOPILOT_TRIGGER"}:
            continue
        autopilot_id = autopilot_ids[action["autopilot_key"]]
        if action["type"] == "ADD_AUTOPILOT_TRIGGER":
            created_trigger = cli.json(_trigger_add_args(autopilot_id, action["desired"]))
            if not action["desired"].get("enabled", True):
                trigger_id = str(created_trigger.get("id") or "")
                if not trigger_id:
                    raise WorkflowError(f"created trigger returned no ID for {action.get('key')}")
                cli.json(_trigger_update_args(autopilot_id, trigger_id, action["desired"]))
        else:
            cli.json(
                _trigger_update_args(
                    autopilot_id, str(action["current_id"]), action["desired"]
                )
            )
        journal["completed"].append({"type": action["type"], "key": action.get("key"), "at": utc_now()})
        write_json(journal_path, journal)

    for action in deferred_agent_actions:
        if action["type"] == "CREATE_AGENT":
            cli.json(_agent_create_args(action["desired"]))
        else:
            cli.json(_agent_update_args(str(action["current_id"]), action["desired"]))
        journal["completed"].append({"type": action["type"], "key": action.get("key"), "at": utc_now()})
        write_json(journal_path, journal)

    if deferred_reporters:
        agent_ids, skill_ids, state = _refresh_maps(cli, manifest)
        apply_agent_skill_assignments(deferred_reporters, agent_ids, skill_ids, state)

    # Create or update the squad after agents exist.
    agent_ids, _, state = _refresh_maps(cli, manifest)
    squad_actions = [
        action for action in plan.get("actions", []) if action.get("type") in {"CREATE_SQUAD", "ADOPT_SQUAD", "UPDATE_SQUAD"}
    ]
    for action in squad_actions:
        desired = action["desired"]
        leader_id = agent_ids[desired["leader"]]
        if action["type"] == "CREATE_SQUAD":
            created = cli.json(
                [
                    "squad",
                    "create",
                    "--name",
                    desired["name"],
                    "--description",
                    desired.get("description", ""),
                    "--leader",
                    leader_id,
                    "--output",
                    "json",
                ]
            )
            squad_id = str(created.get("id"))
        else:
            squad_id = str(action["current_id"])
        cli.json(
            [
                "squad",
                "update",
                squad_id,
                "--name",
                desired["name"],
                "--description",
                desired.get("description", ""),
                "--instructions",
                desired["instructions"],
                "--leader",
                leader_id,
                "--output",
                "json",
            ]
        )
        journal["completed"].append({"type": action["type"], "key": action.get("key"), "at": utc_now()})
        write_json(journal_path, journal)

    agent_ids, _, state = _refresh_maps(cli, manifest)
    squad_spec = manifest["squad"]
    squad_current = find_by_managed_key(
        state["squads"], workflow_id, f"squad.{squad_spec['key']}", squad_spec["name"], "instructions"
    )
    if not squad_current:
        raise WorkflowError("managed squad not found after squad reconciliation")
    squad_id = str(squad_current["id"])
    user_id = str(state.get("user", {}).get("id", ""))
    for action in plan.get("actions", []):
        if action.get("type") not in {"ADD_MEMBER", "SET_ROLE"}:
            continue
        member_type = action["member_type"]
        member_ref = action["member_ref"]
        member_id = user_id if member_ref == "current_user" else agent_ids[member_ref]
        current_members = _as_list(
            cli.json(["squad", "member", "list", squad_id, "--output", "json"]), "members"
        )
        existing = next(
            (
                item
                for item in current_members
                if item.get("member_type") == member_type and str(item.get("member_id")) == member_id
            ),
            None,
        )
        if existing and existing.get("role") == action["role"]:
            continue
        if not existing:
            cli.json(
                [
                    "squad",
                    "member",
                    "add",
                    squad_id,
                    "--member-id",
                    member_id,
                    "--type",
                    member_type,
                    "--role",
                    action["role"],
                    "--output",
                    "json",
                ]
            )
        else:
            cli.json(
                [
                    "squad",
                    "member",
                    "set-role",
                    squad_id,
                    "--member-id",
                    member_id,
                    "--member-type",
                    member_type,
                    "--role",
                    action["role"],
                    "--output",
                    "json",
                ]
            )
        journal["completed"].append({"type": action["type"], "key": member_ref, "at": utc_now()})
        write_json(journal_path, journal)

    journal["finished_at"] = utc_now()
    write_json(journal_path, journal)
    return journal


def install_skills(root: Path, target: Path, copy_mode: bool, replace_existing: bool) -> list[dict[str, str]]:
    manifest = read_json(root / "workflow.json")
    results: list[dict[str, str]] = []
    target.mkdir(parents=True, exist_ok=True)
    for skill in manifest.get("skills", []):
        if "local" not in skill.get("targets", []):
            continue
        source = (root / skill["path"]).resolve()
        destination = target / skill["name"]
        if destination.exists() or destination.is_symlink():
            try:
                if destination.resolve() == source:
                    results.append({"skill": skill["name"], "mode": "existing-link", "path": str(destination)})
                    continue
            except OSError:
                pass
            if not replace_existing:
                raise WorkflowError(f"skill destination exists: {destination}; pass --replace-existing after review")
            if destination.is_symlink() or destination.is_file():
                destination.unlink()
            else:
                shutil.rmtree(destination)
        mode = "copy" if copy_mode else "link"
        if copy_mode:
            shutil.copytree(source, destination)
        else:
            try:
                os.symlink(source, destination, target_is_directory=True)
            except OSError:
                if os.name == "nt":
                    junction = subprocess.run(
                        ["cmd.exe", "/d", "/c", "mklink", "/J", str(destination), str(source)],
                        capture_output=True,
                        text=True,
                        encoding="utf-8",
                        errors="replace",
                    )
                    if junction.returncode == 0:
                        mode = "junction"
                    else:
                        shutil.copytree(source, destination)
                        mode = "copy-fallback"
                else:
                    shutil.copytree(source, destination)
                    mode = "copy-fallback"
        results.append({"skill": skill["name"], "mode": mode, "path": str(destination)})
    return results
