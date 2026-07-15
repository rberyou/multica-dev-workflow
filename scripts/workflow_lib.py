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

from package_skills import build_archive, package_hash


MANAGED_BY = "multica-dev-workflow"
MARKER_RE = re.compile(r"\A<!-- multica-workflow\r?\n(?P<body>.*?)\r?\n-->\r?\n?", re.DOTALL)
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")


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


def validate_repository(root: Path, deployment_profile: str) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = read_json(root / "workflow.json")
    profile = read_json(root / f"deployment-profiles/{deployment_profile}.json")
    errors: list[str] = []
    if manifest.get("schema_version") != 1:
        errors.append("schema_version must be 1")
    workflow = manifest.get("workflow") or {}
    for field in ["id", "name", "version", "protocol_revision", "approver_role"]:
        if not workflow.get(field):
            errors.append(f"workflow.{field} is required")
    version_file = (root / "VERSION").read_text(encoding="utf-8").strip() if (root / "VERSION").is_file() else ""
    if version_file != workflow.get("version"):
        errors.append("VERSION must match workflow.version")
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
    portable_files = [root / "workflow.json", *root.glob("deployment-profiles/*.json"), *root.glob("instructions/**/*.md")]
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


def fetch_state(cli: MulticaCLI) -> dict[str, Any]:
    agents = _as_list(cli.json(["agent", "list", "--output", "json"]), "agents")
    squads = _as_list(cli.json(["squad", "list", "--output", "json"]), "squads")
    skills = _as_list(cli.json(["skill", "list", "--output", "json"]), "skills")
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
    return {
        "agents": agents,
        "squads": squads,
        "skills": skills,
        "runtimes": runtimes,
        "user": user,
        "squad_members": squad_members,
        "skill_details": skill_details,
        "agent_skills": agent_skills,
    }


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
    runtimes = [
        {key: runtime.get(key) for key in ["id", "provider", "status", "custom_name", "daemon_id"]}
        for runtime in state.get("runtimes", [])
    ]
    compact = {
        "agents": sorted(agents, key=lambda item: str(item.get("id"))),
        "squads": sorted(squads, key=lambda item: str(item.get("id"))),
        "skills": sorted(skills, key=lambda item: str(item.get("id"))),
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
) -> tuple[dict[str, Any] | None, bool, list[str]]:
    marked = []
    name_matches = []
    accepted_names = {name, *(previous_names or [])}
    for item in items:
        marker = parse_marker(str(item.get(instruction_field) or ""))
        if marker and marker.get("managed_by") == MANAGED_BY and marker.get("workflow_id") == workflow_id and marker.get("object_key") == object_key:
            marked.append(item)
        if item.get("name") in accepted_names:
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
    write_archives: bool = True,
) -> dict[str, Any]:
    manifest, profile = validate_repository(root, deployment_profile)
    state = fetch_state(cli)
    workflow = manifest["workflow"]
    workflow_id = str(workflow["id"])
    actions: list[dict[str, Any]] = []
    runtime_map = load_runtime_map(runtime_map_path)
    runtimes = state.get("runtimes", [])
    runtime_by_id = runtime_index(state)

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
        if not managed and not adopt:
            actions.append({"type": "BLOCKED", "key": skill["key"], "reason": f"same-name unmarked workspace skill {skill['name']} requires --adopt"})
        elif current_hash != desired_hash:
            actions.append({"type": "ADOPT_SKILL" if not managed else "UPDATE_SKILL", "key": skill["key"], "name": skill["name"], "current_id": current.get("id"), "desired": desired})
        else:
            actions.append({"type": "NO_CHANGE", "key": f"skill.{skill['key']}"})

    current_agents = state.get("agents", [])
    desired_agents: dict[str, dict[str, Any]] = {}
    current_agents_by_key: dict[str, dict[str, Any]] = {}
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
        selected_runtime, runtime_error = _runtime_choice(
            agent["runtime_binding"], binding, runtime_map, runtimes, current, runtime_by_id, rebind_runtimes
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
    desired_attached: dict[str, set[str]] = {agent["key"]: set() for agent in manifest.get("agents", [])}
    for skill in manifest.get("skills", []):
        if "workspace" not in skill.get("targets", []):
            continue
        for agent_key in skill.get("attach_to", []):
            desired_attached.setdefault(agent_key, set()).add(skill["key"])
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
) -> dict[str, Any] | None:
    current, _, errors = match_managed(items, workflow_id, object_key, name, field, previous_names)
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
        current = next((item for item in state["skills"] if item.get("name") == skill["name"]), None)
        if current and current.get("id"):
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
    current_state = fetch_state(cli)
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

    for action in plan.get("actions", []):
        action_type = action.get("type")
        if action_type in {"NO_CHANGE", "WARNING"}:
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
    desired_attached: dict[str, set[str]] = {agent["key"]: set() for agent in manifest["agents"]}
    for skill in manifest["skills"]:
        for agent_key in skill.get("attach_to", []):
            if "workspace" in skill.get("targets", []):
                desired_attached.setdefault(agent_key, set()).add(skill["key"])
    managed_skill_ids = {skill_ids[key] for key in skill_ids}
    for agent_key in affected_agents:
        agent_id = agent_ids[agent_key]
        current = state.get("agent_skills", {}).get(agent_id, [])
        current_ids = {str(item.get("id") or item.get("skill_id")) for item in current}
        unmanaged = current_ids - managed_skill_ids
        desired_ids = {skill_ids[key] for key in desired_attached.get(agent_key, set()) if key in skill_ids}
        union = sorted(unmanaged | desired_ids)
        cli.json(["agent", "skills", "set", agent_id, "--skill-ids", ",".join(union), "--output", "json"])
        journal["completed"].append({"type": "SET_AGENT_SKILLS", "key": agent_key, "at": utc_now()})
        write_json(journal_path, journal)

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
                shutil.copytree(source, destination)
                mode = "copy-fallback"
        results.append({"skill": skill["name"], "mode": mode, "path": str(destination)})
    return results
