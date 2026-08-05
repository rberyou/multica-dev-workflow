#!/usr/bin/env python3
"""Core reconciliation logic for the packaged Multica workflow."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
from typing import Any, Iterable

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from package_skills import build_archive, package_hash, source_files


MANAGED_BY = "multica-dev-workflow"
RETIRED_LOCAL_SKILL_NAMES = {
    "multica-workflow-observer",
    "multica-workflow-maintainer",
    "multica-workflow-maintenance-reviewer",
}
MARKER_RE = re.compile(r"\A<!-- multica-workflow\r?\n(?P<body>.*?)\r?\n-->\r?\n?", re.DOTALL)
UUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
WORKFLOW_VERSION_LITERAL_RE = re.compile(
    r"\bworkflow_version=([0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?)\b"
)
RELEASE_MANIFEST_NAME = "release-manifest.json"
RELEASE_PROTECTED_DIRECTORIES = {
    "deployment-profiles",
    "instructions",
    "scripts",
    "skills",
}
RELEASE_PROTECTED_FILES = {
    "VERSION",
    "requirements.txt",
    "workflow.json",
    "workflow.schema.json",
}


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


def deployment_record_path(root: Path, workspace_id: str) -> Path:
    key = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:16]
    return root / f".multica/deployments/{key}.json"


def deployment_evidence_record_path(
    root: Path, workspace_id: str, plan_digest: str
) -> Path:
    key = hashlib.sha256(workspace_id.encode("utf-8")).hexdigest()[:16]
    return root / ".multica/deployments" / key / f"{plan_digest}.json"


def load_deployment_record(root: Path, workspace_id: str) -> dict[str, Any] | None:
    path = deployment_record_path(root, workspace_id)
    if not path.is_file():
        return None
    try:
        value = read_json(path)
    except (OSError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


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


def _release_path(root: Path, relative: str) -> Path:
    posix = PurePosixPath(relative)
    if posix.is_absolute() or not posix.parts or ".." in posix.parts:
        raise WorkflowError(f"release manifest contains an unsafe path: {relative}")
    path = root.joinpath(*posix.parts).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError as exc:
        raise WorkflowError(
            f"release manifest path escapes the bundle: {relative}"
        ) from exc
    return path


def validate_release_bundle(root: Path) -> dict[str, Any]:
    manifest_path = root / RELEASE_MANIFEST_NAME
    manifest = read_json(manifest_path)
    if not isinstance(manifest, dict):
        raise WorkflowError("release manifest must be a JSON object")
    if manifest.get("schema_version") != 1:
        raise WorkflowError("release manifest schema_version must be 1")
    files = manifest.get("files")
    if not isinstance(files, dict) or not files:
        raise WorkflowError("release manifest must contain file hashes")
    expected_digest = str(manifest.get("bundle_digest") or "")
    payload = {
        key: value for key, value in manifest.items() if key != "bundle_digest"
    }
    if not expected_digest or sha256_value(payload) != expected_digest:
        raise WorkflowError("release manifest digest is invalid or was modified")

    workflow_doc = read_json(root / "workflow.json")
    workflow = workflow_doc.get("workflow") if isinstance(workflow_doc, dict) else {}
    if not isinstance(workflow, dict):
        workflow = {}
    if str(manifest.get("workflow_id") or "") != str(workflow.get("id") or ""):
        raise WorkflowError("release manifest workflow_id does not match workflow.json")
    if str(manifest.get("workflow_version") or "") != str(
        workflow.get("version") or ""
    ):
        raise WorkflowError(
            "release manifest workflow_version does not match workflow.json"
        )
    if not str(manifest.get("release_tag") or ""):
        raise WorkflowError("release manifest has no release_tag")
    if not re.fullmatch(r"[0-9a-f]{40}", str(manifest.get("source_commit") or "")):
        raise WorkflowError("release manifest has an invalid source_commit")

    declared: set[str] = set()
    for relative, expected_hash in files.items():
        relative_text = str(relative)
        path = _release_path(root, relative_text)
        if not path.is_file():
            raise WorkflowError(f"release bundle is missing file: {relative_text}")
        if not re.fullmatch(r"[0-9a-f]{64}", str(expected_hash)):
            raise WorkflowError(
                f"release manifest has an invalid hash for {relative_text}"
            )
        if sha256_file(path) != expected_hash:
            raise WorkflowError(f"release bundle file changed: {relative_text}")
        declared.add(PurePosixPath(relative_text).as_posix())

    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        parts = PurePosixPath(relative).parts
        if relative == RELEASE_MANIFEST_NAME or not parts:
            continue
        if parts[0] in {
            ".git",
            ".multica",
            ".pytest_cache",
            ".venv",
            "build",
            "exports",
        } or "__pycache__" in parts or path.suffix == ".pyc":
            continue
        protected = (
            parts[0] in RELEASE_PROTECTED_DIRECTORIES
            or relative in RELEASE_PROTECTED_FILES
        )
        if protected and relative not in declared:
            raise WorkflowError(
                f"release bundle contains an undeclared workflow file: {relative}"
            )
    return manifest


def source_identity(root: Path) -> tuple[dict[str, str], bool]:
    if (root / RELEASE_MANIFEST_NAME).is_file():
        manifest = validate_release_bundle(root)
        return (
            {
                "type": "release_bundle",
                "id": str(manifest["bundle_digest"]),
                "release_tag": str(manifest.get("release_tag") or ""),
                "git_commit": str(manifest.get("source_commit") or ""),
            },
            False,
        )
    commit = git_head(root)
    dirty = commit == "UNCOMMITTED" or git_dirty(root)
    return {"type": "git", "id": commit, "git_commit": commit}, dirty


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
    if manifest.get("schema_version") != 3:
        errors.append("schema_version must be 3")
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
    squad_agent_keys = {
        str(member.get("agent") or "")
        for member in squad.get("agent_members") or []
        if member.get("agent")
    }
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
    delivery_skill = next(
        (
            skill
            for skill in manifest.get("skills") or []
            if skill.get("key") == "delivery-policy"
        ),
        None,
    )
    if not delivery_skill:
        errors.append("delivery-policy skill is required")
    else:
        if "workspace" not in set(delivery_skill.get("targets") or []):
            errors.append("delivery-policy skill must target workspace")
        if set(delivery_skill.get("attach_to") or []) != squad_agent_keys:
            errors.append("delivery-policy skill must attach to every squad agent")
        delivery_root = root / str(delivery_skill.get("path", ""))
        delivery_schema_path = delivery_root / "references/project-delivery.schema.json"
        delivery_example_path = delivery_root / "references/project-delivery.example.json"
        if not delivery_schema_path.is_file() or not delivery_example_path.is_file():
            errors.append("delivery-policy skill requires its project schema and example")
        else:
            try:
                delivery_schema = read_json(delivery_schema_path)
                delivery_example = read_json(delivery_example_path)
                Draft202012Validator.check_schema(delivery_schema)
                example_errors = sorted(
                    Draft202012Validator(delivery_schema).iter_errors(delivery_example),
                    key=lambda item: tuple(str(part) for part in item.absolute_path),
                )
                for item in example_errors:
                    location = ".".join(str(part) for part in item.absolute_path) or "<root>"
                    errors.append(f"project delivery example {location}: {item.message}")
            except (WorkflowError, OSError, json.JSONDecodeError, SchemaError) as exc:
                errors.append(f"invalid delivery-policy schema or example: {exc}")
    projects = manifest.get("projects") or []
    project_keys = [project.get("key") for project in projects]
    project_titles = [project.get("title") for project in projects]
    if len(set(project_keys)) != len(project_keys) or len(set(project_titles)) != len(project_titles):
        errors.append("project keys and titles must be unique")
    for project in projects:
        if project.get("lead") not in agent_keys:
            errors.append(f"project {project.get('key')} references missing lead agent {project.get('lead')}")
    project_key_set = set(project_keys)
    incidents = manifest.get("incidents")
    if not isinstance(incidents, dict):
        errors.append("incidents is required for schema_version 3")
    else:
        for field in ["project", "skill", "reporter_agents"]:
            if field not in incidents:
                errors.append(f"incidents.{field} is required")
        if incidents.get("project") not in project_key_set:
            errors.append("incidents.project must reference a managed project")
        if incidents.get("skill") not in skill_keys:
            errors.append("incidents.skill must reference a managed skill")
        unknown_reporters = set(incidents.get("reporter_agents") or []) - set(agent_keys)
        if unknown_reporters:
            errors.append(f"incidents.reporter_agents reference unknown agents: {sorted(unknown_reporters)}")
        reporter_agents = set(incidents.get("reporter_agents") or [])
        squad_agents = squad_agent_keys
        if reporter_agents != squad_agents:
            errors.append(
                "incidents.reporter_agents must exactly match squad.agent_members agents"
            )
        incident_skill = next(
            (
                skill
                for skill in manifest.get("skills") or []
                if skill.get("key") == incidents.get("skill")
            ),
            None,
        )
        if incident_skill:
            if "workspace" not in set(incident_skill.get("targets") or []):
                errors.append("incidents.skill must target workspace")
            if set(incident_skill.get("attach_to") or []) != reporter_agents:
                errors.append(
                    "incidents.skill attach_to must exactly match reporter agents"
                )
    portable_files = [
        root / "workflow.json",
        *root.glob("deployment-profiles/*.json"),
        *root.glob("instructions/**/*.md"),
        *root.glob("skills/**/*.md"),
        *root.glob("skills/**/*.yaml"),
        *root.glob("skills/**/*.py"),
        *root.glob("skills/**/*.json"),
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
    runtime_controls: dict[str, Any] | None = None,
) -> dict[str, Any]:
    value = {
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
    if runtime_controls:
        value["runtime_controls"] = runtime_controls
    return value


def normalized_json_object(value: Any) -> dict[str, Any] | None:
    if value in (None, ""):
        return None
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise WorkflowError("managed Agent runtime control must be valid JSON") from exc
    if not isinstance(value, dict):
        raise WorkflowError("managed Agent runtime control must be a JSON object")
    return value


def managed_agent_runtime_controls(
    desired_agent: dict[str, Any], observed_agent: dict[str, Any] | None = None
) -> dict[str, Any]:
    source = observed_agent if observed_agent is not None else desired_agent
    result: dict[str, Any] = {}
    if "custom_args" in desired_agent:
        value = source.get("custom_args")
        result["custom_args"] = list(value) if isinstance(value, list) else []
    if "mcp_config" in desired_agent:
        result["mcp_config"] = normalized_json_object(source.get("mcp_config")) or {}
    if "runtime_config" in desired_agent:
        result["runtime_config"] = normalized_json_object(source.get("runtime_config")) or {}
    return result


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


def manifest_agent_name(manifest: dict[str, Any], agent_key: str) -> str:
    for agent in manifest.get("agents", []):
        if agent.get("key") == agent_key:
            return str(agent.get("name") or "")
    raise WorkflowError(f"manifest agent not found: {agent_key}")


def desired_skill_attachments(manifest: dict[str, Any]) -> dict[str, set[str]]:
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


def fetch_state(cli: MulticaCLI) -> dict[str, Any]:
    agents = _as_list(cli.json(["agent", "list", "--output", "json"]), "agents")
    enriched_agents = []
    for current in agents:
        agent_id = str(current.get("id") or "")
        detail = cli.json(["agent", "get", agent_id, "--output", "json"]) if agent_id else None
        enriched_agents.append({**current, **(detail if isinstance(detail, dict) else {})})
    agents = enriched_agents
    squads = _as_list(cli.json(["squad", "list", "--output", "json"]), "squads")
    skills = _as_list(cli.json(["skill", "list", "--output", "json"]), "skills")
    projects = _as_list(cli.json(["project", "list", "--output", "json"]), "projects")
    autopilots = _as_list(
        cli.json(["autopilot", "list", "--output", "json"]), "autopilots"
    )
    autopilot_details: dict[str, dict[str, Any]] = {}
    for autopilot in autopilots:
        autopilot_id = str(autopilot.get("id") or "")
        if autopilot_id:
            detail = cli.json(
                ["autopilot", "get", autopilot_id, "--output", "json"]
            )
            if isinstance(detail, dict):
                nested = detail.get("autopilot")
                autopilot_details[autopilot_id] = (
                    {**autopilot, **nested, **{key: value for key, value in detail.items() if key != "autopilot"}}
                    if isinstance(nested, dict)
                    else {**autopilot, **detail}
                )
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
                "custom_args",
                "mcp_config",
                "runtime_config",
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
    autopilots = [
        {
            key: detail.get(key)
            for key in ["id", "title", "description", "agent_id", "project_id", "status"]
        }
        for autopilot in state.get("autopilots", [])
        for detail in [
            state.get("autopilot_details", {}).get(
                str(autopilot.get("id") or ""), autopilot
            )
        ]
    ]
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
    workflow = manifest["workflow"]
    workflow_id = str(workflow["id"])
    state = fetch_state(cli)
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
        if not managed:
            if not adopt:
                actions.append({"type": "BLOCKED", "key": skill["key"], "reason": f"same-name unmarked workspace skill {skill['name']} requires --adopt"})
            else:
                actions.append({"type": "ADOPT_SKILL", "key": skill["key"], "name": skill["name"], "current_id": current.get("id"), "desired": desired})
        elif current_hash != desired_hash:
            actions.append({"type": "UPDATE_SKILL", "key": skill["key"], "name": skill["name"], "current_id": current.get("id"), "desired": desired})
        else:
            actions.append({"type": "NO_CHANGE", "key": f"skill.{skill['key']}"})

    desired_workspace_skill_names = {
        str(skill["name"])
        for skill in manifest.get("skills", [])
        if "workspace" in skill.get("targets", [])
    }
    retired_skill_ids: set[str] = set()
    for current in state.get("skills", []):
        skill_id = str(current.get("id") or "")
        detail = state.get("skill_details", {}).get(skill_id, current)
        if (
            deep_find(detail, "managed_by") == MANAGED_BY
            and deep_find(detail, "workflow_id") == workflow_id
            and str(current.get("name") or detail.get("name") or "")
            not in desired_workspace_skill_names
        ):
            if not skill_id:
                actions.append(
                    {
                        "type": "BLOCKED",
                        "key": str(current.get("name") or "retired-skill"),
                        "reason": "retired managed Skill has no ID",
                    }
                )
                continue
            retired_skill_ids.add(skill_id)
            actions.append(
                {
                    "type": "DELETE_RETIRED_SKILL",
                    "key": str(current.get("name") or skill_id),
                    "current_id": skill_id,
                }
            )

    current_agents = state.get("agents", [])
    desired_agent_object_keys = {
        f"agent.{agent['key']}" for agent in manifest.get("agents", [])
    }
    retired_agent_ids: set[str] = set()
    for current in current_agents:
        marker = parse_marker(str(current.get("instructions") or "")) or {}
        object_key = str(marker.get("object_key") or "")
        if (
            marker.get("managed_by") == MANAGED_BY
            and marker.get("workflow_id") == workflow_id
            and object_key.startswith("agent.")
            and object_key not in desired_agent_object_keys
        ):
            if not current.get("id"):
                actions.append(
                    {
                        "type": "BLOCKED",
                        "key": object_key,
                        "reason": "retired managed Agent has no ID",
                    }
                )
            else:
                retired_agent_ids.add(str(current["id"]))
                actions.append(
                    {
                        "type": "ARCHIVE_RETIRED_AGENT",
                        "key": object_key,
                        "current_id": str(current["id"]),
                    }
                )
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
            managed_agent_runtime_controls(agent),
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
            managed_agent_runtime_controls(agent, current),
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
    desired_attached = desired_skill_attachments(manifest)
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

    if retired_skill_ids:
        for agent in state.get("agents", []):
            agent_id = str(agent.get("id") or "")
            current_ids = {
                str(item.get("id") or item.get("skill_id") or "")
                for item in state.get("agent_skills", {}).get(agent_id, [])
            }
            attached_retired = sorted(current_ids & retired_skill_ids)
            if attached_retired:
                actions.append(
                    {
                        "type": "DETACH_RETIRED_SKILLS",
                        "key": str(agent.get("name") or agent_id),
                        "current_id": agent_id,
                        "retired_skill_ids": attached_retired,
                        "remaining_skill_ids": sorted(current_ids - retired_skill_ids),
                    }
                )

    for autopilot in state.get("autopilots", []):
        autopilot_id = str(autopilot.get("id") or "")
        detail = state.get("autopilot_details", {}).get(autopilot_id, autopilot)
        marker = parse_marker(str(detail.get("description") or "")) or {}
        if (
            marker.get("managed_by") == MANAGED_BY
            and marker.get("workflow_id") == workflow_id
            and str(marker.get("object_key") or "").startswith("autopilot.")
        ):
            if not autopilot_id:
                actions.append(
                    {
                        "type": "BLOCKED",
                        "key": str(marker.get("object_key") or "retired-autopilot"),
                        "reason": "retired managed automation has no ID",
                    }
                )
            else:
                actions.append(
                    {
                        "type": "DELETE_RETIRED_AUTOPILOT",
                        "key": str(marker.get("object_key") or autopilot_id),
                        "current_id": autopilot_id,
                    }
                )

    for project in manifest.get("projects", []):
        lead_key = project["lead"]
        lead_name = manifest_agent_name(manifest, lead_key)
        lead_current = current_agents_by_key.get(lead_key)
        exact_lead_name_matches = [
            item
            for item in state.get("agents", [])
            if str(item.get("name") or "") == lead_name
        ]
        lead_collisions = [
            item
            for item in exact_lead_name_matches
            if not lead_current
            or str(item.get("id")) != str(lead_current.get("id"))
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
            actions.append(
                {"type": "BLOCKED", "key": project["key"], "reason": error}
            )
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
            "description": render_marker(
                workflow_id, object_key, spec_hash, body
            ),
            "spec_hash": spec_hash,
        }
        if not current:
            actions.append(
                {"type": "CREATE_PROJECT", "key": project["key"], "desired": desired}
            )
            continue
        lead_current = current_agents_by_key.get(project["lead"])
        current_spec = project_spec(
            project["key"],
            str(current.get("title") or ""),
            strip_marker(str(current.get("description") or "")),
            project["lead"]
            if lead_current
            and str(current.get("lead_id")) == str(lead_current.get("id"))
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

    desired_project_object_keys = {
        f"project.{project['key']}" for project in manifest.get("projects", [])
    }
    retirement_lead_key = str(manifest["squad"]["leader"])
    for current in state.get("projects", []):
        marker = parse_marker(str(current.get("description") or "")) or {}
        object_key = str(marker.get("object_key") or "")
        if (
            marker.get("managed_by") == MANAGED_BY
            and marker.get("workflow_id") == workflow_id
            and object_key.startswith("project.")
            and object_key not in desired_project_object_keys
        ):
            if str(current.get("lead_id") or "") in retired_agent_ids:
                if not current.get("id"):
                    actions.append(
                        {
                            "type": "BLOCKED",
                            "key": object_key,
                            "reason": "retired managed Project has no ID for lead reassignment",
                        }
                    )
                else:
                    actions.append(
                        {
                            "type": "REASSIGN_RETIRED_PROJECT_LEAD",
                            "key": object_key,
                            "current_id": str(current["id"]),
                            "lead_key": retirement_lead_key,
                        }
                    )
            actions.append(
                {
                    "type": "WARNING",
                    "key": object_key,
                    "reason": "preserving retired managed Project and its durable history",
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
            member_id = str(member.get("member_id") or "")
            if (
                member.get("member_type") == "agent"
                and member_id in retired_agent_ids
            ):
                actions.append(
                    {
                        "type": "REMOVE_RETIRED_MEMBER",
                        "key": member_id,
                        "squad_id": str(current_squad["id"]),
                        "member_id": member_id,
                        "member_type": "agent",
                    }
                )
                continue
            if member.get("member_type") == "member" and role == approver_role:
                continue
            if member.get("member_type") == "agent" and member_id in expected_agent_ids.values():
                continue
            if role in expected_roles or role == approver_role:
                actions.append({"type": "BLOCKED", "key": "roster", "reason": f"unmanaged roster member conflicts with managed role {role}"})
            else:
                actions.append({"type": "WARNING", "key": "roster", "reason": f"preserving unmanaged roster member with role {role or '<empty>'}"})

    source, source_dirty = source_identity(root)
    source_commit = str(source.get("git_commit") or "")
    manifest_hash = desired_source_hash(root, manifest, profile)
    runtime_map_hash = sha256_value(runtime_map)
    plan = {
        "schema_version": 1,
        "created_at": utc_now(),
        "source": source,
        "source_commit": source_commit,
        "draft": source_dirty,
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
    if "custom_args" in desired:
        args.extend(["--custom-args", canonical_json(desired.get("custom_args") or [])])
    if "mcp_config" in desired:
        args.extend(["--mcp-config", canonical_json(desired.get("mcp_config") or {})])
    if "runtime_config" in desired:
        args.extend(["--runtime-config", canonical_json(desired.get("runtime_config") or {})])
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
    if "custom_args" in desired:
        args.extend(["--custom-args", canonical_json(desired.get("custom_args") or [])])
    if "mcp_config" in desired:
        args.extend(["--mcp-config", canonical_json(desired.get("mcp_config") or {})])
    if "runtime_config" in desired:
        args.extend(["--runtime-config", canonical_json(desired.get("runtime_config") or {})])
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


def finalize_deployment_record(
    root: Path,
    plan: dict[str, Any],
    journal_path: Path,
    journal: dict[str, Any],
    plan_digest: str,
) -> dict[str, Any]:
    workspace_id = str((plan.get("workspace") or {}).get("id") or "")
    if not workspace_id:
        raise WorkflowError("deployment Plan has no workspace ID")
    record_path = deployment_evidence_record_path(root, workspace_id, plan_digest)
    latest_record_path = deployment_record_path(root, workspace_id)
    applied_actor = str(journal.get("applied_actor") or "")
    if not applied_actor:
        raise WorkflowError("completed apply journal has no applied actor")
    expected_record = {
        "schema_version": 1,
        "deployed_at": journal["finished_at"],
        "workflow_id": plan.get("workflow_id"),
        "workflow_version": plan.get("workflow_version"),
        "source_commit": plan.get("source_commit"),
        "plan_digest": plan_digest,
        "workspace": plan.get("workspace"),
        "profile": plan.get("profile"),
        "deployment_profile": plan.get("deployment_profile"),
        "applied_actor": applied_actor,
        "journal": str(journal_path),
    }
    if plan.get("source"):
        expected_record["source"] = plan.get("source")
    if record_path.is_file():
        deployment_record = read_json(record_path)
        if not isinstance(deployment_record, dict) or any(
            deployment_record.get(key) != value
            for key, value in expected_record.items()
        ):
            raise WorkflowError(
                "immutable deployment evidence differs from the completed apply journal"
            )
    else:
        deployment_record = expected_record
        write_json(record_path, deployment_record)
    update_latest = True
    if latest_record_path.is_file():
        latest_record = read_json(latest_record_path)
        if not isinstance(latest_record, dict):
            raise WorkflowError("Workspace deployment pointer is invalid")
        update_latest = str(latest_record.get("deployed_at") or "") <= str(
            deployment_record.get("deployed_at") or ""
        )
    if update_latest:
        write_json(latest_record_path, deployment_record)
    journal["deployment_record"] = str(record_path)
    write_json(journal_path, journal)
    return journal


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
        raise WorkflowError(
            "draft plans created from an uncommitted or dirty source cannot be applied"
        )
    if plan_has_blockers(plan):
        raise WorkflowError("plan contains BLOCKED actions")
    planned_source = plan.get("source")
    if isinstance(planned_source, dict):
        current_source, source_dirty = source_identity(root)
        if current_source != planned_source:
            raise WorkflowError("deployment source changed after planning")
        if source_dirty:
            raise WorkflowError(
                "deployment source is dirty; apply requires the exact reviewed source"
            )
    else:
        if git_head(root) != plan.get("source_commit"):
            raise WorkflowError("Git HEAD changed after planning; generate a new plan")
        if git_dirty(root):
            raise WorkflowError(
                "working tree is dirty; apply requires the exact reviewed checkout"
            )
    manifest, profile = validate_repository(root, str(plan["deployment_profile"]))
    if desired_source_hash(root, manifest, profile) != plan.get("manifest_hash"):
        raise WorkflowError("manifest or deployment profile changed after planning")
    runtime_map = load_runtime_map(Path(plan["runtime_map_path"]))
    if sha256_value(runtime_map) != plan.get("runtime_map_hash"):
        raise WorkflowError("runtime map changed after planning")
    journal_path = root / f".multica/journals/{expected_digest[:12]}.json"
    if journal_path.is_file():
        existing_journal = read_json(journal_path)
        if isinstance(existing_journal, dict) and existing_journal.get("finished_at"):
            if (
                str(existing_journal.get("plan_digest") or "") != expected_digest
                or (
                    plan.get("source")
                    and existing_journal.get("source") != plan.get("source")
                )
                or (
                    not plan.get("source")
                    and str(existing_journal.get("source_commit") or "")
                    != str(plan.get("source_commit") or "")
                )
                or existing_journal.get("workspace") != plan.get("workspace")
            ):
                raise WorkflowError("completed apply journal differs from the deployment Plan")
            return finalize_deployment_record(
                root, plan, journal_path, existing_journal, expected_digest
            )
    current_state = fetch_state(cli)
    if observed_hash(current_state) != plan.get("observed_hash"):
        raise WorkflowError("Multica state changed after planning; generate a new plan")

    journal = {
        "started_at": utc_now(),
        "plan_digest": expected_digest,
        "source": plan.get("source"),
        "source_commit": plan["source_commit"],
        "workspace": plan["workspace"],
        "applied_actor": os.environ.get("MULTICA_AGENT_ID") or "human_host",
        "completed": [],
    }
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

    for action in plan.get("actions", []):
        if action.get("type") != "DETACH_RETIRED_SKILLS":
            continue
        cli.json(
            [
                "agent",
                "skills",
                "set",
                str(action["current_id"]),
                "--skill-ids",
                ",".join(action.get("remaining_skill_ids") or []),
                "--output",
                "json",
            ]
        )
        journal["completed"].append(
            {"type": action["type"], "key": action.get("key"), "at": utc_now()}
        )
        write_json(journal_path, journal)

    for action in plan.get("actions", []):
        if action.get("type") != "REMOVE_RETIRED_MEMBER":
            continue
        cli.json(
            [
                "squad",
                "member",
                "remove",
                str(action["squad_id"]),
                "--member-id",
                str(action["member_id"]),
                "--type",
                str(action["member_type"]),
                "--output",
                "json",
            ]
        )
        journal["completed"].append(
            {"type": action["type"], "key": action.get("key"), "at": utc_now()}
        )
        write_json(journal_path, journal)

    for action in plan.get("actions", []):
        if action.get("type") != "DELETE_RETIRED_AUTOPILOT":
            continue
        cli.text(["autopilot", "delete", str(action["current_id"])])
        journal["completed"].append(
            {"type": action["type"], "key": action.get("key"), "at": utc_now()}
        )
        write_json(journal_path, journal)

    retirement_agent_ids, _, retirement_state = _refresh_maps(cli, manifest)
    for action in plan.get("actions", []):
        if action.get("type") != "REASSIGN_RETIRED_PROJECT_LEAD":
            continue
        lead_key = str(action["lead_key"])
        lead_id = retirement_agent_ids[lead_key]
        lead_name = manifest_agent_name(manifest, lead_key)
        exact_name_matches = [
            item
            for item in retirement_state.get("agents", [])
            if str(item.get("name") or "") == lead_name
        ]
        if (
            len(exact_name_matches) != 1
            or str(exact_name_matches[0].get("id") or "") != lead_id
        ):
            raise WorkflowError(
                f"retirement project lead name is not unique for {lead_key}"
            )
        updated = cli.json(
            [
                "project",
                "update",
                str(action["current_id"]),
                "--lead",
                lead_name,
                "--output",
                "json",
            ]
        )
        if str(updated.get("lead_id") or "") != lead_id:
            raise WorkflowError(
                f"retirement project lead verification failed for {action['key']}"
            )
        journal["completed"].append(
            {"type": action["type"], "key": action.get("key"), "at": utc_now()}
        )
        write_json(journal_path, journal)

    for action in plan.get("actions", []):
        if action.get("type") != "ARCHIVE_RETIRED_AGENT":
            continue
        cli.json(
            [
                "agent",
                "archive",
                str(action["current_id"]),
                "--output",
                "json",
            ]
        )
        journal["completed"].append(
            {"type": action["type"], "key": action.get("key"), "at": utc_now()}
        )
        write_json(journal_path, journal)

    for action in plan.get("actions", []):
        if action.get("type") != "DELETE_RETIRED_SKILL":
            continue
        cli.text(["skill", "delete", str(action["current_id"]), "--yes"])
        journal["completed"].append(
            {"type": action["type"], "key": action.get("key"), "at": utc_now()}
        )
        write_json(journal_path, journal)

    agent_ids, skill_ids, state = _refresh_maps(cli, manifest)

    # Apply skill assignments once per affected agent, preserving unrelated skills.
    affected_agents = {
        action["agent_key"]
        for action in plan.get("actions", [])
        if action.get("type") in {"ATTACH_SKILL", "DETACH_SKILL"}
    }
    desired_attached = desired_skill_attachments(manifest)

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

    apply_agent_skill_assignments(affected_agents, agent_ids, skill_ids, state)

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
    return finalize_deployment_record(
        root, plan, journal_path, journal, expected_digest
    )


def _remove_skill_destination(destination: Path) -> None:
    is_junction = bool(
        getattr(destination, "is_junction", lambda: False)()
    )
    if destination.is_symlink() or destination.is_file():
        destination.unlink()
    elif is_junction:
        destination.rmdir()
    else:
        shutil.rmtree(destination)


def _owned_retired_skill(destination: Path, expected_name: str) -> bool:
    skill_file = destination / "SKILL.md"
    if not skill_file.is_file():
        return destination.is_symlink() or bool(
            getattr(destination, "is_junction", lambda: False)()
        )
    content = skill_file.read_text(encoding="utf-8", errors="replace")
    name_match = re.search(r"(?m)^name:\s*(\S+)\s*$", content)
    return bool(
        name_match
        and name_match.group(1) == expected_name
        and re.search(rf"(?m)^\s*managed_by:\s*{re.escape(MANAGED_BY)}\s*$", content)
        and re.search(r"(?m)^\s*workflow_id:\s*development-delivery\s*$", content)
    )


def _copy_skill_source(source: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for path in source_files(source):
        relative = path.relative_to(source)
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def install_skills(root: Path, target: Path, copy_mode: bool, replace_existing: bool) -> list[dict[str, str]]:
    manifest = read_json(root / "workflow.json")
    results: list[dict[str, str]] = []
    target.mkdir(parents=True, exist_ok=True)
    for retired_name in sorted(RETIRED_LOCAL_SKILL_NAMES):
        destination = target / retired_name
        if not (
            destination.exists()
            or destination.is_symlink()
            or bool(getattr(destination, "is_junction", lambda: False)())
        ):
            continue
        if not _owned_retired_skill(destination, retired_name):
            raise WorkflowError(
                f"retired skill destination is not owned by this workflow: {destination}"
            )
        _remove_skill_destination(destination)
        results.append(
            {
                "skill": retired_name,
                "mode": "retired-removed",
                "path": str(destination),
            }
        )
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
            _remove_skill_destination(destination)
        mode = "copy" if copy_mode else "link"
        if copy_mode:
            _copy_skill_source(source, destination)
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
                        _copy_skill_source(source, destination)
                        mode = "copy-fallback"
                else:
                    _copy_skill_source(source, destination)
                    mode = "copy-fallback"
        results.append({"skill": skill["name"], "mode": mode, "path": str(destination)})
    return results
