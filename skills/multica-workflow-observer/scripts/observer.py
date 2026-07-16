#!/usr/bin/env python3
"""Portable workflow incident reporting and deterministic Multica audits."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
import platform
from pathlib import Path
import re
import shutil
import subprocess
import sys
from typing import Any


MANAGED_BY = "multica-dev-workflow"
WORKFLOW_ID = "development-delivery"
OPERATIONS_PROJECT_KEY = "project.workflow-operations"
OBSERVER_AGENT_KEY = "agent.workflow-observer"
SQUAD_KEY = "squad.development-delivery"
APPROVER_ROLE = "人工审批人"
ACTIVE_STATUSES = {"backlog", "todo", "in_progress", "in_review", "blocked"}
SECRET_KEY_RE = re.compile(r"token|secret|password|cookie|authorization|private[_-]?key|custom_env", re.I)
BEARER_RE = re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+\-/]+=*")
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----", re.S)
SENSITIVE_HEADER_RE = re.compile(
    r"(?im)\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key)\s*:\s*[^\r\n]+"
)
SENSITIVE_ENV_RE = re.compile(
    r"(?im)\b([A-Z][A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|COOKIE|API_KEY|PRIVATE_KEY)[A-Z0-9_]*)\s*=\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"(?im)\b(token|secret|password|cookie|authorization|api[_-]?key|private[_-]?key)\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)"
)
SEVERITY_RANK = {"low": 0, "medium": 1, "high": 2, "urgent": 3}
APPROVAL_LINE_RE = re.compile(
    r"^APPROVE (?:PLAN v\S+|REQUIREMENT v\S+|WORKFLOW (?:PLAN|RELEASE|CANARY) \S+)$"
)
EXPECTED_AGENT_KEYS = {
    "agent.leader",
    "agent.planner",
    "agent.plan-reviewer",
    "agent.integrator",
    "agent.developer-a",
    "agent.developer-b",
    "agent.code-reviewer",
    "agent.workflow-observer",
    "agent.workflow-maintainer",
    "agent.workflow-maintenance-reviewer",
}
EXPECTED_SKILL_NAMES = {
    "multica-requirement-intake",
    "multica-workflow-observer",
    "multica-workflow-maintainer",
}


class ObserverError(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def as_list(value: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return [item for item in value[key] if isinstance(item, dict)]
    return []


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


def strip_marker(value: str | None) -> str:
    if not value or not value.startswith("<!-- multica-workflow\n"):
        return str(value or "")
    end = value.find("-->\n")
    return value[end + 4 :] if end >= 0 else str(value)


def sha256_value(value: Any) -> str:
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def normalize_permission(agent: dict[str, Any], workspace_id: str) -> str:
    explicit = str(agent.get("permission_mode") or "")
    if explicit and explicit != "public_to":
        return explicit
    targets = agent.get("invocation_targets") or []
    if any(
        item.get("target_type") == "workspace" and str(item.get("target_id")) == workspace_id
        for item in targets
        if isinstance(item, dict)
    ):
        return "public_to_workspace"
    return "private"


def normalized_ids(value: Any) -> list[str]:
    result = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, str):
            result.append(item)
        elif isinstance(item, dict):
            identifier = item.get("id") or item.get("user_id") or item.get("member_id")
            if identifier:
                result.append(str(identifier))
    return sorted(set(result))


def managed_match(items: list[dict[str, Any]], object_key: str, field: str) -> dict[str, Any]:
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
        raise ObserverError(f"expected one managed {object_key}, found {len(matches)}")
    return matches[0]


def multica_candidates(
    explicit: str | None = None,
    home: Path | None = None,
    system: str | None = None,
) -> list[Path]:
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
    home = home or Path.home()
    system = (system or platform.system()).lower()
    if system == "windows":
        local = Path(os.environ.get("LOCALAPPDATA", home / "AppData/Local"))
        appdata = Path(os.environ.get("APPDATA", home / "AppData/Roaming"))
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
    unique = []
    seen = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            seen.add(key)
            unique.append(candidate)
    return unique


def discover_multica(explicit: str | None = None) -> str:
    for candidate in multica_candidates(explicit):
        if not candidate.is_file():
            continue
        result = subprocess.run(
            [str(candidate), "version", "--output", "json"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return str(candidate.resolve())
    raise ObserverError("Multica CLI not found; set MULTICA_BIN or install/login through Multica Desktop")


class CLI:
    def __init__(self, binary: str, profile: str | None, workspace_id: str):
        self.binary = binary
        self.profile = profile
        self.workspace_id = workspace_id

    def command(self, args: list[str]) -> list[str]:
        result = [self.binary]
        if self.profile:
            result.extend(["--profile", self.profile])
        result.extend(["--workspace-id", self.workspace_id, *args])
        return result

    def json(self, args: list[str], input_text: str | None = None) -> Any:
        result = subprocess.run(
            self.command(args),
            input=input_text,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
        )
        if result.returncode != 0:
            detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
            raise ObserverError(f"multica {' '.join(args[:3])} failed: {detail}")
        text = result.stdout.strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError as exc:
            raise ObserverError(f"multica returned invalid JSON for {' '.join(args[:3])}") from exc


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            empty = child is None or child == "" or child is False or child == 0
            result[key] = "<redacted>" if SECRET_KEY_RE.search(str(key)) and not empty else redact(child)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        text = PRIVATE_KEY_RE.sub("<redacted-private-key>", value)
        text = SENSITIVE_HEADER_RE.sub(lambda match: f"{match.group(1)}: <redacted>", text)
        text = SENSITIVE_ENV_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
        text = SENSITIVE_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)
        text = BEARER_RE.sub("<redacted-credential>", text)
        return text[:4000] + ("...<truncated>" if len(text) > 4000 else "")
    return value


def redacted_text(value: Any, limit: int = 4000) -> str:
    text = str(redact(str(value)))
    return text[:limit] + ("...<truncated>" if len(text) > limit else "")


def metadata_string_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = [item.strip() for item in value.split(",")]
        if isinstance(parsed, list):
            return [str(item) for item in parsed if str(item)]
    return []


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


def skill_version() -> str:
    skill_md = Path(__file__).resolve().parents[1] / "SKILL.md"
    text = skill_md.read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s*version:\s*(\S+)\s*$", text)
    return match.group(1) if match else "unknown"


def control_contract() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "references/control-plane-contract.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        raise ObserverError(f"Observer control-plane contract is unavailable: {path}") from exc


def embedded_package_hash() -> str:
    skill_text = (Path(__file__).resolve().parents[1] / "SKILL.md").read_text(encoding="utf-8")
    embedded_match = re.search(r"(?m)^\s*package_hash:\s*(\S+)\s*$", skill_text)
    return embedded_match.group(1) if embedded_match else ""


def portable_source_hash() -> str:
    skill_dir = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    files = sorted(
        (
            path
            for path in skill_dir.rglob("*")
            if path.is_file()
            and path.relative_to(skill_dir).as_posix()
            != "references/control-plane-contract.json"
            and not any(part in {"__pycache__", ".git"} for part in path.parts)
        ),
        key=lambda path: path.relative_to(skill_dir).as_posix(),
    )
    for path in files:
        relative_text = path.relative_to(skill_dir).as_posix()
        data = path.read_bytes()
        if path.suffix.lower() in {".md", ".py", ".json", ".yaml", ".yml", ".txt"}:
            text = data.decode("utf-8").replace("\r\n", "\n")
            if relative_text == "SKILL.md":
                text = re.sub(r"(?m)^\s*package_hash:\s*\S+\s*\n", "", text)
            data = text.encode("utf-8")
        relative = relative_text.encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def issue_ref(issue: dict[str, Any]) -> str:
    return str(issue.get("identifier") or issue.get("key") or issue.get("id") or "")


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


def resolve_requirement_context(
    cli: CLI, source: dict[str, Any], source_metadata: dict[str, Any]
) -> tuple[str, str, dict[str, Any]]:
    current = source
    current_metadata = source_metadata
    seen = set()
    while current.get("parent_issue_id"):
        parent_id = str(current["parent_issue_id"])
        if parent_id in seen:
            raise ObserverError("Issue parent chain contains a cycle")
        seen.add(parent_id)
        if len(seen) > 100:
            raise ObserverError("Issue parent chain exceeds 100 levels")
        current = cli.json(["issue", "get", parent_id, "--output", "json"])
        if not isinstance(current, dict):
            raise ObserverError(f"parent Issue is unreadable: {parent_id}")
        current_metadata = metadata_map(cli, issue_ref(current) or parent_id)
    requirement_id = issue_ref(current)
    protocol = str(current_metadata.get("protocol_revision") or "v2")
    return requirement_id, protocol, current_metadata


def set_metadata(cli: CLI, issue_id: str, key: str, value: Any) -> None:
    if isinstance(value, bool):
        kind, rendered = "bool", "true" if value else "false"
    elif isinstance(value, (int, float)):
        kind, rendered = "number", str(value)
    else:
        kind, rendered = "string", str(value)
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
            kind,
            "--output",
            "json",
        ]
    )


def add_comment(cli: CLI, issue_id: str, content: str) -> None:
    cli.json(["issue", "comment", "add", issue_id, "--content-stdin", "--output", "json"], input_text=content)


def record_pending_failure(
    cli: CLI,
    issue_id: str,
    rule_id: str,
    dedupe_key: str,
    phase: str,
    payload: dict[str, Any],
) -> None:
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).decode("ascii")
    try:
        add_comment(
            cli,
            issue_id,
            (
                f"WORKFLOW INCIDENT REPORT PENDING: rule={rule_id}, "
                f"dedupe={incident_fingerprint(dedupe_key)}, phase={phase}. "
                "The deterministic audit must retry or surface this report.\n"
                f"WORKFLOW_INCIDENT_PENDING_PAYLOAD {encoded}"
            ),
        )
    except ObserverError:
        pass


def resolve_control_plane(cli: CLI) -> tuple[dict[str, Any], dict[str, Any], str]:
    project = managed_match(as_list(cli.json(["project", "list", "--output", "json"]), "projects"), OPERATIONS_PROJECT_KEY, "description")
    observer = managed_match(as_list(cli.json(["agent", "list", "--output", "json"]), "agents"), OBSERVER_AGENT_KEY, "instructions")
    squad = managed_match(as_list(cli.json(["squad", "list", "--output", "json"]), "squads"), SQUAD_KEY, "instructions")
    members = as_list(cli.json(["squad", "member", "list", str(squad["id"]), "--output", "json"]), "members")
    approvers = [item for item in members if item.get("member_type") == "member" and item.get("role") == APPROVER_ROLE]
    if len(approvers) != 1:
        raise ObserverError(f"expected one human approver, found {len(approvers)}")
    return project, observer, str(approvers[0]["member_id"])


def find_incidents(
    cli: CLI, project_id: str, dedupe_key: str, max_issues: int = 5000
) -> list[dict[str, Any]]:
    result = []
    offset = 0
    page_size = 100
    while offset < max_issues:
        limit = min(page_size, max_issues - offset)
        page = as_list(
            cli.json(
                [
                    "issue",
                    "list",
                    "--project",
                    project_id,
                    "--metadata",
                    "workflow_object_type=incident",
                    "--metadata",
                    f"incident_dedupe_key={dedupe_key}",
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
        result.extend(page)
        if len(page) < limit:
            break
        offset += len(page)
    else:
        raise ObserverError(f"Incident lookup exceeded max_issues={max_issues} for dedupe key {dedupe_key}")

    fingerprint = incident_fingerprint(dedupe_key)
    fallback = []
    offset = 0
    while offset < max_issues:
        limit = min(page_size, max_issues - offset)
        page = as_list(
            cli.json(
                [
                    "issue",
                    "list",
                    "--project",
                    project_id,
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
        fallback.extend(
            item for item in page if f"[wf:{fingerprint}]" in str(item.get("title") or "")
        )
        if len(page) < limit:
            break
        offset += len(page)
    else:
        raise ObserverError(f"Incident fallback lookup exceeded max_issues={max_issues}")
    by_id = {issue_ref(item): item for item in [*result, *fallback] if issue_ref(item)}
    return list(by_id.values())


def incident_fingerprint(dedupe_key: str) -> str:
    return hashlib.sha256(dedupe_key.encode("utf-8")).hexdigest()[:12]


def incident_title(args: argparse.Namespace, dedupe_key: str, safe_summary: str) -> str:
    return (
        f"[Workflow Incident][{args.severity}][wf:{incident_fingerprint(dedupe_key)}] "
        f"{args.rule_id}: {safe_summary}"
    )


def incident_description(args: argparse.Namespace, source: dict[str, Any], dedupe_key: str) -> str:
    safe = redact(
        {
            "summary": args.summary,
            "expected": args.expected,
            "actual": args.actual,
            "evidence": args.evidence or "",
        }
    )
    return (
        f"# {safe['summary']}\n\n"
        f"## Source\n\n- Issue: {issue_ref(source)}\n- Rule: `{args.rule_id}`\n- Severity: `{args.severity}`\n"
        f"- Dedupe: `{dedupe_key}`\n\n"
        f"## Expected\n\n{safe['expected']}\n\n"
        f"## Actual\n\n{safe['actual']}\n\n"
        f"## Evidence\n\n{safe['evidence'] or 'See the linked source Issue; raw evidence was not copied.'}\n"
    )


def report_incident(cli: CLI, args: argparse.Namespace) -> dict[str, Any]:
    source = cli.json(["issue", "get", args.source_issue, "--output", "json"])
    source_id = issue_ref(source) or args.source_issue
    source_meta = metadata_map(cli, source_id)
    requirement, protocol, _ = resolve_requirement_context(cli, source, source_meta)
    entity = str(args.entity or source_id)
    dedupe_key = args.dedupe_key or f"{WORKFLOW_ID}:{protocol}:{args.rule_id}:{entity}"
    pending_payload = {
        "dedupe_key": dedupe_key,
        "rule_id": args.rule_id,
        "severity": args.severity,
        "summary": redacted_text(args.summary),
        "expected": redacted_text(args.expected),
        "actual": redacted_text(args.actual),
        "evidence": redacted_text(args.evidence or ""),
        "requirement": requirement,
        "entity": entity,
        "protocol": protocol,
        "reporter_agent_id": args.reporter_agent_id
        or os.environ.get("MULTICA_AGENT_ID", "unknown"),
        "reporter_role": args.reporter_role or "unknown",
        "block_source": bool(args.block_source),
    }
    try:
        set_metadata(
            cli, source_id, "workflow_incident_pending_index", WORKFLOW_ID
        )
    except ObserverError:
        record_pending_failure(
            cli, source_id, args.rule_id, dedupe_key, "pending-index", pending_payload
        )
        try:
            set_metadata(cli, source_id, "workflow_incident_pending", True)
        except ObserverError:
            pass
        raise
    try:
        set_metadata(
            cli,
            source_id,
            "workflow_incident_pending_payload",
            json.dumps(pending_payload, ensure_ascii=False, sort_keys=True),
        )
    except ObserverError:
        record_pending_failure(
            cli, source_id, args.rule_id, dedupe_key, "payload", pending_payload
        )
        try:
            set_metadata(cli, source_id, "workflow_incident_pending", True)
        except ObserverError:
            pass
        raise
    try:
        set_metadata(cli, source_id, "workflow_incident_pending", True)
    except ObserverError:
        record_pending_failure(
            cli, source_id, args.rule_id, dedupe_key, "pending-marker", pending_payload
        )
        raise
    project, observer, approver_id = resolve_control_plane(cli)
    version = skill_version()
    existing = find_incidents(cli, str(project["id"]), dedupe_key)
    active = [item for item in existing if str(item.get("status")) in ACTIVE_STATUSES]
    if len(active) > 1:
        raise ObserverError(f"multiple active Incidents share dedupe key {dedupe_key}")
    closed = sorted(
        [item for item in existing if str(item.get("status")) not in ACTIVE_STATUSES],
        key=lambda item: str(item.get("updated_at") or item.get("created_at") or ""),
        reverse=True,
    )
    incident = active[0] if active else (closed[0] if closed else None)
    recurrence_of = None
    reopened = False
    if incident and str(incident.get("status")) in {"done", "cancelled"}:
        incident_id = issue_ref(incident)
        incident_meta = metadata_map(cli, incident_id)
        fixed_release = incident_meta.get("incident_fixed_release")
        prior_version = str(incident_meta.get("workflow_version") or "")
        if fixed_release or (prior_version and prior_version != version):
            recurrence_of = incident_id
            incident = None
        else:
            cli.json(["issue", "update", incident_id, "--status", "todo", "--output", "json"])
            reopened = True

    safe_summary = redacted_text(args.summary, 240)
    description = incident_description(args, source, dedupe_key)
    if incident:
        incident_id = issue_ref(incident)
        action = "updated"
    else:
        created = cli.json(
            [
                "issue",
                "create",
                "--title",
                incident_title(args, dedupe_key, safe_summary),
                "--description-stdin",
                "--project",
                str(project["id"]),
                "--assignee-id",
                str(observer["id"]),
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
            raise ObserverError("created Incident did not return an Issue ID")
        action = "created"

    current_incident_meta = metadata_map(cli, incident_id)
    previous_requirements = set(
        metadata_string_list(current_incident_meta.get("incident_source_requirements"))
    )
    previous_severity = str(current_incident_meta.get("incident_severity") or "low")
    previous_notified = parse_time(current_incident_meta.get("incident_last_notified_at"))
    cooldown_hours = int(getattr(args, "notification_cooldown_hours", 24))
    deterministic_confirmation = bool(getattr(args, "deterministic_confirmation", False))
    blocked_requirement_count = int(getattr(args, "blocked_requirement_count", 0) or 0)
    previous_blocked_count = int(current_incident_meta.get("incident_blocked_requirement_count") or 0)
    now = datetime.now(timezone.utc)
    notification_due = bool(
        action == "created"
        or reopened
        or args.severity == "urgent"
        or SEVERITY_RANK.get(args.severity, 0) > SEVERITY_RANK.get(previous_severity, 0)
        or requirement not in previous_requirements
        or (deterministic_confirmation and str(current_incident_meta.get("incident_deterministic_confirmed")).lower() != "true")
        or blocked_requirement_count > previous_blocked_count
        or previous_notified is None
        or (now - previous_notified).total_seconds() >= cooldown_hours * 3600
    )
    if incident and notification_due:
        add_comment(cli, incident_id, f"Additional evidence from {source_id} at {utc_now()}\n\n{description}")
    all_requirements = sorted(previous_requirements | {requirement})
    seen_at = utc_now()
    evidence_log = []
    try:
        parsed_evidence = json.loads(str(current_incident_meta.get("incident_evidence_log") or "[]"))
        if isinstance(parsed_evidence, list):
            evidence_log = [item for item in parsed_evidence if isinstance(item, dict)]
    except json.JSONDecodeError:
        evidence_log = []
    evidence_log.append(
        {
            "seen_at": seen_at,
            "source_issue_id": source_id,
            "source_requirement_id": requirement,
            "summary": redacted_text(args.summary, 500),
            "actual": redacted_text(args.actual, 1000),
            "evidence": redacted_text(args.evidence or "", 1000),
        }
    )
    evidence_log = evidence_log[-20:]
    metadata = {
        "workflow_object_type": "incident",
        "workflow_id": WORKFLOW_ID,
        "incident_dedupe_key": dedupe_key,
        "incident_rule_id": args.rule_id,
        "incident_status": "new" if action == "created" or reopened else current_incident_meta.get("incident_status", "new"),
        "incident_severity": args.severity,
        "source_issue_id": source_id,
        "source_requirement_id": requirement,
        "reporter_agent_id": args.reporter_agent_id or os.environ.get("MULTICA_AGENT_ID", "unknown"),
        "reporter_role": args.reporter_role or "unknown",
        "observer_id": str(observer["id"]),
        "human_approver_id": approver_id,
        "workflow_version": version,
        "protocol_revision": protocol,
        "waiting_on": "workflow_observer",
        "incident_evidence_count": int(current_incident_meta.get("incident_evidence_count") or 0) + 1,
        "incident_last_seen_at": seen_at,
        "incident_source_requirements": json.dumps(all_requirements, ensure_ascii=False),
        "incident_deterministic_confirmed": deterministic_confirmation
        or str(current_incident_meta.get("incident_deterministic_confirmed")).lower() == "true",
        "incident_blocked_requirement_count": max(previous_blocked_count, blocked_requirement_count),
        "incident_evidence_log": json.dumps(evidence_log, ensure_ascii=False, sort_keys=True),
    }
    if notification_due:
        metadata["incident_last_notified_at"] = seen_at
    if recurrence_of:
        metadata["recurrence_of"] = recurrence_of
    for key, value in metadata.items():
        set_metadata(cli, incident_id, key, value)

    if args.severity in {"urgent", "high"} and notification_due:
        cli.json(["issue", "subscriber", "add", incident_id, "--user-id", approver_id, "--output", "json"])
    set_metadata(cli, source_id, "workflow_incident_id", incident_id)
    set_metadata(cli, source_id, "workflow_incident_last_evidence_at", seen_at)
    if notification_due:
        add_comment(cli, source_id, f"WORKFLOW INCIDENT {action.upper()}: {incident_id} (`{args.rule_id}`, {args.severity})")
    if args.block_source:
        cli.json(["issue", "update", source_id, "--status", "blocked", "--output", "json"])
        set_metadata(cli, source_id, "waiting_on", "workflow_fix")
        set_metadata(cli, source_id, "blocked_reason", f"workflow incident {incident_id}: {safe_summary}")
    set_metadata(cli, source_id, "workflow_incident_pending_payload", "")
    set_metadata(cli, source_id, "workflow_incident_pending_index", "")
    set_metadata(cli, source_id, "workflow_incident_pending", False)
    return {
        "action": action,
        "incident_id": incident_id,
        "dedupe_key": dedupe_key,
        "notified": notification_due,
    }


def list_workflow_issues(cli: CLI, max_issues: int) -> tuple[list[dict[str, Any]], bool]:
    result: dict[str, dict[str, Any]] = {}
    page_size = 100

    def scan(extra: list[str], workflow_only: bool = True) -> bool:
        offset = 0
        while True:
            command = ["issue", "list"]
            if workflow_only:
                command.extend(["--metadata", f"workflow_id={WORKFLOW_ID}"])
            command.extend(
                [
                    *extra,
                    "--limit",
                    str(page_size),
                    "--offset",
                    str(offset),
                    "--output",
                    "json",
                ]
            )
            page = as_list(
                cli.json(command),
                "issues",
            )
            for item in page:
                ref = issue_ref(item)
                if ref:
                    result[ref] = item
            if len(result) > max_issues:
                return False
            if len(page) < page_size:
                return True
            offset += len(page)

    for status in sorted(ACTIVE_STATUSES):
        if not scan(["--status", status]):
            return list(result.values())[:max_issues], False
    if not scan(["--metadata", "workflow_incident_pending=true"], workflow_only=False):
        return list(result.values())[:max_issues], False
    if not scan(
        ["--metadata", f"workflow_incident_pending_index={WORKFLOW_ID}"],
        workflow_only=False,
    ):
        return list(result.values())[:max_issues], False
    queue = list(result.values())
    fetched_parent_ids = set()
    while queue:
        child = queue.pop()
        parent_id = str(child.get("parent_issue_id") or "")
        if not parent_id or parent_id in fetched_parent_ids:
            continue
        fetched_parent_ids.add(parent_id)
        parent = cli.json(["issue", "get", parent_id, "--output", "json"])
        if not isinstance(parent, dict):
            raise ObserverError(f"parent Issue is unreadable: {parent_id}")
        ref = issue_ref(parent) or parent_id
        if ref not in result:
            result[ref] = parent
            if len(result) > max_issues:
                return list(result.values())[:max_issues], False
            queue.append(parent)
    return list(result.values()), True


def finding(rule_id: str, severity: str, issue: dict[str, Any], expected: str, actual: str) -> dict[str, Any]:
    return {
        "rule_id": rule_id,
        "severity": severity,
        "source_issue": issue_ref(issue),
        "entity": issue_ref(issue),
        "expected": expected,
        "actual": actual,
    }


def approval_findings(
    cli: CLI, issue: dict[str, Any], metadata: dict[str, Any]
) -> list[dict[str, Any]]:
    issue_id = issue_ref(issue)
    comments = as_list(
        cli.json(["issue", "comment", "list", issue_id, "--full", "--output", "json"]),
        "comments",
    )
    candidates = []
    for comment in comments:
        lines = [line.strip() for line in str(comment.get("content") or "").splitlines()]
        approval_lines = [line for line in lines if APPROVAL_LINE_RE.fullmatch(line)]
        if approval_lines:
            candidates.append((comment, approval_lines))
    human_id = str(metadata.get("human_approver_id") or "")
    invalid = [
        item
        for item, _ in candidates
        if item.get("author_type") != "member" or str(item.get("author_id") or "") != human_id
    ]
    findings = []
    if invalid:
        findings.append(
            finding(
                "WF-APPROVAL-001",
                "urgent",
                issue,
                "approval-like comments are authored by the durable human approver",
                f"invalid approval comment IDs: {[str(item.get('id') or '') for item in invalid]}",
            )
        )
    approval_comment_id = str(metadata.get("approval_comment_id") or "")
    approval_recorded = any(
        str(metadata.get(key)).lower() == "true"
        for key in ["plan_approved", "requirement_approved", "release_approved", "approval_recorded"]
    )
    expected_lines = set()
    approved_plan_revision = str(metadata.get("approved_plan_revision") or metadata.get("plan_revision") or "")
    approval_revision = str(metadata.get("approval_revision") or metadata.get("plan_revision") or "")
    if str(metadata.get("plan_approved")).lower() == "true" and approved_plan_revision:
        expected_lines.add(f"APPROVE PLAN {approved_plan_revision}")
    if str(metadata.get("requirement_approved")).lower() == "true" and approval_revision:
        expected_lines.add(f"APPROVE REQUIREMENT {approval_revision}")
    release_digest = str(metadata.get("release_plan_digest") or "")
    if str(metadata.get("release_approved")).lower() == "true" and release_digest:
        expected_lines.update(
            {
                f"APPROVE WORKFLOW RELEASE {release_digest}",
                f"APPROVE WORKFLOW RELEASE {release_digest[:12]}",
            }
        )
    if approval_comment_id and not expected_lines:
        stage = str(metadata.get("workflow_stage") or "")
        if stage in {"plan", "design"} and approval_revision:
            expected_lines.add(f"APPROVE PLAN {approval_revision}")
        elif stage == "requirement" and approval_revision:
            expected_lines.add(f"APPROVE REQUIREMENT {approval_revision}")
    valid = [
        item
        for item, lines in candidates
        if item.get("author_type") == "member"
        and str(item.get("author_id") or "") == human_id
        and (not expected_lines or any(line in expected_lines for line in lines))
    ]
    if approval_recorded and not approval_comment_id:
        findings.append(
            finding(
                "WF-APPROVAL-001",
                "urgent",
                issue,
                "recorded approval binds an explicit approval_comment_id",
                "approval metadata is true but approval_comment_id is missing",
            )
        )
    if approval_comment_id and not any(str(item.get("id") or "") == approval_comment_id for item in valid):
        findings.append(
            finding(
                "WF-APPROVAL-001",
                "urgent",
                issue,
                "approval_comment_id references a valid durable-human approval comment",
                (
                    f"approval_comment_id={approval_comment_id} is missing, has the wrong author, "
                    f"or does not match {sorted(expected_lines)}"
                ),
            )
        )
    elif approval_recorded and not valid:
        findings.append(
            finding(
                "WF-APPROVAL-001",
                "urgent",
                issue,
                "recorded approval has a durable-human approval comment",
                "approval metadata is true but no valid approval comment exists",
            )
        )
    return findings


def marker_keys(items: list[dict[str, Any]], field: str) -> list[str]:
    result = []
    for item in items:
        marker = parse_marker(str(item.get(field) or ""))
        if marker and marker.get("managed_by") == MANAGED_BY and marker.get("workflow_id") == WORKFLOW_ID:
            result.append(str(marker.get("object_key") or ""))
    return result


def detailed_items(
    cli: CLI, summaries: list[dict[str, Any]], resource: str
) -> list[dict[str, Any]]:
    result = []
    for summary in summaries:
        object_id = str(summary.get("id") or "")
        if not object_id:
            result.append(summary)
            continue
        raw = cli.json([resource, "get", object_id, "--output", "json"])
        nested = raw.get(resource) if isinstance(raw, dict) else None
        payload = nested if isinstance(nested, dict) else raw
        detail = {**summary, **(payload if isinstance(payload, dict) else {})}
        if isinstance(raw, dict):
            for field in ["triggers", "subscribers"]:
                if field in raw:
                    detail[field] = raw[field]
        result.append(detail)
    return result


def audit_control_plane(cli: CLI, coverage_issue: str | None) -> list[dict[str, Any]]:
    contract = control_contract()
    agents = detailed_items(
        cli,
        as_list(cli.json(["agent", "list", "--output", "json"]), "agents"),
        "agent",
    )
    squads = detailed_items(
        cli,
        as_list(cli.json(["squad", "list", "--output", "json"]), "squads"),
        "squad",
    )
    projects = detailed_items(
        cli,
        as_list(cli.json(["project", "list", "--output", "json"]), "projects"),
        "project",
    )
    autopilots = detailed_items(
        cli,
        as_list(cli.json(["autopilot", "list", "--output", "json"]), "autopilots"),
        "autopilot",
    )
    skills = as_list(cli.json(["skill", "list", "--output", "json"]), "skills")
    runtimes = as_list(cli.json(["runtime", "list", "--output", "json"]), "runtimes")
    skill_details = {
        str(skill.get("id") or ""): cli.json(
            ["skill", "get", str(skill["id"]), "--output", "json"]
        )
        for skill in skills
        if skill.get("id")
    }
    actual = {
        "agents": marker_keys(agents, "instructions"),
        "squads": marker_keys(squads, "instructions"),
        "projects": marker_keys(projects, "description"),
        "autopilots": marker_keys(autopilots, "description"),
    }
    expected = {
        "agents": sorted(f"agent.{key}" for key in contract["agents"]),
        "squads": [f"squad.{contract['squad']['key']}"],
        "projects": sorted(f"project.{key}" for key in contract["projects"]),
        "autopilots": sorted(f"autopilot.{key}" for key in contract["autopilots"]),
    }
    drift = {}
    collisions = {}

    def record_collisions(
        kind: str,
        key: str,
        items: list[dict[str, Any]],
        name_field: str,
        expected_name: str,
        marker_field: str,
        object_key: str,
    ) -> None:
        conflicts = []
        for item in items:
            if str(item.get(name_field) or "") != expected_name:
                continue
            marker = parse_marker(str(item.get(marker_field) or "")) or {}
            if not (
                marker.get("managed_by") == MANAGED_BY
                and marker.get("workflow_id") == WORKFLOW_ID
                and marker.get("object_key") == object_key
            ):
                conflicts.append(str(item.get("id") or "<missing-id>"))
        if conflicts:
            collisions.setdefault(kind, {})[key] = sorted(conflicts)

    for key, desired in contract["agents"].items():
        record_collisions(
            "agents", key, agents, "name", desired["name"], "instructions", f"agent.{key}"
        )
    record_collisions(
        "squads",
        contract["squad"]["key"],
        squads,
        "name",
        contract["squad"]["name"],
        "instructions",
        f"squad.{contract['squad']['key']}",
    )
    for key, desired in contract["projects"].items():
        record_collisions(
            "projects", key, projects, "title", desired["title"], "description", f"project.{key}"
        )
    for key, desired in contract["autopilots"].items():
        record_collisions(
            "autopilots",
            key,
            autopilots,
            "title",
            desired["title"],
            "description",
            f"autopilot.{key}",
        )
    for name in contract["skills"]:
        conflicts = []
        for skill in skills:
            if str(skill.get("name") or "") != name:
                continue
            detail = skill_details.get(str(skill.get("id") or ""), skill)
            if not (
                deep_find(detail, "managed_by") == MANAGED_BY
                and deep_find(detail, "workflow_id") == WORKFLOW_ID
            ):
                conflicts.append(str(skill.get("id") or "<missing-id>"))
        if conflicts:
            collisions.setdefault("skills", {})[name] = sorted(conflicts)
    if collisions:
        drift["name_collisions"] = collisions
    if contract.get("workflow_version") != skill_version():
        drift["contract_version"] = {
            "expected": skill_version(),
            "actual": contract.get("workflow_version"),
        }
    current_source_hash = portable_source_hash()
    if contract.get("observer_source_hash") != current_source_hash:
        drift["observer_source_hash"] = {
            "expected": contract.get("observer_source_hash"),
            "actual": current_source_hash,
        }
    for kind, expected_keys in expected.items():
        actual_keys = actual[kind]
        if sorted(actual_keys) != expected_keys or len(actual_keys) != len(set(actual_keys)):
            drift[kind] = {"expected": expected_keys, "actual": sorted(actual_keys)}
    runtime_by_id = {str(item.get("id")): item for item in runtimes if item.get("id")}
    agent_by_key = {}
    for key in contract["agents"]:
        matches = [
            item
            for item in agents
            if (parse_marker(str(item.get("instructions") or "")) or {}).get("object_key")
            == f"agent.{key}"
        ]
        if len(matches) == 1:
            agent_by_key[key] = matches[0]

    matching_profiles = []
    for profile_name, bindings in contract["profiles"].items():
        matches = True
        for key, desired in contract["agents"].items():
            current = agent_by_key.get(key)
            if not current:
                matches = False
                break
            binding = bindings[desired["runtime_binding"]]
            runtime = runtime_by_id.get(str(current.get("runtime_id") or ""), {})
            if (
                runtime.get("provider") != binding.get("provider")
                or runtime.get("status") != binding.get("required_status", "online")
                or str(current.get("model") or "") != str(binding.get("model") or "")
                or str(current.get("thinking_level") or "")
                != str(binding.get("thinking_level") or "")
            ):
                matches = False
                break
        if matches:
            matching_profiles.append(profile_name)
    if len(agent_by_key) == len(contract["agents"]) and len(matching_profiles) != 1:
        drift["deployment_profile"] = {"matching_profiles": matching_profiles}
    selected_profile = matching_profiles[0] if len(matching_profiles) == 1 else None

    agent_drift = {}
    for key, desired in contract["agents"].items():
        current = agent_by_key.get(key)
        if not current:
            continue
        mismatches = {}
        direct = {
            "name": str(current.get("name") or ""),
            "description": str(current.get("description") or ""),
            "max_concurrent_tasks": int(current.get("max_concurrent_tasks") or 0),
            "permission_mode": normalize_permission(current, cli.workspace_id),
            "instructions_sha256": sha256_value(strip_marker(str(current.get("instructions") or ""))),
        }
        for field, actual_value in direct.items():
            if actual_value != desired[field]:
                mismatches[field] = {"expected": desired[field], "actual": actual_value}
        if selected_profile:
            binding = contract["profiles"][selected_profile][desired["runtime_binding"]]
            runtime = runtime_by_id.get(str(current.get("runtime_id") or ""), {})
            runtime_actual = {
                "provider": runtime.get("provider"),
                "status": runtime.get("status"),
                "model": str(current.get("model") or ""),
                "thinking_level": str(current.get("thinking_level") or ""),
            }
            runtime_expected = {
                "provider": binding.get("provider"),
                "status": binding.get("required_status", "online"),
                "model": str(binding.get("model") or ""),
                "thinking_level": str(binding.get("thinking_level") or ""),
            }
            if runtime_actual != runtime_expected:
                mismatches["runtime"] = {"expected": runtime_expected, "actual": runtime_actual}
            marker_hash = (parse_marker(str(current.get("instructions") or "")) or {}).get("spec_hash")
            expected_marker_hash = desired["spec_hashes"][selected_profile]
            if marker_hash != expected_marker_hash:
                mismatches["marker_spec_hash"] = {
                    "expected": expected_marker_hash,
                    "actual": marker_hash,
                }
        if mismatches:
            agent_drift[key] = mismatches
    if agent_drift:
        drift["agent_specs"] = agent_drift

    managed_skill_names = []
    managed_skill_ids = {}
    skill_drift = {}
    expected_version = skill_version()
    for skill in skills:
        skill_id = str(skill.get("id") or "")
        detail = skill_details.get(skill_id, skill)
        if deep_find(detail, "managed_by") != MANAGED_BY or deep_find(detail, "workflow_id") != WORKFLOW_ID:
            continue
        name = str(skill.get("name") or deep_find(detail, "name") or "")
        managed_skill_names.append(name)
        managed_skill_ids[skill_id] = name
        version = str(deep_find(detail, "version") or "")
        if version != expected_version:
            skill_drift.setdefault(name, {})["version"] = {
                "expected": expected_version,
                "actual": version,
            }
        expected_hash = (contract["skills"].get(name) or {}).get("package_hash")
        actual_hash = str(deep_find(detail, "package_hash") or "")
        if expected_hash and actual_hash != expected_hash:
            skill_drift.setdefault(name, {})["package_hash"] = {
                "expected": expected_hash,
                "actual": actual_hash,
            }
        if name == "multica-workflow-observer":
            embedded_hash = embedded_package_hash()
            if embedded_hash and actual_hash != embedded_hash:
                skill_drift.setdefault(name, {})["self_package_hash"] = {
                    "embedded": embedded_hash,
                    "workspace": actual_hash,
                }
    if sorted(managed_skill_names) != sorted(contract["skills"]) or len(managed_skill_names) != len(set(managed_skill_names)):
        drift["skills"] = {
            "expected": sorted(contract["skills"]),
            "actual": sorted(managed_skill_names),
        }
    if skill_drift:
        drift["skill_specs"] = skill_drift

    attachment_drift = {}
    for key, current in agent_by_key.items():
        assignments = as_list(
            cli.json(["agent", "skills", "list", str(current["id"]), "--output", "json"]),
            "skills",
        )
        actual_managed = sorted(
            {
                managed_skill_ids.get(str(item.get("id") or item.get("skill_id") or ""))
                for item in assignments
                if managed_skill_ids.get(str(item.get("id") or item.get("skill_id") or ""))
            }
        )
        expected_managed = sorted(
            name for name, spec in contract["skills"].items() if key in spec.get("attach_to", [])
        )
        if actual_managed != expected_managed:
            attachment_drift[key] = {"expected": expected_managed, "actual": actual_managed}
    if attachment_drift:
        drift["skill_attachments"] = attachment_drift

    squad_matches = [
        item
        for item in squads
        if (parse_marker(str(item.get("instructions") or "")) or {}).get("object_key")
        == f"squad.{contract['squad']['key']}"
    ]
    human_approver_id = ""
    if len(squad_matches) == 1:
        squad = squad_matches[0]
        desired_squad = contract["squad"]
        squad_mismatches = {}
        squad_actual = {
            "name": str(squad.get("name") or ""),
            "description": str(squad.get("description") or ""),
            "instructions_sha256": sha256_value(strip_marker(str(squad.get("instructions") or ""))),
            "leader_id": str(squad.get("leader_id") or ""),
        }
        squad_expected = {
            "name": desired_squad["name"],
            "description": desired_squad["description"],
            "instructions_sha256": desired_squad["instructions_sha256"],
            "leader_id": str((agent_by_key.get(desired_squad["leader"]) or {}).get("id") or ""),
        }
        if squad_actual != squad_expected:
            squad_mismatches["spec"] = {"expected": squad_expected, "actual": squad_actual}
        squad_marker_hash = (parse_marker(str(squad.get("instructions") or "")) or {}).get("spec_hash")
        if squad_marker_hash != desired_squad["spec_hash"]:
            squad_mismatches["marker_spec_hash"] = {
                "expected": desired_squad["spec_hash"],
                "actual": squad_marker_hash,
            }
        members = as_list(
            cli.json(["squad", "member", "list", str(squad["id"]), "--output", "json"]),
            "members",
        )
        actual_agent_roles = {
            str(item.get("member_id")): str(item.get("role") or "")
            for item in members
            if item.get("member_type") == "agent"
            and str(item.get("member_id")) in {str(agent.get("id")) for agent in agent_by_key.values()}
        }
        expected_agent_roles = {
            str((agent_by_key.get(item["agent"]) or {}).get("id") or ""): item["role"]
            for item in desired_squad["agent_members"]
        }
        if actual_agent_roles != expected_agent_roles:
            squad_mismatches["agent_roles"] = {
                "expected": expected_agent_roles,
                "actual": actual_agent_roles,
            }
        approver_role = desired_squad["human_members"][0]["role"]
        approvers = [
            item
            for item in members
            if item.get("member_type") == "member" and item.get("role") == approver_role
        ]
        if len(approvers) == 1:
            human_approver_id = str(approvers[0].get("member_id") or "")
        else:
            squad_mismatches["human_approver"] = {"expected_count": 1, "actual_count": len(approvers)}
        if squad_mismatches:
            drift["squad_spec"] = squad_mismatches

    project_by_key = {}
    for key, desired in contract["projects"].items():
        matches = [
            item
            for item in projects
            if (parse_marker(str(item.get("description") or "")) or {}).get("object_key")
            == f"project.{key}"
        ]
        if len(matches) != 1:
            continue
        project = matches[0]
        project_by_key[key] = project
        actual_project = {
            "title": str(project.get("title") or ""),
            "description": strip_marker(str(project.get("description") or "")).strip(),
            "lead_id": str(project.get("lead_id") or ""),
            "status": str(project.get("status") or ""),
            "icon": str(project.get("icon") or ""),
        }
        expected_project = {
            "title": desired["title"],
            "description": str(desired["description"]).strip(),
            "lead_id": str((agent_by_key.get(desired["lead"]) or {}).get("id") or ""),
            "status": desired["status"],
            "icon": str(desired.get("icon") or ""),
        }
        if actual_project != expected_project:
            drift.setdefault("project_specs", {})[key] = {
                "expected": expected_project,
                "actual": actual_project,
            }
        project_marker_hash = (parse_marker(str(project.get("description") or "")) or {}).get("spec_hash")
        if project_marker_hash != desired["spec_hash"]:
            drift.setdefault("project_specs", {}).setdefault(key, {})["marker_spec_hash"] = {
                "expected": desired["spec_hash"],
                "actual": project_marker_hash,
            }

    for key, desired in contract["autopilots"].items():
        matches = [
            item
            for item in autopilots
            if (parse_marker(str(item.get("description") or "")) or {}).get("object_key")
            == f"autopilot.{key}"
        ]
        if len(matches) != 1:
            continue
        detail = matches[0]
        actual_autopilot = {
            "title": str(detail.get("title") or ""),
            "description": strip_marker(str(detail.get("description") or "")).strip(),
            "agent_id": str(detail.get("agent_id") or ""),
            "mode": str(detail.get("mode") or ""),
            "project_id": str(detail.get("project_id") or ""),
            "priority": str(detail.get("priority") or "none"),
            "status": str(detail.get("status") or ""),
            "issue_title_template": str(detail.get("issue_title_template") or ""),
            "subscriber_ids": normalized_ids(detail.get("subscribers") or detail.get("subscriber_ids")),
        }
        expected_autopilot = {
            "title": desired["title"],
            "description": str(desired["description"]).strip(),
            "agent_id": str((agent_by_key.get(desired["agent"]) or {}).get("id") or ""),
            "mode": desired["mode"],
            "project_id": str((project_by_key.get(desired["project"]) or {}).get("id") or ""),
            "priority": desired["priority"],
            "status": desired["status"],
            "issue_title_template": str(desired.get("issue_title_template") or ""),
            "subscriber_ids": [human_approver_id] if desired.get("subscribers") else [],
        }
        mismatches = {}
        if actual_autopilot != expected_autopilot:
            mismatches["spec"] = {"expected": expected_autopilot, "actual": actual_autopilot}
        expected_autopilot_hash = sha256_value(
            {
                "key": key,
                "title": desired["title"],
                "description": str(desired["description"]).strip() + "\n",
                "agent": desired["agent"],
                "mode": desired["mode"],
                "project": desired["project"],
                "priority": desired["priority"],
                "status": desired["status"],
                "issue_title_template": str(desired.get("issue_title_template") or ""),
                "subscriber_ids": [human_approver_id] if desired.get("subscribers") else [],
            }
        )
        autopilot_marker_hash = (parse_marker(str(detail.get("description") or "")) or {}).get("spec_hash")
        if autopilot_marker_hash != expected_autopilot_hash:
            mismatches["marker_spec_hash"] = {
                "expected": expected_autopilot_hash,
                "actual": autopilot_marker_hash,
            }
        current_triggers = detail.get("triggers") if isinstance(detail.get("triggers"), list) else []
        for expected_trigger in desired.get("triggers", []):
            trigger_matches = [
                item for item in current_triggers if item.get("label") == expected_trigger["label"]
            ]
            if len(trigger_matches) != 1:
                mismatches.setdefault("triggers", {})[expected_trigger["key"]] = {
                    "expected_count": 1,
                    "actual_count": len(trigger_matches),
                }
                continue
            current_trigger = trigger_matches[0]
            actual_trigger = {
                "kind": current_trigger.get("kind") or current_trigger.get("type"),
                "label": current_trigger.get("label") or "",
                "enabled": bool(current_trigger.get("enabled", True)),
                "cron": current_trigger.get("cron") or current_trigger.get("schedule") or "",
                "timezone": current_trigger.get("timezone") or "UTC",
            }
            expected_trigger_spec = {
                field: expected_trigger[field]
                for field in ["kind", "label", "enabled", "cron", "timezone"]
            }
            if actual_trigger != expected_trigger_spec:
                mismatches.setdefault("triggers", {})[expected_trigger["key"]] = {
                    "expected": expected_trigger_spec,
                    "actual": actual_trigger,
                }
        if mismatches:
            drift.setdefault("autopilot_specs", {})[key] = mismatches
    if not drift:
        return []
    return [
        finding(
            "WF-DRIFT-001",
            "high",
            {"identifier": coverage_issue or ""},
            "managed control-plane inventory and Skill versions match the reviewed release",
            json.dumps(drift, ensure_ascii=False, sort_keys=True),
        )
    ]


def flatten_issue_children(value: Any) -> list[dict[str, Any]]:
    result = []
    if isinstance(value, dict):
        if (value.get("id") or value.get("identifier") or value.get("key")) and value.get("status"):
            result.append(value)
        for child in value.values():
            result.extend(flatten_issue_children(child))
    elif isinstance(value, list):
        for child in value:
            result.extend(flatten_issue_children(child))
    return result


def parent_findings(cli: CLI, issue: dict[str, Any]) -> list[dict[str, Any]]:
    if str(issue.get("status") or "") != "done":
        return []
    children = flatten_issue_children(
        cli.json(["issue", "children", issue_ref(issue), "--output", "json"])
    )
    incomplete = [child for child in children if str(child.get("status")) not in {"done", "cancelled"}]
    if not incomplete:
        return []
    return [
        finding(
            "WF-PARENT-001",
            "high",
            issue,
            "done parent has no incomplete required child",
            f"incomplete children: {[issue_ref(child) for child in incomplete]}",
        )
    ]


def audit_issue(cli: CLI, issue: dict[str, Any], backlog_hours: int) -> list[dict[str, Any]]:
    issue_id = issue_ref(issue)
    meta = metadata_map(cli, issue_id)
    object_type = meta.get("workflow_object_type")
    if object_type in {"incident", "canary_fixture"}:
        return []
    status = str(issue.get("status") or "")
    findings = []
    findings.extend(approval_findings(cli, issue, meta))
    try:
        requirement_id, authoritative_protocol, _ = resolve_requirement_context(
            cli, issue, meta
        )
    except ObserverError as exc:
        findings.append(
            finding(
                "WF-PROTOCOL-001",
                "high",
                issue,
                "top-level requirement protocol authority is readable",
                str(exc),
            )
        )
        authoritative_protocol = str(meta.get("top_protocol_revision") or "v2")
        requirement_id = issue_id
    effective_protocol = authoritative_protocol
    stage = str(meta.get("workflow_stage") or issue.get("stage") or "")
    if effective_protocol == "v3":
        required_contract = (
            [
                "workflow_id",
                "workflow_version",
                "top_protocol_revision",
                "human_approver_id",
                "workflow_stage",
            ]
            if issue_id == requirement_id
            else ["human_approver_id", "workflow_stage"]
        )
        missing_contract = [
            key
            for key in required_contract
            if not (meta.get(key) or (key == "workflow_stage" and stage))
        ]
        if missing_contract:
            findings.append(
                finding(
                    "WF-PROTOCOL-001",
                    "high",
                    issue,
                    "v3 Issue carries the required workflow contract metadata",
                    f"missing metadata: {missing_contract}",
                )
            )
        if stage and stage != "requirement" and not meta.get("plan_revision"):
            findings.append(
                finding(
                    "WF-PLANREV-001",
                    "high",
                    issue,
                    "non-requirement v3 stages bind a Plan revision",
                    "plan_revision is missing",
                )
            )
        if status == "in_review":
            missing_review_ids = [key for key in ["original_owner_id", "reviewer_id"] if not meta.get(key)]
            if missing_review_ids:
                findings.append(
                    finding(
                        "WF-REVIEW-002",
                        "high",
                        issue,
                        "review stage records independent owner and reviewer IDs",
                        f"missing metadata: {missing_review_ids}",
                    )
                )
        if stage in {"development_task", "integration_validation"} and status in {
            "backlog",
            "todo",
            "in_progress",
        } and "dependencies_satisfied" not in meta:
            findings.append(
                finding(
                    "WF-DEPENDENCY-001",
                    "high",
                    issue,
                    "active task records its dependency gate",
                    "dependencies_satisfied is missing",
                )
            )
        if stage == "development_task" and status in {"in_review", "done"}:
            missing_review_sha = [
                key for key in ["pr_head_sha", "reviewed_commit_sha"] if not meta.get(key)
            ]
            if missing_review_sha:
                findings.append(
                    finding(
                        "WF-REVIEW-001",
                        "high",
                        issue,
                        "reviewed development task binds current and reviewed commit SHAs",
                        f"missing metadata: {missing_review_sha}",
                    )
                )
    if status == "blocked" and (not meta.get("blocked_reason") or not meta.get("waiting_on")):
        findings.append(finding("WF-BLOCKED-001", "medium", issue, "blocked_reason and waiting_on are present", "blocked metadata is incomplete"))
    pending_payload_without_link = bool(meta.get("workflow_incident_pending_payload")) and not meta.get(
        "workflow_incident_id"
    )
    if str(meta.get("workflow_incident_pending")).lower() == "true" or pending_payload_without_link:
        findings.append(
            finding(
                "WF-INCIDENT-001",
                "medium",
                issue,
                "pending report is recovered or visibly failed",
                "workflow Incident pending payload requires recovery",
            )
        )
    reviewed = meta.get("review_commit_sha") or meta.get("reviewed_commit_sha")
    current = meta.get("pr_head_sha") or meta.get("current_commit_sha")
    if reviewed and current and reviewed != current:
        findings.append(finding("WF-REVIEW-001", "high", issue, "Review SHA equals current PR head", f"reviewed={reviewed}, current={current}"))
    if meta.get("original_owner_id") and meta.get("reviewer_id") == meta.get("original_owner_id"):
        findings.append(finding("WF-REVIEW-002", "high", issue, "reviewer is independent", "reviewer_id equals original_owner_id"))
    if str(meta.get("implementation_started")).lower() == "true" and str(meta.get("plan_approved")).lower() != "true":
        findings.append(finding("WF-PLAN-001", "high", issue, "approved Plan precedes Implementation", "implementation_started without plan_approved"))
    if status in {"todo", "in_progress"} and str(meta.get("dependencies_satisfied")).lower() == "false":
        findings.append(finding("WF-DEPENDENCY-001", "high", issue, "active task dependencies are satisfied", "dependencies_satisfied=false"))
    if meta.get("approved_plan_revision") and meta.get("plan_revision") != meta.get("approved_plan_revision"):
        findings.append(finding("WF-PLANREV-001", "high", issue, "task Plan revision equals approved revision", f"plan_revision={meta.get('plan_revision')}, approved={meta.get('approved_plan_revision')}"))
    if meta.get("approval_author_id") and (
        meta.get("approval_author_type") != "member" or meta.get("approval_author_id") != meta.get("human_approver_id")
    ):
        findings.append(finding("WF-APPROVAL-001", "urgent", issue, "approval author matches durable human approver", "approval identity mismatch"))
    if status == "backlog" and str(meta.get("dependencies_satisfied")).lower() == "true":
        updated = parse_time(issue.get("updated_at"))
        if updated and (datetime.now(timezone.utc) - updated).total_seconds() >= backlog_hours * 3600:
            findings.append(finding("WF-BACKLOG-001", "medium", issue, "dependency-satisfied task is promoted", f"backlog for at least {backlog_hours} hours"))
    if int(meta.get("runtime_failure_count") or 0) >= 2 and meta.get("waiting_on") != "runtime":
        findings.append(finding("WF-RUNTIME-001", "high", issue, "repeated Runtime failure is surfaced", "runtime_failure_count>=2 without waiting_on=runtime"))
    explicit_protocol = meta.get("protocol_revision")
    top_protocol = meta.get("top_protocol_revision")
    if (explicit_protocol and explicit_protocol != authoritative_protocol) or (
        top_protocol and top_protocol != authoritative_protocol
    ):
        findings.append(
            finding(
                "WF-PROTOCOL-001",
                "high",
                issue,
                "child protocol inherits the top-level requirement",
                (
                    f"protocol_revision={explicit_protocol}, top_protocol_revision={top_protocol}, "
                    f"authoritative={authoritative_protocol}"
                ),
            )
        )
    if object_type == "maintenance_change" and meta.get("incident_severity") in {"urgent", "high"} and status in ACTIVE_STATUSES:
        updated = parse_time(issue.get("updated_at"))
        if updated and (datetime.now(timezone.utc) - updated).total_seconds() >= 24 * 3600:
            findings.append(finding("WF-MAINT-001", "high", issue, "urgent/high Maintenance Change progresses within 24 hours", "maintenance change is stale"))
    return findings


def health(cli: CLI, max_age_minutes: int) -> dict[str, Any]:
    autopilots = detailed_items(
        cli,
        as_list(cli.json(["autopilot", "list", "--output", "json"]), "autopilots"),
        "autopilot",
    )
    object_key = "autopilot.workflow-health-audit"
    autopilot = managed_match(autopilots, object_key, "description")
    expected_title = str(
        (control_contract().get("autopilots", {}).get("workflow-health-audit") or {}).get(
            "title"
        )
        or ""
    )
    conflicts = [
        str(item.get("id") or "<missing-id>")
        for item in autopilots
        if expected_title
        and str(item.get("title") or "") == expected_title
        and (parse_marker(str(item.get("description") or "")) or {}).get("object_key")
        != object_key
    ]
    if conflicts:
        raise ObserverError(
            f"Observer Autopilot title has unmarked collisions: {sorted(conflicts)}"
        )
    runs = as_list(cli.json(["autopilot", "runs", str(autopilot["id"]), "--limit", "20", "--output", "json"]), "runs")
    if not runs:
        raise ObserverError("Observer Autopilot has no readable run history")
    active_statuses = {"queued", "pending", "running", "in_progress", "in-progress"}
    terminal_runs = [
        item
        for item in runs
        if str(item.get("status") or "").lower() not in active_statuses
    ]
    latest = max(
        terminal_runs or runs,
        key=lambda item: str(item.get("created_at") or item.get("started_at") or ""),
    )
    stamp = parse_time(latest.get("finished_at") or latest.get("created_at") or latest.get("started_at"))
    if not stamp:
        raise ObserverError("Observer Autopilot latest run has no parseable timestamp")
    age = (datetime.now(timezone.utc) - stamp).total_seconds() / 60
    status = str(latest.get("status") or "").lower()
    conclusion = str(latest.get("conclusion") or "").lower()
    if age > max_age_minutes:
        raise ObserverError(f"Observer Autopilot latest run is stale: {age:.0f} minutes")
    if status and status not in {"success", "succeeded", "completed", "done"}:
        raise ObserverError(f"Observer Autopilot latest run status is {status}")
    if conclusion and conclusion not in {"success", "succeeded"}:
        raise ObserverError(
            f"Observer Autopilot latest run conclusion is {conclusion}"
        )
    if not status and not conclusion:
        raise ObserverError("Observer Autopilot latest run has no status or conclusion")
    return {"autopilot_id": autopilot["id"], "latest_run": latest, "age_minutes": round(age, 1)}


def pending_namespace(issue: dict[str, Any], payload: dict[str, Any]) -> argparse.Namespace | None:
    required_payload = ["dedupe_key", "rule_id", "severity", "summary", "expected", "actual"]
    if not all(payload.get(key) not in {None, ""} for key in required_payload):
        return None
    return argparse.Namespace(
        source_issue=issue_ref(issue),
        source_requirement=payload.get("requirement"),
        rule_id=str(payload["rule_id"]),
        severity=str(payload["severity"]),
        summary=str(payload["summary"]),
        expected=str(payload["expected"]),
        actual=str(payload["actual"]),
        evidence=str(payload.get("evidence") or ""),
        entity=payload.get("entity"),
        dedupe_key=str(payload["dedupe_key"]),
        protocol_revision=payload.get("protocol"),
        reporter_agent_id=str(payload.get("reporter_agent_id") or "unknown"),
        reporter_role=str(payload.get("reporter_role") or "unknown"),
        block_source=bool(payload.get("block_source")),
        notification_cooldown_hours=24,
        deterministic_confirmation=True,
        blocked_requirement_count=0,
    )


def pending_recovery_args(
    issue: dict[str, Any], metadata: dict[str, Any], comments: list[dict[str, Any]] | None = None
) -> argparse.Namespace | None:
    payload_value = metadata.get("workflow_incident_pending_payload")
    payload = None
    if isinstance(payload_value, str) and payload_value:
        try:
            parsed = json.loads(payload_value)
            payload = parsed if isinstance(parsed, dict) else None
        except json.JSONDecodeError:
            payload = None
    pending = str(metadata.get("workflow_incident_pending")).lower() == "true"
    if payload and (pending or not metadata.get("workflow_incident_id")):
        recovered = pending_namespace(issue, payload)
        if recovered:
            return recovered
    for comment in reversed(comments or []):
        match = re.search(
            r"(?m)^WORKFLOW_INCIDENT_PENDING_PAYLOAD\s+([A-Za-z0-9_=-]+)\s*$",
            str(comment.get("content") or ""),
        )
        if not match:
            continue
        try:
            decoded = base64.urlsafe_b64decode(match.group(1).encode("ascii"))
            parsed = json.loads(decoded.decode("utf-8"))
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            continue
        if isinstance(parsed, dict):
            recovered = pending_namespace(issue, parsed)
            if recovered:
                return recovered
    required = [
        "workflow_incident_pending_dedupe",
        "workflow_incident_pending_rule_id",
        "workflow_incident_pending_severity",
        "workflow_incident_pending_summary",
        "workflow_incident_pending_expected",
        "workflow_incident_pending_actual",
    ]
    if not pending or any(
        metadata.get(key) in {None, ""} for key in required
    ):
        return None
    return argparse.Namespace(
        source_issue=issue_ref(issue),
        source_requirement=metadata.get("workflow_incident_pending_requirement"),
        rule_id=str(metadata["workflow_incident_pending_rule_id"]),
        severity=str(metadata["workflow_incident_pending_severity"]),
        summary=str(metadata["workflow_incident_pending_summary"]),
        expected=str(metadata["workflow_incident_pending_expected"]),
        actual=str(metadata["workflow_incident_pending_actual"]),
        evidence=str(metadata.get("workflow_incident_pending_evidence") or ""),
        entity=metadata.get("workflow_incident_pending_entity"),
        dedupe_key=str(metadata["workflow_incident_pending_dedupe"]),
        protocol_revision=metadata.get("workflow_incident_pending_protocol"),
        reporter_agent_id=os.environ.get("MULTICA_AGENT_ID"),
        reporter_role="工作流观察员恢复",
        block_source=str(metadata.get("workflow_incident_pending_block_source")).lower() == "true",
        notification_cooldown_hours=24,
        deterministic_confirmation=True,
        blocked_requirement_count=0,
    )


def audit(cli: CLI, args: argparse.Namespace) -> dict[str, Any]:
    coverage_issue = args.coverage_issue or os.environ.get("MULTICA_TASK_ID")
    issues, complete = list_workflow_issues(cli, args.max_issues)
    findings = []
    recovered = []
    if args.report:
        for issue in issues:
            comments = as_list(
                cli.json(
                    ["issue", "comment", "list", issue_ref(issue), "--full", "--output", "json"]
                ),
                "comments",
            )
            recovery_args = pending_recovery_args(
                issue, metadata_map(cli, issue_ref(issue)), comments
            )
            if recovery_args:
                recovered.append(report_incident(cli, recovery_args))
    for issue in issues:
        findings.extend(audit_issue(cli, issue, args.backlog_hours))
        findings.extend(parent_findings(cli, issue))
    if not complete:
        findings.append(
            {
                "rule_id": "WF-AUDIT-COVERAGE-001",
                "severity": "urgent",
                "source_issue": coverage_issue or (issue_ref(issues[0]) if issues else ""),
                "entity": WORKFLOW_ID,
                "expected": "all active workflow Issues are scanned",
                "actual": f"scan exceeded max_issues={args.max_issues}",
            }
        )
    if args.scope in {"issues", "all"}:
        findings.extend(audit_control_plane(cli, coverage_issue))
    health_result = None
    health_error = None
    if args.scope in {"all", "health"}:
        try:
            health_result = health(cli, args.health_max_age_minutes)
        except ObserverError as exc:
            health_error = str(exc)
            if coverage_issue:
                findings.append(
                    {
                        "rule_id": "WF-OBSERVER-001",
                        "severity": "high",
                        "source_issue": coverage_issue,
                        "entity": "workflow-health-audit",
                        "expected": "Observer Autopilot has a recent successful run",
                        "actual": health_error,
                    }
                )
    reported = []
    if args.report:
        source_status = {issue_ref(issue): str(issue.get("status") or "") for issue in issues}
        for item in findings:
            if not item.get("source_issue"):
                raise ObserverError(f"cannot persist finding without source Issue: {item['rule_id']}")
            report_args = argparse.Namespace(
                source_issue=item["source_issue"],
                source_requirement=None,
                rule_id=item["rule_id"],
                severity=item["severity"],
                summary=item["actual"],
                expected=item["expected"],
                actual=item["actual"],
                evidence="Deterministic workflow audit finding.",
                entity=item.get("entity"),
                dedupe_key=None,
                protocol_revision=None,
                reporter_agent_id=os.environ.get("MULTICA_AGENT_ID"),
                reporter_role="工作流观察员",
                block_source=item["severity"] in {"urgent", "high"}
                and source_status.get(str(item.get("source_issue") or "")) in ACTIVE_STATUSES,
                notification_cooldown_hours=24,
                deterministic_confirmation=True,
                blocked_requirement_count=0,
            )
            reported.append(report_incident(cli, report_args))
    by_severity: dict[str, int] = {}
    for item in findings:
        by_severity[item["severity"]] = by_severity.get(item["severity"], 0) + 1
    return {
        "scanned": len(issues),
        "coverage_complete": complete,
        "findings": findings,
        "finding_counts": by_severity,
        "reported": reported,
        "recovered": recovered,
        "health": health_result,
        "health_error": health_error,
    }


def audit_failed(result: dict[str, Any]) -> bool:
    return not bool(result.get("coverage_complete")) or bool(result.get("health_error"))


def build_cli(args: argparse.Namespace) -> CLI:
    workspace = args.workspace or os.environ.get("MULTICA_WORKSPACE_ID")
    if not workspace:
        raise ObserverError("workspace is required; pass --workspace or set MULTICA_WORKSPACE_ID")
    profile = args.profile or os.environ.get("MULTICA_OBSERVER_PROFILE") or os.environ.get("MULTICA_REQUIREMENT_PROFILE")
    return CLI(discover_multica(args.multica_bin), profile, workspace)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--multica-bin")
    root.add_argument("--profile")
    root.add_argument("--workspace")
    sub = root.add_subparsers(dest="command", required=True)

    report = sub.add_parser("report-incident")
    report.add_argument("--source-issue", required=True)
    report.add_argument("--source-requirement")
    report.add_argument("--rule-id", default="WF-SELF-REPORT-001")
    report.add_argument("--severity", choices=["low", "medium", "high", "urgent"], default="medium")
    report.add_argument("--summary", required=True)
    report.add_argument("--expected", required=True)
    report.add_argument("--actual", required=True)
    report.add_argument("--evidence")
    report.add_argument("--entity")
    report.add_argument("--dedupe-key")
    report.add_argument("--protocol-revision")
    report.add_argument("--reporter-agent-id")
    report.add_argument("--reporter-role")
    report.add_argument("--block-source", action="store_true")
    report.add_argument("--notification-cooldown-hours", type=int, default=24)
    report.add_argument("--deterministic-confirmation", action="store_true")
    report.add_argument("--blocked-requirement-count", type=int, default=0)

    audit_parser = sub.add_parser("audit")
    audit_parser.add_argument("--scope", choices=["issues", "health", "all"], default="all")
    audit_parser.add_argument("--report", action="store_true")
    audit_parser.add_argument("--max-issues", type=int, default=5000)
    audit_parser.add_argument("--backlog-hours", type=int, default=24)
    audit_parser.add_argument("--health-max-age-minutes", type=int, default=135)
    audit_parser.add_argument("--coverage-issue")
    audit_parser.add_argument("--output", choices=["json"], default="json")

    health_parser = sub.add_parser("health")
    health_parser.add_argument("--max-age-minutes", type=int, default=135)
    health_parser.add_argument("--output", choices=["json"], default="json")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        cli = build_cli(args)
        if args.command == "report-incident":
            result = report_incident(cli, args)
        elif args.command == "audit":
            result = audit(cli, args)
            if audit_failed(result):
                print(json.dumps(result, ensure_ascii=False, indent=2))
                return 2
        else:
            result = health(cli, args.max_age_minutes)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except ObserverError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
