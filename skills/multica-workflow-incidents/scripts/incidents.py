#!/usr/bin/env python3
"""Bind protocol v4 workflow Issues and persist event-driven Incidents."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from typing import Any


MANAGED_BY = "multica-dev-workflow"
WORKFLOW_ID = "development-delivery"
PROTOCOL_REVISION = "v4"
INCIDENT_PROJECT_KEY = "project.workflow-incidents"
LEADER_AGENT_KEY = "agent.leader"
DEVELOPMENT_SQUAD_KEY = "squad.development-delivery"
EXTERNAL_FIX_OBJECT_TYPE = "incident_fix_requirement"
EXTERNAL_FIX_MODE = "external"
STANDARD_CLOSURE_MODE = "standard"
INDEPENDENT_REMEDIATION_CLOSURE_MODE = "independent_remediation"
ACTIVE_STATUSES = {"backlog", "todo", "in_progress", "in_review", "blocked"}
SEVERITIES = {"low", "medium", "high", "urgent"}
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "urgent": 3}
WORKFLOW_OBJECT_TYPES = {
    "requirement",
    "plan",
    "design",
    "task_split",
    "implementation",
    "development_task",
    "integration_validation",
}
DEVELOPMENT_TREE_METADATA_KEYS = {
    "root_requirement_id",
    "protocol_revision",
    "top_protocol_revision",
    "created_by_role",
    "workflow_stage",
    "human_approver_id",
    "final_approval_gate_state",
    "final_approval_revision",
    "final_approval_comment_id",
    "final_approval_author_id",
    "delivery_policy_digest",
    "policy_digest",
    "plan_revision",
}
REFERENCE_TYPE_RE = re.compile(r"[a-z][a-z0-9._-]{1,63}")
REFERENCE_VALUE_RE = re.compile(r"\S{1,2000}")
MUTABLE_REFERENCE_RE = re.compile(
    r"(?i)(?:^|[/:._-])(latest|current|head|main|master|tip)(?:$|[/:._-])"
)
SECRET_KEY_RE = re.compile(
    r"token|secret|password|cookie|authorization|private[_-]?key|api[_-]?key|custom_env",
    re.I,
)
BEARER_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+\-/]+=*")
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S
)
SENSITIVE_HEADER_RE = re.compile(
    r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:\s*[^\r\n]+"
)
SENSITIVE_ENV_RE = re.compile(
    r"(?im)\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|COOKIE|API_KEY|PRIVATE_KEY)[A-Z0-9_]*)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)


class IncidentError(RuntimeError):
    pass


def utc_now() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_value(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def as_list(value: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return [item for item in value[key] if isinstance(item, dict)]
    return []


def issue_ref(issue: dict[str, Any]) -> str:
    return str(issue.get("identifier") or issue.get("key") or issue.get("id") or "")


def parse_marker(value: str | None) -> dict[str, str] | None:
    if not value or not value.startswith("<!-- multica-workflow\n"):
        return None
    end = value.find("-->\n")
    if end < 0:
        return None
    result = {}
    for line in value[len("<!-- multica-workflow\n") : end].splitlines():
        if "=" in line:
            key, item = line.split("=", 1)
            result[key.strip()] = item.strip()
    return result


def managed_match(
    items: list[dict[str, Any]], object_key: str, field: str
) -> dict[str, Any]:
    matches = []
    for item in items:
        marker = parse_marker(str(item.get(field) or ""))
        if (
            marker
            and marker.get("managed_by") == MANAGED_BY
            and marker.get("workflow_id") == WORKFLOW_ID
            and marker.get("object_key") == object_key
        ):
            matches.append(item)
    if len(matches) != 1:
        raise IncidentError(f"expected one managed {object_key}, found {len(matches)}")
    return matches[0]


def marker_is_managed(value: str | None) -> bool:
    marker = parse_marker(value)
    return bool(
        marker
        and marker.get("managed_by") == MANAGED_BY
        and marker.get("workflow_id") == WORKFLOW_ID
    )


def has_broken_marker(value: str | None) -> bool:
    return bool(
        value
        and value.startswith("<!-- multica-workflow\n")
        and not parse_marker(value)
    )


def multica_candidates(explicit: str | None = None) -> list[Path]:
    candidates = [
        Path(value).expanduser()
        for value in [
            explicit,
            os.environ.get("MULTICA_BIN"),
            shutil.which("multica"),
            shutil.which("multica.exe"),
        ]
        if value
    ]
    home = Path.home()
    system = platform.system().lower()
    if system == "windows":
        appdata = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
        candidates.append(appdata / "Multica/bin/multica.exe")
    elif system == "darwin":
        candidates.append(
            Path(
                "/Applications/Multica.app/Contents/Resources/app.asar.unpacked/resources/bin/multica"
            )
        )
    else:
        candidates.append(
            Path("/opt/Multica/resources/app.asar.unpacked/resources/bin/multica")
        )
    result = []
    seen = set()
    for candidate in candidates:
        text = str(candidate)
        if text not in seen:
            seen.add(text)
            result.append(candidate)
    return result


def discover_multica(explicit: str | None = None) -> str:
    for candidate in multica_candidates(explicit):
        if not candidate.is_file():
            continue
        result = subprocess.run(
            [str(candidate), "version", "--output", "json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode == 0:
            return str(candidate.resolve())
    raise IncidentError(
        "Multica CLI not found; set MULTICA_BIN or install/login through Multica Desktop"
    )


class CLI:
    def __init__(self, binary: str, profile: str | None, workspace_id: str):
        self.binary = binary
        self.profile = profile
        self.workspace_id = workspace_id

    def command(self, args: list[str]) -> list[str]:
        result = [self.binary]
        if self.profile:
            result.extend(["--profile", self.profile])
        return [*result, "--workspace-id", self.workspace_id, *args]

    def json(self, args: list[str], input_text: str | None = None) -> Any:
        result = subprocess.run(
            self.command(args),
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise IncidentError(f"multica {' '.join(args[:3])} failed: {detail}")
        if not result.stdout.strip():
            return {}
        try:
            return json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise IncidentError(
                f"multica returned invalid JSON for {' '.join(args[:3])}"
            ) from exc


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(key): "<redacted>"
            if SECRET_KEY_RE.search(str(key))
            and child is not None
            and child != ""
            and child is not False
            and child != 0
            else redact(child)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = PRIVATE_KEY_RE.sub("<redacted-private-key>", value)
        text = SENSITIVE_HEADER_RE.sub(
            lambda match: f"{match.group(1)}: <redacted>", text
        )
        text = SENSITIVE_ENV_RE.sub(
            lambda match: f"{match.group(1)}=<redacted>", text
        )
        text = BEARER_RE.sub("<redacted-credential>", text)
        return text[:4000] + ("...<truncated>" if len(text) > 4000 else "")
    return value


def redacted_text(value: Any, limit: int = 4000) -> str:
    text = str(redact(str(value)))
    return text[:limit] + ("...<truncated>" if len(text) > limit else "")


def metadata_map(cli: CLI, issue_id: str) -> dict[str, Any]:
    value = cli.json(["issue", "metadata", "list", issue_id, "--output", "json"])
    if isinstance(value, dict):
        if isinstance(value.get("metadata"), dict):
            return value["metadata"]
        if all(not isinstance(item, dict) for item in value.values()):
            return value
    result = {}
    for item in as_list(value, "metadata"):
        key = item.get("key") or item.get("name")
        if key:
            result[str(key)] = item.get("value")
    return result


def set_metadata(cli: CLI, issue_id: str, key: str, value: Any) -> None:
    if isinstance(value, bool):
        value_type = "bool"
        rendered = "true" if value else "false"
    elif isinstance(value, (int, float)):
        value_type = "number"
        rendered = str(value)
    else:
        value_type = "string"
        rendered = str(value)
    cli.json(
        [
            "issue",
            "metadata",
            "set",
            issue_id,
            "--key",
            key,
            "--value",
            rendered,
            "--type",
            value_type,
            "--output",
            "json",
        ]
    )


def set_metadata_map(cli: CLI, issue_id: str, values: dict[str, Any]) -> None:
    for key, value in values.items():
        set_metadata(cli, issue_id, key, value)


def add_comment(cli: CLI, issue_id: str, content: str) -> None:
    cli.json(
        ["issue", "comment", "add", issue_id, "--content-stdin", "--output", "json"],
        input_text=content,
    )


def skill_version() -> str:
    text = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(
        encoding="utf-8"
    )
    match = re.search(r"(?m)^\s*version:\s*(\S+)\s*$", text)
    return match.group(1) if match else "unknown"


def resolve_root_requirement(
    cli: CLI, issue: dict[str, Any]
) -> tuple[str, dict[str, Any]]:
    current = issue
    seen = set()
    while current.get("parent_issue_id"):
        parent_id = str(current["parent_issue_id"])
        if parent_id in seen or len(seen) >= 100:
            raise IncidentError("Issue parent chain contains a cycle or exceeds 100 levels")
        seen.add(parent_id)
        parent = cli.json(["issue", "get", parent_id, "--output", "json"])
        if not isinstance(parent, dict):
            raise IncidentError(f"parent Issue is unreadable: {parent_id}")
        current = parent
    return issue_ref(current), current


def resolve_control_plane(cli: CLI) -> tuple[dict[str, Any], dict[str, Any]]:
    projects = as_list(cli.json(["project", "list", "--output", "json"]), "projects")
    agents = as_list(cli.json(["agent", "list", "--output", "json"]), "agents")
    return (
        managed_match(projects, INCIDENT_PROJECT_KEY, "description"),
        managed_match(agents, LEADER_AGENT_KEY, "instructions"),
    )


def enrich_items(
    cli: CLI, noun: str, items: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    result = []
    for item in items:
        item_id = str(item.get("id") or "")
        detail = cli.json([noun, "get", item_id, "--output", "json"]) if item_id else {}
        result.append({**item, **(detail if isinstance(detail, dict) else {})})
    return result


def external_fix_control_plane(
    cli: CLI,
) -> tuple[
    list[dict[str, Any]],
    dict[str, Any],
    dict[str, Any],
    set[str],
    set[str],
]:
    projects = enrich_items(
        cli,
        "project",
        as_list(cli.json(["project", "list", "--output", "json"]), "projects"),
    )
    squads = enrich_items(
        cli,
        "squad",
        as_list(cli.json(["squad", "list", "--output", "json"]), "squads"),
    )
    agents = enrich_items(
        cli,
        "agent",
        as_list(cli.json(["agent", "list", "--output", "json"]), "agents"),
    )
    incident_project = managed_match(projects, INCIDENT_PROJECT_KEY, "description")
    development_squad = managed_match(squads, DEVELOPMENT_SQUAD_KEY, "instructions")
    squad_id = str(development_squad.get("id") or "")
    if not squad_id:
        raise IncidentError("managed development Squad has no stable ID")
    managed_members = as_list(
        cli.json(["squad", "member", "list", squad_id, "--output", "json"]),
        "members",
    )
    forbidden_assignees = {
        squad_id,
        *(str(item.get("member_id") or "") for item in managed_members),
        *(
            str(item.get("id") or "")
            for item in agents
            if marker_is_managed(str(item.get("instructions") or ""))
        ),
    }
    forbidden_assignees.discard("")
    known_identity_ids = {
        *(str(item.get("id") or "") for item in agents),
        *(str(item.get("id") or "") for item in squads),
        *(
            str(item.get("member_id") or item.get("id") or "")
            for item in as_list(
                cli.json(["workspace", "member", "list", "--output", "json"]),
                "members",
            )
        ),
    }
    user = cli.json(["user", "profile", "get", "--output", "json"])
    if isinstance(user, dict) and user.get("id"):
        known_identity_ids.add(str(user["id"]))
    for squad in squads:
        current_squad_id = str(squad.get("id") or "")
        if current_squad_id:
            known_identity_ids.update(
                str(item.get("member_id") or "")
                for item in as_list(
                    cli.json(
                        [
                            "squad",
                            "member",
                            "list",
                            current_squad_id,
                            "--output",
                            "json",
                        ]
                    ),
                    "members",
                )
            )
    known_identity_ids.discard("")
    return (
        projects,
        incident_project,
        development_squad,
        forbidden_assignees,
        known_identity_ids,
    )


def resolve_explicit_project(
    projects: list[dict[str, Any]], reference: str
) -> dict[str, Any]:
    matches = [
        item
        for item in projects
        if reference
        in {
            str(item.get("id") or ""),
            str(item.get("identifier") or ""),
            str(item.get("key") or ""),
            str(item.get("slug") or ""),
            str(item.get("title") or ""),
            str(item.get("name") or ""),
        }
    ]
    if len(matches) != 1:
        raise IncidentError(
            f"external project reference must resolve uniquely, found {len(matches)}"
        )
    return matches[0]


def require_safe_external_target(
    cli: CLI, project_reference: str, assignee_id: str | None
) -> dict[str, Any]:
    (
        projects,
        incident_project,
        _,
        forbidden_assignees,
        known_identity_ids,
    ) = external_fix_control_plane(cli)
    project = resolve_explicit_project(projects, project_reference)
    project_id = str(project.get("id") or "")
    description = str(project.get("description") or "")
    if not project_id:
        raise IncidentError("external project has no stable ID")
    if project_id == str(incident_project.get("id") or ""):
        raise IncidentError("external fix Requirement cannot use the Incident Project")
    if has_broken_marker(description):
        raise IncidentError("external project has an invalid managed marker")
    if marker_is_managed(description):
        raise IncidentError("external fix Requirement cannot use a managed workflow Project")
    project_owner_ids = {
        str(project.get(key) or "") for key in ["lead_id", "agent_id", "squad_id"]
    }
    project_owner_ids.discard("")
    if not project_owner_ids:
        raise IncidentError("external project ownership cannot be verified safely")
    if not project_owner_ids <= known_identity_ids:
        raise IncidentError("external project owner identity cannot be verified safely")
    if project_owner_ids & forbidden_assignees:
        raise IncidentError("external project is owned by the development Squad")
    if assignee_id and assignee_id in forbidden_assignees:
        raise IncidentError("external fix Requirement cannot be assigned to the development Squad")
    if assignee_id:
        if assignee_id not in known_identity_ids:
            raise IncidentError("external assignee identity cannot be verified safely")
    return project


def bind_workflow_issue(cli: CLI, args: argparse.Namespace) -> dict[str, Any]:
    issue = cli.json(["issue", "get", args.issue, "--output", "json"])
    if not isinstance(issue, dict):
        raise IncidentError(f"Issue is unreadable: {args.issue}")
    issue_id = issue_ref(issue) or args.issue
    if args.object_type not in WORKFLOW_OBJECT_TYPES:
        raise IncidentError("workflow object type is not part of protocol v4")
    root_id, _ = resolve_root_requirement(cli, issue)
    if args.root_requirement_id and args.root_requirement_id != root_id:
        raise IncidentError("root_requirement_id conflicts with the Issue parent chain")
    values = {
        "managed_by": MANAGED_BY,
        "workflow_id": WORKFLOW_ID,
        "workflow_version": skill_version(),
        "workflow_instance_id": cli.workspace_id,
        "workflow_object_type": args.object_type,
        "root_requirement_id": root_id,
        "created_by_role": args.created_by_role,
        "protocol_revision": PROTOCOL_REVISION,
        "top_protocol_revision": PROTOCOL_REVISION,
    }
    current = metadata_map(cli, issue_id)
    for key, value in values.items():
        existing = current.get(key)
        if existing not in {None, ""} and str(existing) != str(value):
            raise IncidentError(f"existing {key} conflicts with the requested binding")
    set_metadata_map(cli, issue_id, values)
    return {"issue_id": issue_id, **values}


def incident_fingerprint(dedupe_key: str) -> str:
    return hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:12]


def find_incidents(cli: CLI, project_id: str, dedupe_key: str) -> list[dict[str, Any]]:
    result = list_issues(
        cli,
        project_id,
        [
            "workflow_object_type=incident",
            f"incident_dedupe_key={dedupe_key}",
        ],
    )
    if result:
        return result
    fingerprint = incident_fingerprint(dedupe_key)
    fallback = list_issues(cli, project_id, [])
    return [item for item in fallback if f"[wf:{fingerprint}]" in str(item.get("title") or "")]


def list_issues(
    cli: CLI,
    project_id: str | None,
    metadata_filters: list[str],
    max_issues: int = 5000,
) -> list[dict[str, Any]]:
    result = []
    offset = 0
    page_size = 100
    while offset < max_issues:
        command = ["issue", "list"]
        if project_id:
            command.extend(["--project", project_id])
        for metadata_filter in metadata_filters:
            command.extend(["--metadata", metadata_filter])
        command.extend(
            [
                "--limit",
                str(min(page_size, max_issues - offset)),
                "--offset",
                str(offset),
                "--output",
                "json",
            ]
        )
        page = as_list(cli.json(command), "issues")
        result.extend(page)
        if len(page) < page_size:
            return result
        offset += len(page)
    raise IncidentError(f"Incident search exceeded max_issues={max_issues}")


def external_fix_marker(incident_id: str) -> str:
    fingerprint = external_fix_fingerprint(incident_id)
    return (
        "<!-- multica-workflow\n"
        f"managed_by={MANAGED_BY}\n"
        f"workflow_id={WORKFLOW_ID}\n"
        f"object_key={EXTERNAL_FIX_OBJECT_TYPE}\n"
        f"fix_execution_mode={EXTERNAL_FIX_MODE}\n"
        f"workflow_incident_id={incident_id}\n"
        f"fix_fingerprint={fingerprint}\n"
        "-->\n"
    )


def cli_marker_identity(incident_id: str) -> str:
    return canonical_json(
        {
            "workflow_id": WORKFLOW_ID,
            "workflow_object_type": EXTERNAL_FIX_OBJECT_TYPE,
            "fix_execution_mode": EXTERNAL_FIX_MODE,
            "workflow_incident_id": incident_id,
        }
    )


def external_fix_fingerprint(incident_id: str) -> str:
    return hashlib.sha256(cli_marker_identity(incident_id).encode("utf-8")).hexdigest()[:12]


def external_fix_candidate(
    cli: CLI, issue: dict[str, Any], incident_id: str
) -> bool:
    issue_id = issue_ref(issue)
    if not issue_id:
        return False
    metadata = metadata_map(cli, issue_id)
    if (
        metadata.get("workflow_object_type") == EXTERNAL_FIX_OBJECT_TYPE
        and metadata.get("fix_execution_mode") == EXTERNAL_FIX_MODE
        and str(metadata.get("workflow_incident_id") or "") == incident_id
    ):
        return True
    marker = parse_marker(str(issue.get("description") or ""))
    return bool(
        marker
        and marker.get("managed_by") == MANAGED_BY
        and marker.get("workflow_id") == WORKFLOW_ID
        and marker.get("object_key") == EXTERNAL_FIX_OBJECT_TYPE
        and marker.get("fix_execution_mode") == EXTERNAL_FIX_MODE
        and marker.get("workflow_incident_id") == incident_id
        and marker.get("fix_fingerprint") == external_fix_fingerprint(incident_id)
    )


def find_external_fix_candidates(cli: CLI, incident_id: str) -> list[dict[str, Any]]:
    candidates = []
    for item in list_issues(cli, None, []):
        item_id = issue_ref(item)
        detail = (
            cli.json(["issue", "get", item_id, "--output", "json"])
            if item_id
            else {}
        )
        enriched = {**item, **(detail if isinstance(detail, dict) else {})}
        if external_fix_candidate(cli, enriched, incident_id):
            candidates.append(enriched)
    return candidates


def parse_evidence_log(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
    return []


def report_incident(cli: CLI, args: argparse.Namespace) -> dict[str, Any]:
    source = cli.json(["issue", "get", args.source_issue, "--output", "json"])
    if not isinstance(source, dict):
        raise IncidentError(f"source Issue is unreadable: {args.source_issue}")
    source_id = issue_ref(source) or args.source_issue
    source_metadata = metadata_map(cli, source_id)
    if source_metadata.get("workflow_id") != WORKFLOW_ID:
        raise IncidentError("source Issue is not bound to the managed workflow")
    if source_metadata.get("protocol_revision") != PROTOCOL_REVISION:
        raise IncidentError("source Issue does not use protocol v4")
    if source_metadata.get("workflow_object_type") == "incident":
        raise IncidentError("an Incident cannot be the source of another Incident")
    root_id, _ = resolve_root_requirement(cli, source)
    entity = args.entity or root_id or source_id
    dedupe_key = sha256_value(
        {
            "workspace_id": cli.workspace_id,
            "workflow_id": WORKFLOW_ID,
            "protocol_revision": PROTOCOL_REVISION,
            "rule_id": args.rule_id,
            "entity": args.dedupe_key or entity,
        }
    )
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,63}", args.rule_id):
        raise IncidentError("rule_id must be a stable 2-64 character identifier")
    project, leader = resolve_control_plane(cli)
    existing = find_incidents(cli, str(project["id"]), dedupe_key)
    active = [item for item in existing if str(item.get("status") or "") in ACTIVE_STATUSES]
    if len(active) > 1:
        raise IncidentError("multiple active Incidents share the same dedupe key")
    blocked_by = (
        str(source_metadata.get("workflow_blocked_by_incident_id") or "")
        if args.block_source
        else ""
    )
    if blocked_by and (
        not active or issue_ref(active[0]) != blocked_by
    ):
        raise IncidentError(
            f"source Issue is already blocked by another Incident: {blocked_by}"
        )
    recurrence_of = ""
    if active:
        incident = active[0]
        incident_id = issue_ref(incident)
        action = "updated"
    else:
        closed = sorted(
            [item for item in existing if issue_ref(item)],
            key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
            reverse=True,
        )
        recurrence_of = issue_ref(closed[0]) if closed else ""
        safe_summary = " ".join(redacted_text(args.summary, 240).split())
        description = (
            f"# {safe_summary}\n\n"
            f"- Source Issue: {source_id}\n"
            f"- Root Requirement: {root_id}\n"
            f"- Rule: `{args.rule_id}`\n"
            f"- Severity: `{args.severity}`\n\n"
            f"## Expected\n\n{redacted_text(args.expected)}\n\n"
            f"## Actual\n\n{redacted_text(args.actual)}\n\n"
            f"## Evidence\n\n{redacted_text(args.evidence or 'See the linked source Issue.')}\n"
        )
        created = cli.json(
            [
                "issue",
                "create",
                "--title",
                f"[Workflow Incident][{args.severity}][wf:{incident_fingerprint(dedupe_key)}] {args.rule_id}: {safe_summary}",
                "--description-stdin",
                "--project",
                str(project["id"]),
                "--assignee-id",
                str(leader["id"]),
                "--status",
                "todo",
                "--priority",
                args.severity,
                "--output",
                "json",
            ],
            input_text=description,
        )
        incident_id = issue_ref(created)
        if not incident_id:
            raise IncidentError("created Incident did not return an Issue ID")
        action = "created"

    metadata = metadata_map(cli, incident_id)
    current_status = str(metadata.get("incident_status") or "open")
    if current_status not in {"open", "in_fix"}:
        current_status = "open"
    current_severity = str(metadata.get("incident_severity") or "low")
    effective_severity = max(
        [current_severity, args.severity], key=lambda item: SEVERITY_RANK.get(item, -1)
    )
    if action == "updated" and effective_severity != current_severity:
        cli.json(
            [
                "issue",
                "update",
                incident_id,
                "--priority",
                effective_severity,
                "--output",
                "json",
            ]
        )
    affected_sources = [
        str(item)
        for item in parse_json_list(metadata.get("affected_source_issue_ids"))
        if item
    ]
    if source_id not in affected_sources:
        affected_sources.append(source_id)
    evidence_log = parse_evidence_log(metadata.get("incident_evidence_log"))
    evidence_payload = {
        "source_issue_id": source_id,
        "source_requirement_id": str(
            metadata.get("source_requirement_id") or root_id
        ),
        "summary": redacted_text(args.summary, 500),
        "expected": redacted_text(args.expected, 1000),
        "actual": redacted_text(args.actual, 1000),
        "evidence": redacted_text(args.evidence or "", 1000),
    }
    evidence_item = {
        **evidence_payload,
        "seen_at": utc_now(),
        "fingerprint": sha256_value(evidence_payload),
    }
    if evidence_item["fingerprint"] not in {
        str(item.get("fingerprint") or "") for item in evidence_log
    }:
        evidence_log.append(evidence_item)
    evidence_log = evidence_log[-20:]
    values = {
        "managed_by": MANAGED_BY,
        "workflow_object_type": "incident",
        "workflow_id": WORKFLOW_ID,
        "workflow_version": skill_version(),
        "workflow_instance_id": cli.workspace_id,
        "protocol_revision": PROTOCOL_REVISION,
        "incident_dedupe_key": dedupe_key,
        "incident_rule_id": args.rule_id,
        "incident_status": current_status,
        "incident_severity": effective_severity,
        "source_issue_id": str(metadata.get("source_issue_id") or source_id),
        "source_requirement_id": root_id,
        "affected_source_issue_ids": json.dumps(
            affected_sources, ensure_ascii=False, sort_keys=True
        ),
        "reporter_agent_id": args.reporter_agent_id
        or os.environ.get("MULTICA_AGENT_ID", "human_host"),
        "reporter_role": args.reporter_role or "unknown",
        "incident_last_seen_at": utc_now(),
        "incident_evidence_log": json.dumps(
            evidence_log, ensure_ascii=False, sort_keys=True
        ),
        "waiting_on": str(metadata.get("waiting_on") or "development_leader")
        if current_status == "in_fix"
        else "development_leader",
    }
    if recurrence_of:
        values["recurrence_of"] = recurrence_of
    set_metadata_map(cli, incident_id, values)
    set_metadata_map(
        cli,
        source_id,
        {
            "workflow_incident_id": incident_id,
            "workflow_incident_last_evidence_at": utc_now(),
        },
    )
    add_comment(
        cli,
        source_id,
        f"WORKFLOW INCIDENT {action.upper()}: {incident_id} (`{args.rule_id}`, {args.severity})",
    )
    if args.block_source:
        previous_status = str(source.get("status") or "")
        if blocked_by == incident_id:
            previous_status = str(
                source_metadata.get("workflow_blocked_previous_status")
                or previous_status
            )
        cli.json(["issue", "update", source_id, "--status", "blocked", "--output", "json"])
        set_metadata_map(
            cli,
            source_id,
            {
                "workflow_blocked_by_incident_id": incident_id,
                "workflow_blocked_previous_status": previous_status,
                "waiting_on": "workflow_fix",
                "blocked_reason": f"workflow Incident {incident_id}",
            },
        )
        blocked_sources = [
            str(item)
            for item in parse_json_list(metadata.get("blocked_source_issue_ids"))
            if item
        ]
        if source_id not in blocked_sources:
            blocked_sources.append(source_id)
        set_metadata(
            cli,
            incident_id,
            "blocked_source_issue_ids",
            json.dumps(blocked_sources, ensure_ascii=False, sort_keys=True),
        )
    return {"action": action, "incident_id": incident_id, "dedupe_key": dedupe_key}


def require_incident(cli: CLI, incident_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    issue = cli.json(["issue", "get", incident_id, "--output", "json"])
    if not isinstance(issue, dict):
        raise IncidentError(f"Incident is unreadable: {incident_id}")
    metadata = metadata_map(cli, issue_ref(issue) or incident_id)
    if (
        metadata.get("workflow_object_type") != "incident"
        or metadata.get("workflow_id") != WORKFLOW_ID
        or metadata.get("protocol_revision") != PROTOCOL_REVISION
    ):
        raise IncidentError("Issue is not a managed workflow Incident")
    return issue, metadata


def parse_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def require_non_conflicting_metadata(
    metadata: dict[str, Any], values: dict[str, Any]
) -> None:
    for key, value in values.items():
        existing = metadata.get(key)
        if existing not in {None, ""} and str(existing) != str(value):
            raise IncidentError(f"existing {key} conflicts with the requested fix binding")


def require_unique_incident_owner(
    cli: CLI, requirement_id: str, incident_id: str
) -> None:
    owners = {
        issue_ref(item)
        for item in list_issues(
            cli,
            None,
            [
                "workflow_object_type=incident",
                f"fix_requirement_id={requirement_id}",
            ],
        )
        if issue_ref(item)
    }
    if any(owner != incident_id for owner in owners):
        raise IncidentError("fix Requirement is already owned by another Incident")
    if len(owners) > 1:
        raise IncidentError("multiple Incidents claim the same fix Requirement")


def validate_external_fix_requirement(
    cli: CLI,
    requirement: dict[str, Any],
    incident_id: str,
    expected_project_id: str | None = None,
    expected_assignee_id: str | None = None,
    require_reverse_binding: bool = False,
) -> tuple[str, dict[str, Any]]:
    requirement_id = issue_ref(requirement)
    if not requirement_id:
        raise IncidentError("external fix Requirement has no stable ID")
    require_unique_incident_owner(cli, requirement_id, incident_id)
    if requirement.get("parent_issue_id") not in {None, ""}:
        raise IncidentError("external fix Requirement cannot belong to a development Issue tree")
    project_id = str(requirement.get("project_id") or "")
    assignee_id = str(requirement.get("assignee_id") or "")
    if not project_id:
        raise IncidentError("external fix Requirement has no Project")
    project = require_safe_external_target(cli, project_id, assignee_id or None)
    if expected_project_id and project_id != expected_project_id:
        raise IncidentError("existing external fix Requirement uses a different Project")
    if expected_assignee_id is not None and assignee_id != expected_assignee_id:
        raise IncidentError("existing external fix Requirement uses a different assignee")
    metadata = metadata_map(cli, requirement_id)
    if has_broken_marker(str(requirement.get("description") or "")):
        raise IncidentError("external fix Requirement has an invalid managed marker")
    marker = parse_marker(str(requirement.get("description") or ""))
    marker_proves_partial_create = bool(
        marker
        and marker.get("managed_by") == MANAGED_BY
        and marker.get("workflow_id") == WORKFLOW_ID
        and marker.get("object_key") == EXTERNAL_FIX_OBJECT_TYPE
        and marker.get("fix_execution_mode") == EXTERNAL_FIX_MODE
        and marker.get("workflow_incident_id") == incident_id
        and marker.get("fix_fingerprint") == external_fix_fingerprint(incident_id)
    )
    if (
        marker_is_managed(str(requirement.get("description") or ""))
        and marker
        and marker.get("object_key") == EXTERNAL_FIX_OBJECT_TYPE
        and not marker_proves_partial_create
    ):
        raise IncidentError("external fix Requirement marker conflicts with this Incident")
    if not marker_proves_partial_create:
        if metadata.get("workflow_object_type") != EXTERNAL_FIX_OBJECT_TYPE:
            raise IncidentError("external fix Requirement has the wrong workflow object type")
        if metadata.get("fix_execution_mode") != EXTERNAL_FIX_MODE:
            raise IncidentError("external fix Requirement must declare external execution")
    values = {
        "managed_by": MANAGED_BY,
        "workflow_id": WORKFLOW_ID,
        "workflow_instance_id": cli.workspace_id,
        "workflow_object_type": EXTERNAL_FIX_OBJECT_TYPE,
        "fix_execution_mode": EXTERNAL_FIX_MODE,
        "workflow_incident_id": incident_id,
    }
    require_non_conflicting_metadata(metadata, values)
    if (
        require_reverse_binding
        and str(metadata.get("workflow_incident_id") or "") != incident_id
    ):
        raise IncidentError("external fix Requirement is not bound back to this Incident")
    conflicts = sorted(
        key
        for key in DEVELOPMENT_TREE_METADATA_KEYS
        if metadata.get(key) not in {None, ""}
    )
    if conflicts:
        raise IncidentError(
            "external fix Requirement has conflicting development-delivery metadata: "
            + ", ".join(conflicts)
        )
    return requirement_id, {**values, "workflow_version": skill_version()}


def validate_legacy_fix_requirement(
    cli: CLI,
    requirement: dict[str, Any],
    incident_id: str,
    require_reverse_binding: bool = False,
) -> tuple[str, dict[str, Any]]:
    requirement_id = issue_ref(requirement)
    if not requirement_id:
        raise IncidentError("legacy fix Requirement has no stable ID")
    require_unique_incident_owner(cli, requirement_id, incident_id)
    metadata = metadata_map(cli, requirement_id)
    marker = parse_marker(str(requirement.get("description") or ""))
    if (
        marker_is_managed(str(requirement.get("description") or ""))
        and marker
        and marker.get("object_key") == EXTERNAL_FIX_OBJECT_TYPE
    ):
        raise IncidentError("external fix Requirement cannot use the legacy flow")
    if (
        metadata.get("workflow_id") != WORKFLOW_ID
        or metadata.get("workflow_object_type") != "requirement"
        or metadata.get("protocol_revision") != PROTOCOL_REVISION
    ):
        raise IncidentError("fix Requirement is not a managed protocol v4 Requirement")
    reverse = str(metadata.get("workflow_incident_id") or "")
    if reverse and reverse != incident_id:
        raise IncidentError("fix Requirement is already linked to another Incident")
    if require_reverse_binding and reverse != incident_id:
        raise IncidentError("legacy fix Requirement is not bound back to this Incident")
    return requirement_id, metadata


def bind_external_fix(
    cli: CLI,
    incident_id: str,
    incident_metadata: dict[str, Any],
    requirement: dict[str, Any],
    expected_project_id: str | None = None,
    expected_assignee_id: str | None = None,
) -> dict[str, Any]:
    requirement_id, requirement_values = validate_external_fix_requirement(
        cli,
        requirement,
        incident_id,
        expected_project_id=expected_project_id,
        expected_assignee_id=expected_assignee_id,
    )
    existing_fix = str(incident_metadata.get("fix_requirement_id") or "")
    if existing_fix and existing_fix != requirement_id:
        raise IncidentError("Incident is already linked to a different fix Requirement")
    incident_mode = str(incident_metadata.get("fix_execution_mode") or "")
    if incident_mode not in {"", EXTERNAL_FIX_MODE}:
        raise IncidentError("Incident fix execution mode conflicts with external execution")
    set_metadata_map(cli, requirement_id, requirement_values)
    link_time = str(incident_metadata.get("fix_linked_at") or utc_now())
    set_metadata_map(
        cli,
        incident_id,
        {
            "fix_requirement_id": requirement_id,
            "incident_status": "in_fix",
            "waiting_on": "external_fix_owner",
            "fix_execution_mode": EXTERNAL_FIX_MODE,
            "fix_linked_at": link_time,
        },
    )
    cli.json(["issue", "update", incident_id, "--status", "in_progress", "--output", "json"])
    if not existing_fix:
        add_comment(cli, incident_id, f"External fix Requirement linked: {requirement_id}.")
    return {
        "incident_id": incident_id,
        "fix_requirement_id": requirement_id,
        "fix_execution_mode": EXTERNAL_FIX_MODE,
    }


def external_fix_description(
    incident_id: str, incident: dict[str, Any], metadata: dict[str, Any]
) -> str:
    evidence_log = parse_evidence_log(metadata.get("incident_evidence_log"))
    latest = evidence_log[-1] if evidence_log else {}
    summary = latest.get("summary") or incident.get("title") or f"Workflow Incident {incident_id}"
    expected = latest.get("expected") or "Restore the documented workflow invariant."
    actual = latest.get("actual") or "See the linked Incident evidence."
    source_id = str(latest.get("source_issue_id") or metadata.get("source_issue_id") or "")
    evidence = latest.get("evidence") or "See the linked Incident evidence log."
    return (
        external_fix_marker(incident_id)
        + f"# External fix for {redacted_text(summary, 240)}\n\n"
        + f"- Workflow Incident: {incident_id}\n"
        + f"- Source Issue: {redacted_text(source_id, 240)}\n"
        + f"- Execution mode: `{EXTERNAL_FIX_MODE}`\n\n"
        + f"## Expected\n\n{redacted_text(expected, 1500)}\n\n"
        + f"## Actual\n\n{redacted_text(actual, 1500)}\n\n"
        + f"## Redacted source evidence\n\n{redacted_text(evidence, 1500)}\n\n"
        + "## Acceptance and deployment verification\n\n"
        + "- Implement the correction through the external owner or external process.\n"
        + "- Record a typed immutable fix reference.\n"
        + "- Deploy the correction and record a typed deployment verification reference.\n"
        + "- Verify the Incident's expected behavior before requesting closure.\n"
    )


def create_fix_requirement(cli: CLI, args: argparse.Namespace) -> dict[str, Any]:
    incident, incident_metadata = require_incident(cli, args.incident)
    incident_id = issue_ref(incident) or args.incident
    if incident_metadata.get("incident_status") == "closed":
        raise IncidentError("a closed Incident cannot create a fix Requirement")
    project = require_safe_external_target(cli, args.project, args.assignee_id)
    project_id = str(project.get("id") or "")
    existing_fix = str(incident_metadata.get("fix_requirement_id") or "")
    if existing_fix:
        candidates = find_external_fix_candidates(cli, incident_id)
        unexpected = [item for item in candidates if issue_ref(item) != existing_fix]
        if unexpected:
            raise IncidentError(
                "another external fix Requirement also matches this Incident"
            )
        existing = cli.json(["issue", "get", existing_fix, "--output", "json"])
        if not isinstance(existing, dict):
            raise IncidentError("existing fix Requirement is unreadable")
        existing_metadata = metadata_map(cli, existing_fix)
        if existing_metadata.get("workflow_object_type") != EXTERNAL_FIX_OBJECT_TYPE:
            raise IncidentError("Incident already uses a legacy fix Requirement")
        result = bind_external_fix(
            cli,
            incident_id,
            incident_metadata,
            existing,
            expected_project_id=project_id,
            expected_assignee_id=args.assignee_id,
        )
        return {**result, "action": "reused"}
    candidates = find_external_fix_candidates(cli, incident_id)
    if len(candidates) > 1:
        raise IncidentError("multiple external fix Requirements match this Incident")
    if not candidates:
        safe_summary = " ".join(
            redacted_text(
                parse_evidence_log(incident_metadata.get("incident_evidence_log"))[-1].get("summary")
                if parse_evidence_log(incident_metadata.get("incident_evidence_log"))
                else incident.get("title") or incident_id,
                180,
            ).split()
        )
        command = [
            "issue",
            "create",
            "--title",
            f"[Incident Fix][wf:{external_fix_fingerprint(incident_id)}] {safe_summary}",
            "--description-stdin",
            "--project",
            project_id,
            "--status",
            "backlog",
        ]
        if args.assignee_id:
            command.extend(["--assignee-id", args.assignee_id])
        command.extend(["--output", "json"])
        try:
            cli.json(
                command,
                input_text=external_fix_description(incident_id, incident, incident_metadata),
            )
        except IncidentError:
            candidates = find_external_fix_candidates(cli, incident_id)
            if len(candidates) != 1:
                raise
        else:
            candidates = find_external_fix_candidates(cli, incident_id)
    if len(candidates) != 1:
        raise IncidentError("external fix Requirement creation could not be recovered uniquely")
    result = bind_external_fix(
        cli,
        incident_id,
        incident_metadata,
        candidates[0],
        expected_project_id=project_id,
        expected_assignee_id=args.assignee_id,
    )
    return {**result, "action": "created_or_recovered"}


def link_fix(cli: CLI, args: argparse.Namespace) -> dict[str, Any]:
    incident, incident_metadata = require_incident(cli, args.incident)
    incident_id = issue_ref(incident) or args.incident
    if incident_metadata.get("incident_status") == "closed":
        raise IncidentError("a closed Incident cannot be linked to a new fix")
    requirement = cli.json(["issue", "get", args.requirement, "--output", "json"])
    if not isinstance(requirement, dict):
        raise IncidentError(f"Requirement is unreadable: {args.requirement}")
    requirement_id = issue_ref(requirement) or args.requirement
    requirement_metadata = metadata_map(cli, requirement_id)
    requirement_marker = parse_marker(str(requirement.get("description") or ""))
    if (
        requirement_metadata.get("workflow_object_type") == EXTERNAL_FIX_OBJECT_TYPE
        or (
            requirement_marker
            and requirement_marker.get("managed_by") == MANAGED_BY
            and requirement_marker.get("workflow_id") == WORKFLOW_ID
            and requirement_marker.get("object_key") == EXTERNAL_FIX_OBJECT_TYPE
        )
    ):
        return bind_external_fix(
            cli, incident_id, incident_metadata, requirement
        )
    requirement_id, requirement_metadata = validate_legacy_fix_requirement(
        cli, requirement, incident_id
    )
    existing_fix = str(incident_metadata.get("fix_requirement_id") or "")
    if existing_fix and existing_fix != requirement_id:
        raise IncidentError("Incident is already linked to a different fix Requirement")
    incident_mode = str(incident_metadata.get("fix_execution_mode") or "")
    if incident_mode and incident_mode != "legacy_ordinary_development":
        raise IncidentError("Incident fix execution mode conflicts with legacy execution")
    set_metadata(cli, requirement_id, "workflow_incident_id", incident_id)
    set_metadata_map(
        cli,
        incident_id,
        {
            "fix_requirement_id": requirement_id,
            "incident_status": "in_fix",
            "waiting_on": "ordinary_development_workflow",
            "fix_linked_at": str(incident_metadata.get("fix_linked_at") or utc_now()),
        },
    )
    cli.json(["issue", "update", incident_id, "--status", "in_progress", "--output", "json"])
    if not existing_fix:
        add_comment(cli, incident_id, f"Fix linked to ordinary Requirement {requirement_id}.")
    return {
        "incident_id": incident_id,
        "fix_requirement_id": requirement_id,
        "fix_execution_mode": "legacy_ordinary_development",
    }


def require_reference_type(name: str, value: str | None) -> str:
    if not value or not REFERENCE_TYPE_RE.fullmatch(value):
        raise IncidentError(f"{name} must be a stable 2-64 character type")
    return value


def require_identity_reference(
    name: str, reference_type: str, value: str | None
) -> str:
    if not value or not value.strip():
        raise IncidentError(f"passed verification requires {name}")
    clean = value.strip()
    if redacted_text(clean, 2000) != clean:
        raise IncidentError(f"{name} must not contain secrets or exceed 2000 characters")
    if not REFERENCE_VALUE_RE.fullmatch(clean):
        raise IncidentError(f"{name} must be a single immutable reference token")
    if MUTABLE_REFERENCE_RE.search(clean):
        raise IncidentError(f"{name} must not use a mutable alias")
    if reference_type == "git_commit" and not re.fullmatch(r"[0-9a-fA-F]{40}", clean):
        raise IncidentError(f"{name} with type git_commit requires a full 40-character commit")
    return clean


def close_incident(cli: CLI, args: argparse.Namespace) -> dict[str, Any]:
    incident, metadata = require_incident(cli, args.incident)
    incident_id = issue_ref(incident) or args.incident
    requested_closure_mode = getattr(args, "closure_mode", STANDARD_CLOSURE_MODE)
    if metadata.get("incident_status") == "closed":
        recorded_closure_mode = str(
            metadata.get("incident_closure_mode") or STANDARD_CLOSURE_MODE
        )
        if requested_closure_mode != recorded_closure_mode:
            raise IncidentError("closed Incident uses a different closure mode")
        return {"incident_id": incident_id, "closed": True, "result": "already_closed"}
    if not metadata.get("fix_requirement_id"):
        raise IncidentError("Incident must link a fix Requirement before verification")
    fix_requirement_id = str(metadata["fix_requirement_id"])
    fix_requirement = cli.json(
        ["issue", "get", fix_requirement_id, "--output", "json"]
    )
    if not isinstance(fix_requirement, dict):
        raise IncidentError("fix Requirement is unreadable")
    fix_metadata = metadata_map(cli, fix_requirement_id)
    closure_mode = requested_closure_mode
    if closure_mode not in {
        STANDARD_CLOSURE_MODE,
        INDEPENDENT_REMEDIATION_CLOSURE_MODE,
    }:
        raise IncidentError("unsupported Incident closure mode")
    if fix_metadata.get("workflow_object_type") == EXTERNAL_FIX_OBJECT_TYPE:
        validate_external_fix_requirement(
            cli, fix_requirement, incident_id, require_reverse_binding=True
        )
        mode = EXTERNAL_FIX_MODE
        waiting_on = "external_fix_owner"
        if str(metadata.get("fix_execution_mode") or "") not in {"", EXTERNAL_FIX_MODE}:
            raise IncidentError("Incident and fix Requirement execution modes conflict")
        if closure_mode != STANDARD_CLOSURE_MODE:
            raise IncidentError(
                "independent remediation closure applies only to a legacy ordinary Requirement"
            )
    else:
        validate_legacy_fix_requirement(
            cli, fix_requirement, incident_id, require_reverse_binding=True
        )
        mode = "legacy_ordinary_development"
        waiting_on = "ordinary_development_workflow"
        if str(metadata.get("fix_execution_mode") or "") not in {
            "",
            "legacy_ordinary_development",
        }:
            raise IncidentError("Incident and fix Requirement execution modes conflict")
    fix_status = str(fix_requirement.get("status") or "")
    independent_remediation = (
        mode == "legacy_ordinary_development"
        and closure_mode == INDEPENDENT_REMEDIATION_CLOSURE_MODE
    )
    if independent_remediation:
        if args.result != "passed":
            raise IncidentError(
                "independent remediation closure requires a passed verification result"
            )
        if metadata.get("incident_status") != "in_fix":
            raise IncidentError(
                "independent remediation closure requires an Incident in in_fix"
            )
        if fix_status != "cancelled":
            raise IncidentError(
                "independent remediation closure requires a cancelled legacy fix Requirement"
            )
    elif fix_status != "done":
        raise IncidentError("fix Requirement must be done before Incident verification")
    evidence = redacted_text(args.evidence, 2000)
    if not evidence.strip():
        raise IncidentError("verification evidence must be non-empty after redaction")
    if args.result == "failed":
        set_metadata_map(
            cli,
            incident_id,
            {
                "incident_status": "in_fix",
                "waiting_on": waiting_on,
                "last_verification_result": "failed",
                "last_verification_evidence": evidence,
                "last_verified_at": utc_now(),
            },
        )
        cli.json(["issue", "update", incident_id, "--status", "in_progress", "--output", "json"])
        add_comment(cli, incident_id, f"Verification failed.\n\n{evidence}")
        return {"incident_id": incident_id, "closed": False, "result": "failed"}

    closure_values: dict[str, Any]
    if mode == EXTERNAL_FIX_MODE or independent_remediation:
        fix_reference_type = require_reference_type(
            "fix reference type", args.fix_reference_type
        )
        fix_reference = require_identity_reference(
            "immutable fix reference", fix_reference_type, args.fix_reference
        )
        verification_type = require_reference_type(
            "deployment verification reference type",
            args.deployment_verification_reference_type,
        )
        verification_reference = require_identity_reference(
            "deployment verification reference",
            verification_type,
            args.deployment_verification_reference,
        )
        closure_values = {
            "immutable_fix_reference_type": fix_reference_type,
            "immutable_fix_reference": fix_reference,
            "deployment_verification_reference_type": verification_type,
            "deployment_verification_reference": verification_reference,
        }
        if independent_remediation:
            closure_values["incident_closure_mode"] = (
                INDEPENDENT_REMEDIATION_CLOSURE_MODE
            )
    else:
        if not args.source_commit or not re.fullmatch(
            r"[0-9a-fA-F]{40}", args.source_commit
        ):
            raise IncidentError(
                "passed verification requires a full 40-character source commit"
            )
        if not args.deployment_plan_digest or not re.fullmatch(
            r"[0-9a-fA-F]{64}", args.deployment_plan_digest
        ):
            raise IncidentError("passed verification requires a full deployment Plan digest")
        closure_values = {
            "fixed_source_commit": args.source_commit,
            "deployment_plan_digest": args.deployment_plan_digest,
        }
    blocked_sources = [
        str(item)
        for item in parse_json_list(metadata.get("blocked_source_issue_ids"))
        if item
    ]
    primary_source = str(metadata.get("source_issue_id") or "")
    if primary_source and primary_source not in blocked_sources:
        blocked_sources.append(primary_source)
    restored_count = 0
    for source_id in blocked_sources:
        source = cli.json(["issue", "get", source_id, "--output", "json"])
        source_metadata = metadata_map(cli, source_id)
        if source_metadata.get("workflow_blocked_by_incident_id") == incident_id:
            previous = str(source_metadata.get("workflow_blocked_previous_status") or "todo")
            cli.json(["issue", "update", source_id, "--status", previous, "--output", "json"])
            set_metadata_map(
                cli,
                source_id,
                {
                    "workflow_blocked_by_incident_id": "",
                    "workflow_blocked_previous_status": "",
                    "waiting_on": "",
                    "blocked_reason": "",
                },
            )
            restored_count += 1
    values = {
        "incident_status": "closed",
        "waiting_on": "",
        "last_verification_result": "passed",
        "last_verification_evidence": evidence,
        "verified_at": utc_now(),
        "verified_by": os.environ.get("MULTICA_AGENT_ID", "human_host"),
        **closure_values,
    }
    if mode == EXTERNAL_FIX_MODE:
        values["fix_execution_mode"] = mode
    set_metadata_map(cli, incident_id, values)
    cli.json(["issue", "update", incident_id, "--status", "done", "--output", "json"])
    add_comment(cli, incident_id, f"Verification passed.\n\n{evidence}")
    return {
        "incident_id": incident_id,
        "closed": True,
        "result": "passed",
        "sources_restored": restored_count,
    }


def build_cli(args: argparse.Namespace) -> CLI:
    workspace = args.workspace or os.environ.get("MULTICA_WORKSPACE_ID")
    if not workspace:
        raise IncidentError("workspace is required; pass --workspace or set MULTICA_WORKSPACE_ID")
    profile = args.profile or os.environ.get("MULTICA_REQUIREMENT_PROFILE")
    return CLI(discover_multica(args.multica_bin), profile, workspace)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--multica-bin")
    root.add_argument("--profile")
    root.add_argument("--workspace")
    sub = root.add_subparsers(dest="command", required=True)

    bind = sub.add_parser("bind-workflow-issue")
    bind.add_argument("--issue", required=True)
    bind.add_argument("--object-type", required=True)
    bind.add_argument("--root-requirement-id")
    bind.add_argument("--created-by-role", required=True)
    bind.add_argument("--output", choices=["json"], default="json")

    report = sub.add_parser("report")
    report.add_argument("--source-issue", required=True)
    report.add_argument("--rule-id", default="WF-SELF-REPORT-001")
    report.add_argument("--severity", choices=sorted(SEVERITIES), default="medium")
    report.add_argument("--summary", required=True)
    report.add_argument("--expected", required=True)
    report.add_argument("--actual", required=True)
    report.add_argument("--evidence")
    report.add_argument("--entity")
    report.add_argument("--dedupe-key")
    report.add_argument("--reporter-agent-id")
    report.add_argument("--reporter-role")
    report.add_argument("--block-source", action="store_true")
    report.add_argument("--output", choices=["json"], default="json")

    create_fix = sub.add_parser("create-fix-requirement")
    create_fix.add_argument("--incident", required=True)
    create_fix.add_argument("--project", required=True)
    create_fix.add_argument("--assignee-id")
    create_fix.add_argument("--output", choices=["json"], default="json")

    link = sub.add_parser("link-fix")
    link.add_argument("--incident", required=True)
    link.add_argument("--requirement", required=True)
    link.add_argument("--output", choices=["json"], default="json")

    close = sub.add_parser("close")
    close.add_argument("--incident", required=True)
    close.add_argument("--result", choices=["passed", "failed"], required=True)
    close.add_argument("--evidence", required=True)
    close.add_argument("--source-commit")
    close.add_argument("--deployment-plan-digest")
    close.add_argument("--fix-reference-type")
    close.add_argument("--fix-reference")
    close.add_argument("--deployment-verification-reference-type")
    close.add_argument("--deployment-verification-reference")
    close.add_argument(
        "--closure-mode",
        choices=(STANDARD_CLOSURE_MODE, INDEPENDENT_REMEDIATION_CLOSURE_MODE),
        default=STANDARD_CLOSURE_MODE,
    )
    close.add_argument("--output", choices=["json"], default="json")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        cli = build_cli(args)
        if args.command == "bind-workflow-issue":
            result = bind_workflow_issue(cli, args)
        elif args.command == "report":
            result = report_incident(cli, args)
        elif args.command == "create-fix-requirement":
            result = create_fix_requirement(cli, args)
        elif args.command == "link-fix":
            result = link_fix(cli, args)
        else:
            result = close_incident(cli, args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except IncidentError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
