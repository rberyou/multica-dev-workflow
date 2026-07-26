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
    cli: CLI, project_id: str, metadata_filters: list[str], max_issues: int = 5000
) -> list[dict[str, Any]]:
    result = []
    offset = 0
    page_size = 100
    while offset < max_issues:
        command = ["issue", "list", "--project", project_id]
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
    if (
        requirement_metadata.get("workflow_id") != WORKFLOW_ID
        or requirement_metadata.get("workflow_object_type") != "requirement"
        or requirement_metadata.get("protocol_revision") != PROTOCOL_REVISION
    ):
        raise IncidentError("fix Requirement is not a managed protocol v4 Requirement")
    existing_fix = str(incident_metadata.get("fix_requirement_id") or "")
    if existing_fix and existing_fix != requirement_id:
        raise IncidentError("Incident is already linked to a different fix Requirement")
    set_metadata_map(
        cli,
        incident_id,
        {
            "fix_requirement_id": requirement_id,
            "incident_status": "in_fix",
            "waiting_on": "ordinary_development_workflow",
            "fix_linked_at": utc_now(),
        },
    )
    cli.json(["issue", "update", incident_id, "--status", "in_progress", "--output", "json"])
    add_comment(cli, incident_id, f"Fix linked to ordinary Requirement {requirement_id}.")
    set_metadata(cli, requirement_id, "workflow_incident_id", incident_id)
    return {"incident_id": incident_id, "fix_requirement_id": requirement_id}


def close_incident(cli: CLI, args: argparse.Namespace) -> dict[str, Any]:
    incident, metadata = require_incident(cli, args.incident)
    incident_id = issue_ref(incident) or args.incident
    if metadata.get("incident_status") == "closed":
        return {"incident_id": incident_id, "closed": True, "result": "already_closed"}
    if not metadata.get("fix_requirement_id"):
        raise IncidentError("Incident must link an ordinary fix Requirement before verification")
    fix_requirement_id = str(metadata["fix_requirement_id"])
    fix_requirement = cli.json(
        ["issue", "get", fix_requirement_id, "--output", "json"]
    )
    if not isinstance(fix_requirement, dict):
        raise IncidentError("fix Requirement is unreadable")
    fix_metadata = metadata_map(cli, fix_requirement_id)
    if (
        fix_metadata.get("workflow_id") != WORKFLOW_ID
        or fix_metadata.get("workflow_object_type") != "requirement"
        or fix_metadata.get("protocol_revision") != PROTOCOL_REVISION
    ):
        raise IncidentError("fix Requirement no longer satisfies protocol v4")
    if str(fix_requirement.get("status") or "") != "done":
        raise IncidentError("fix Requirement must be done before Incident verification")
    evidence = redacted_text(args.evidence, 2000)
    if args.result == "failed":
        set_metadata_map(
            cli,
            incident_id,
            {
                "incident_status": "in_fix",
                "waiting_on": "ordinary_development_workflow",
                "last_verification_result": "failed",
                "last_verification_evidence": evidence,
                "last_verified_at": utc_now(),
            },
        )
        cli.json(["issue", "update", incident_id, "--status", "in_progress", "--output", "json"])
        add_comment(cli, incident_id, f"Verification failed.\n\n{evidence}")
        return {"incident_id": incident_id, "closed": False, "result": "failed"}

    if not args.source_commit or not re.fullmatch(r"[0-9a-fA-F]{40}", args.source_commit):
        raise IncidentError("passed verification requires a full 40-character source commit")
    if not args.deployment_plan_digest or not re.fullmatch(
        r"[0-9a-fA-F]{64}", args.deployment_plan_digest
    ):
        raise IncidentError("passed verification requires a full deployment Plan digest")
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
        "fixed_source_commit": args.source_commit,
        "deployment_plan_digest": args.deployment_plan_digest,
    }
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
