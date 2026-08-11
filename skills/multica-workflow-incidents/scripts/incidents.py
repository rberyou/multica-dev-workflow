#!/usr/bin/env python3
"""Bind protocol v4 workflow Issues and persist event-driven Incidents."""

from __future__ import annotations

import argparse
import base64
import binascii
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
NON_BLOCKED_ACTIVE_STATUSES = ACTIVE_STATUSES - {"blocked"}
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
INTEGRATION_REVIEW_ACTIONS = {"prepare", "start", "handoff", "approve", "recover"}
HANDOFF_OUTCOMES = {"queued", "coalesced", "deferred"}
RETRYABLE_HANDOFF_OUTCOMES = {"lost", "busy"}
INTEGRATION_REVIEW_BLOCK_WAITING_ON = {
    "integration_review_evidence",
    "integration_reviewer_configuration",
    "integration_review_trigger",
}
INTEGRATION_REVIEW_BLOCK_REASONS = {
    "integration review evidence is stale",
    "integration review role or routing validation failed",
}
METADATA_RECORD_PREFIX = "v1."
MAX_ISSUE_METADATA_KEYS = 50
FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
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


def encode_metadata_record(value: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(canonical_json(value).encode("utf-8"))
    return METADATA_RECORD_PREFIX + payload.decode("ascii").rstrip("=")


def decode_metadata_record(
    value: Any, name: str, record_type: str | None = None
) -> dict[str, Any]:
    if not isinstance(value, str) or not value.startswith(METADATA_RECORD_PREFIX):
        raise IncidentError(f"{name} is not a versioned metadata record")
    encoded = value[len(METADATA_RECORD_PREFIX) :]
    if not encoded or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None:
        raise IncidentError(f"{name} is invalid")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
        record = json.loads(decoded)
    except (UnicodeError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
        raise IncidentError(f"{name} is invalid") from exc
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise IncidentError(f"{name} has an unsupported schema")
    if record_type is not None and record.get("record_type") != record_type:
        raise IncidentError(f"{name} has the wrong record type")
    if encode_metadata_record(record) != value:
        raise IncidentError(f"{name} is not canonical")
    return record


def _require_object(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise IncidentError(f"{name} must be an object")
    return value


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and FULL_SHA_RE.fullmatch(value) is not None


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


def _add(reasons: list[str], condition: bool, message: str) -> None:
    if not condition:
        reasons.append(message)


def _parse_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise IncidentError(f"{name} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise IncidentError(f"{name} is invalid") from exc
    if parsed.tzinfo is None:
        raise IncidentError(f"{name} must include a timezone")
    return parsed


def _metadata_capacity_reasons(
    metadata_keys: Any, update_keys: Any, label: str = "Issue"
) -> list[str]:
    if (
        not isinstance(metadata_keys, list)
        or not all(isinstance(key, str) and bool(key) for key in metadata_keys)
        or len(metadata_keys) != len(set(metadata_keys))
    ):
        return [f"{label} metadata key inventory is invalid"]
    projected = set(metadata_keys)
    projected.update(str(key) for key in update_keys)
    if len(projected) > MAX_ISSUE_METADATA_KEYS:
        return [
            f"{label} metadata updates exceed the platform 50-key limit "
            f"({len(metadata_keys)} current, {len(projected)} projected)"
        ]
    return []


def _record_digest(value: Any, name: str) -> str:
    if not isinstance(value, str):
        raise IncidentError(f"{name} is missing")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _review_binding_digest(record: dict[str, Any]) -> str:
    keys = (
        "schema_version",
        "record_type",
        "workspace_id",
        "squad_id",
        "roster_digest",
        "issue_id",
        "owner_id",
        "reviewer_id",
        "plan_revision",
        "delivery_policy_digest",
        "base_commit_sha",
        "reviewed_commit_sha",
        "dependency_digest",
        "lease_digest",
        "recovery_record_digest",
    )
    return sha256_value({key: record.get(key) for key in keys})


def _review_epoch_id(record: dict[str, Any]) -> str:
    return sha256_value(
        {
            "review_binding_digest": record.get("review_binding_digest"),
            "handoff_comment_id": record.get("handoff_comment_id"),
            "trigger_run_id": record.get("trigger_run_id"),
            "trigger_outcome": record.get("trigger_outcome"),
            "handoff_created_at": record.get("handoff_created_at"),
        }
    )


def _workflow_block_transition_pending(value: Any) -> bool:
    if value in {None, ""}:
        return False
    try:
        record = decode_metadata_record(
            value, "workflow_block_transition_record", "workflow_block_transition"
        )
    except IncidentError:
        return True
    completed = record.get("completed")
    plan = {key: item for key, item in record.items() if key != "completed"}
    try:
        _validate_block_plan(plan)
    except IncidentError:
        return True
    if not isinstance(completed, int) or isinstance(completed, bool):
        return True
    total = len(_block_data_writes(plan))
    return completed < total or completed > total


INTEGRATION_BLOCK_RECORD_KEY = "integration_review_block_transition_record"
INTEGRATION_BLOCK_FIELDS = (
    "status",
    "waiting_on",
    "blocked_reason",
    "integration_review_previous_status",
    INTEGRATION_BLOCK_RECORD_KEY,
)


def _integration_block_projection(issue: dict[str, Any]) -> dict[str, Any]:
    return {key: issue.get(key, "") for key in INTEGRATION_BLOCK_FIELDS}


def _integration_block_apply(
    projection: dict[str, Any], write: dict[str, Any]
) -> dict[str, Any]:
    result = dict(projection)
    if write.get("kind") == "status":
        result["status"] = write.get("value")
    else:
        result[str(write.get("key"))] = write.get("value", "")
    return result


def _integration_block_record_value(plan: dict[str, Any], completed: int) -> str:
    return encode_metadata_record({**plan, "completed": completed})


def _integration_block_data_writes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    target = plan["target"]
    issue_id = plan["issue_id"]
    if plan["direction"] == "block":
        writes = [
            {
                "kind": "status",
                "issue_id": issue_id,
                "value": target["status"],
            }
        ]
        order = ("integration_review_previous_status", "waiting_on", "blocked_reason")
    else:
        writes = []
        order = (
            "waiting_on",
            "blocked_reason",
            "integration_review_previous_status",
        )
    writes.extend(
        [
            {
                "kind": "metadata",
                "issue_id": issue_id,
                "key": key,
                "value": target[key],
            }
            for key in order
        ]
    )
    if plan["direction"] == "restore":
        writes.append(
            {
                "kind": "status",
                "issue_id": issue_id,
                "value": target["status"],
            }
        )
    return writes


def _integration_block_full_writes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    issue_id = plan["issue_id"]
    writes = [
        {
            "kind": "metadata",
            "issue_id": issue_id,
            "key": INTEGRATION_BLOCK_RECORD_KEY,
            "value": _integration_block_record_value(plan, 0),
        }
    ]
    for completed, write in enumerate(_integration_block_data_writes(plan), start=1):
        writes.append(write)
        writes.append(
            {
                "kind": "metadata",
                "issue_id": issue_id,
                "key": INTEGRATION_BLOCK_RECORD_KEY,
                "value": _integration_block_record_value(plan, completed),
            }
        )
    return writes


def _validate_integration_block_plan(plan: dict[str, Any]) -> None:
    if (
        plan.get("schema_version") != 1
        or plan.get("record_type") != "integration_review_block_transition"
    ):
        raise IncidentError("integration Review block transition schema is invalid")
    if not isinstance(plan.get("issue_id"), str) or not plan.get("issue_id"):
        raise IncidentError("integration Review block transition issue_id is missing")
    if plan.get("direction") not in {"block", "restore"}:
        raise IncidentError("integration Review block transition direction is invalid")
    initial = _require_object(
        plan.get("initial"), "integration Review block initial tuple"
    )
    target = _require_object(plan.get("target"), "integration Review block target tuple")
    if initial.get(INTEGRATION_BLOCK_RECORD_KEY) not in {None, ""}:
        raise IncidentError("integration Review block initial record must be empty")
    if plan["direction"] == "block":
        initial_active = initial.get("status") in NON_BLOCKED_ACTIVE_STATUSES
        initial_owned_block = (
            initial.get("status") == "blocked"
            and initial.get("waiting_on") in INTEGRATION_REVIEW_BLOCK_WAITING_ON
            and initial.get("blocked_reason") in INTEGRATION_REVIEW_BLOCK_REASONS
            and initial.get("integration_review_previous_status")
            in NON_BLOCKED_ACTIVE_STATUSES
        )
        if not initial_active and not initial_owned_block:
            raise IncidentError(
                "integration Review block must start active or from its exact blocker"
            )
        if target.get("status") != "blocked":
            raise IncidentError("integration Review block target must be blocked")
        expected_previous = (
            initial.get("status")
            if initial_active
            else initial.get("integration_review_previous_status")
        )
        if target.get("integration_review_previous_status") != expected_previous:
            raise IncidentError("integration Review previous status is invalid")
        if target.get("waiting_on") not in INTEGRATION_REVIEW_BLOCK_WAITING_ON:
            raise IncidentError("integration Review blocker waiting_on is invalid")
        if target.get("blocked_reason") not in INTEGRATION_REVIEW_BLOCK_REASONS:
            raise IncidentError("integration Review blocker reason is invalid")
    else:
        previous = initial.get("integration_review_previous_status")
        if (
            initial.get("status") != "blocked"
            or previous not in NON_BLOCKED_ACTIVE_STATUSES
        ):
            raise IncidentError("integration Review restore source is invalid")
        if target != {
            "status": previous,
            "waiting_on": "",
            "blocked_reason": "",
            "integration_review_previous_status": "",
        }:
            raise IncidentError("integration Review restore target is invalid")


def _integration_block_plan_from_record(value: Any) -> tuple[dict[str, Any], int]:
    record = decode_metadata_record(
        value,
        INTEGRATION_BLOCK_RECORD_KEY,
        "integration_review_block_transition",
    )
    completed = record.get("completed")
    if not isinstance(completed, int) or isinstance(completed, bool) or completed < 0:
        raise IncidentError("integration Review block completed is invalid")
    plan = {key: item for key, item in record.items() if key != "completed"}
    _validate_integration_block_plan(plan)
    if completed > len(_integration_block_data_writes(plan)):
        raise IncidentError("integration Review block completed is out of range")
    return plan, completed


def _integration_block_preflight(
    issue: dict[str, Any], target: dict[str, Any] | None
) -> dict[str, Any]:
    current_record = issue.get(INTEGRATION_BLOCK_RECORD_KEY)
    superseded_complete_record = False
    resuming_existing = False
    requested_target_deferred = False
    if current_record not in {None, ""}:
        plan, completed = _integration_block_plan_from_record(current_record)
        if plan.get("issue_id") != issue.get("issue_id"):
            raise IncidentError("integration Review block record targets another Issue")
        complete = completed == len(_integration_block_data_writes(plan))
        current_without_record = _integration_block_projection(issue)
        current_without_record[INTEGRATION_BLOCK_RECORD_KEY] = ""
        if (
            complete
            and (target is None or target == plan.get("target"))
            and current_without_record
            == {**plan["target"], INTEGRATION_BLOCK_RECORD_KEY: ""}
        ):
            return {
                "allowed": True,
                "outcome": "complete",
                "writes": [],
                "complete": True,
                "target": plan["target"],
                "metadata_keys": [],
                "resuming_existing": False,
                "requested_target_deferred": False,
            }
        if complete:
            superseded_complete_record = True
        else:
            resuming_existing = True
            if target is not None and target != plan.get("target"):
                requested_target_deferred = True
                target = None
    if target is None and (
        current_record in {None, ""} or superseded_complete_record
    ):
        return {
            "allowed": True,
            "outcome": "no_action",
            "writes": [],
            "complete": True,
            "target": None,
            "metadata_keys": [],
            "resuming_existing": False,
            "requested_target_deferred": False,
        }
    if current_record in {None, ""} or superseded_complete_record:
        initial = _integration_block_projection(issue)
        initial[INTEGRATION_BLOCK_RECORD_KEY] = ""
        direction = "block" if target and target.get("status") == "blocked" else "restore"
        plan = {
            "schema_version": 1,
            "record_type": "integration_review_block_transition",
            "issue_id": issue.get("issue_id"),
            "direction": direction,
            "initial": initial,
            "target": target,
        }
    _validate_integration_block_plan(plan)
    metadata_keys = {
        INTEGRATION_BLOCK_RECORD_KEY,
        "waiting_on",
        "blocked_reason",
        "integration_review_previous_status",
    }
    capacity_reasons = _metadata_capacity_reasons(
        issue.get("metadata_keys"), metadata_keys, "integration validation"
    )
    if capacity_reasons:
        return {
            "allowed": False,
            "outcome": "rejected",
            "reasons": capacity_reasons,
            "writes": [],
            "complete": False,
            "target": plan["target"],
            "metadata_keys": metadata_keys,
            "resuming_existing": resuming_existing,
            "requested_target_deferred": requested_target_deferred,
        }
    expected = dict(plan["initial"])
    expected[INTEGRATION_BLOCK_RECORD_KEY] = ""
    current = _integration_block_projection(issue)
    if superseded_complete_record:
        current[INTEGRATION_BLOCK_RECORD_KEY] = ""
    full_writes = _integration_block_full_writes(plan)
    matching = []
    if expected == current:
        matching.append(0)
    for index, write in enumerate(full_writes, start=1):
        expected = _integration_block_apply(expected, write)
        if expected == current:
            matching.append(index)
    if not matching:
        raise IncidentError("integration Review block state is not a valid retry prefix")
    progress = max(matching)
    remaining = full_writes[progress:]
    return {
        "allowed": True,
        "outcome": "complete" if not remaining else "resume_required",
        "writes": remaining,
        "complete": not remaining,
        "target": plan["target"],
        "metadata_keys": metadata_keys,
        "progress": progress,
        "total_writes": len(full_writes),
        "resuming_existing": resuming_existing,
        "requested_target_deferred": requested_target_deferred,
    }


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


def _integration_review_rejected(
    issue: dict[str, Any],
    action: str,
    reasons: list[str],
    *,
    retry_required: bool = False,
    retries_exhausted: bool = False,
    stale_evidence: bool = False,
) -> dict[str, Any]:
    incident_owned = bool(issue.get("workflow_blocked_by_incident_id"))
    transition_pending = _workflow_block_transition_pending(
        issue.get("workflow_block_transition_record")
    )
    reported_reasons = list(reasons)
    block_transition = {
        "allowed": True,
        "outcome": "preserved" if incident_owned or transition_pending else "no_action",
        "writes": [],
        "complete": True,
        "target": None,
    }
    if not incident_owned and not transition_pending:
        current_review_record = issue.get(INTEGRATION_BLOCK_RECORD_KEY)
        if current_review_record not in {None, ""}:
            try:
                record_plan, _completed = _integration_block_plan_from_record(
                    current_review_record
                )
                if record_plan.get("issue_id") != issue.get("issue_id"):
                    raise IncidentError(
                        "integration Review block record targets another Issue"
                    )
            except IncidentError as exc:
                reported_reasons.append(str(exc))
                block_transition = {
                    "allowed": False,
                    "outcome": "rejected",
                    "writes": [],
                    "complete": False,
                    "target": None,
                }
        if block_transition["allowed"] and (not retry_required or retries_exhausted):
            waiting_on = (
                "integration_review_evidence"
                if stale_evidence
                else "integration_reviewer_configuration"
            )
            if action == "handoff":
                waiting_on = "integration_review_trigger"
            reason = (
                "integration review evidence is stale"
                if stale_evidence
                else "integration review role or routing validation failed"
            )
            previous_status = issue.get("integration_review_previous_status")
            owns_existing = (
                issue.get("status") == "blocked"
                and issue.get("waiting_on") in INTEGRATION_REVIEW_BLOCK_WAITING_ON
                and issue.get("blocked_reason") in INTEGRATION_REVIEW_BLOCK_REASONS
                and previous_status in NON_BLOCKED_ACTIVE_STATUSES
            )
            if issue.get("status") in NON_BLOCKED_ACTIVE_STATUSES:
                previous_status = issue.get("status")
            target = None
            if issue.get("status") in NON_BLOCKED_ACTIVE_STATUSES or owns_existing:
                target = {
                    "status": "blocked",
                    "waiting_on": waiting_on,
                    "blocked_reason": reason,
                    "integration_review_previous_status": previous_status,
                }
            try:
                block_transition = _integration_block_preflight(issue, target)
            except IncidentError as exc:
                reported_reasons.append(str(exc))
                block_transition = {
                    "allowed": False,
                    "outcome": "rejected",
                    "writes": [],
                    "complete": False,
                    "target": None,
                }
            if not block_transition["allowed"]:
                reported_reasons.extend(block_transition.get("reasons") or [])
    block_target = (
        block_transition.get("target") if block_transition.get("allowed") else None
    )
    block_updates = {
        key: value
        for key, value in (block_target or {}).items()
        if key != "status"
    }
    block_status = (block_target or {}).get("status")
    return {
        "allowed": False,
        "action": action,
        "outcome": "rejected",
        "reasons": reported_reasons,
        "metadata_updates": {},
        "assignee_write": None,
        "status_write": None,
        "retry_required": retry_required and not retries_exhausted,
        "retries_exhausted": retries_exhausted,
        "block_metadata_updates": block_updates,
        "block_status_write": block_status,
        "block_writes": block_transition.get("writes") or [],
        "block_transition_complete": block_transition.get("complete") is True,
        "block_transition_outcome": block_transition.get("outcome"),
        "incident_blocker_preserved": incident_owned,
        "block_transition_preserved": transition_pending,
    }


def _integration_review_restore_target(
    issue: dict[str, Any]
) -> dict[str, Any] | None:
    if issue.get("workflow_blocked_by_incident_id") or _workflow_block_transition_pending(
        issue.get("workflow_block_transition_record")
    ):
        return None
    previous_status = issue.get("integration_review_previous_status")
    if (
        issue.get("status") != "blocked"
        or previous_status not in NON_BLOCKED_ACTIVE_STATUSES
        or issue.get("waiting_on") not in INTEGRATION_REVIEW_BLOCK_WAITING_ON
        or issue.get("blocked_reason") not in INTEGRATION_REVIEW_BLOCK_REASONS
    ):
        return None
    return {
        "status": str(previous_status),
        "waiting_on": "",
        "blocked_reason": "",
        "integration_review_previous_status": "",
    }


def _integration_review_allowed(
    issue: dict[str, Any],
    action: str,
    outcome: str,
    updates: dict[str, Any],
    assignee_write: str | None,
) -> dict[str, Any]:
    restore_target = _integration_review_restore_target(issue)
    try:
        block_transition = _integration_block_preflight(issue, restore_target)
    except IncidentError as exc:
        return _integration_review_rejected(issue, action, [str(exc)])
    if not block_transition["allowed"]:
        return _integration_review_rejected(
            issue, action, list(block_transition.get("reasons") or [])
        )
    if block_transition.get("resuming_existing") and block_transition.get("writes"):
        block_target = block_transition.get("target") or {}
        return {
            "allowed": False,
            "action": action,
            "outcome": "block_transition_resume_required",
            "reasons": [
                "finish the recorded integration Review blocker transition and rerun the action"
            ],
            "metadata_updates": {},
            "assignee_write": None,
            "status_write": None,
            "retry_required": False,
            "retries_exhausted": False,
            "block_metadata_updates": {
                key: value for key, value in block_target.items() if key != "status"
            },
            "block_status_write": block_target.get("status"),
            "block_writes": block_transition["writes"],
            "block_transition_complete": False,
            "block_transition_outcome": block_transition.get("outcome"),
            "incident_blocker_preserved": bool(
                issue.get("workflow_blocked_by_incident_id")
            ),
            "block_transition_preserved": _workflow_block_transition_pending(
                issue.get("workflow_block_transition_record")
            ),
        }
    capacity_reasons = _metadata_capacity_reasons(
        issue.get("metadata_keys"),
        [*updates, *block_transition.get("metadata_keys", [])],
        "integration validation",
    )
    if capacity_reasons:
        return _integration_review_rejected(issue, action, capacity_reasons)
    return {
        "allowed": True,
        "action": action,
        "outcome": outcome,
        "reasons": [],
        "metadata_updates": updates,
        "assignee_write": assignee_write,
        "status_write": None,
        "retry_required": False,
        "retries_exhausted": False,
        "block_metadata_updates": (
            {
                key: value
                for key, value in (block_transition.get("target") or {}).items()
                if key != "status"
            }
        ),
        "block_status_write": (block_transition.get("target") or {}).get("status"),
        "block_writes": block_transition["writes"],
        "block_transition_complete": block_transition["complete"],
        "block_transition_outcome": block_transition.get("outcome"),
        "incident_blocker_preserved": bool(
            issue.get("workflow_blocked_by_incident_id")
        ),
        "block_transition_preserved": _workflow_block_transition_pending(
            issue.get("workflow_block_transition_record")
        ),
    }


def _active_roster(
    context: dict[str, Any]
) -> tuple[str, str, str, str, str]:
    workspace_id = context.get("workspace_id")
    squad_id = context.get("squad_id")
    if not isinstance(workspace_id, str) or not workspace_id:
        raise IncidentError("context.workspace_id is missing")
    if not isinstance(squad_id, str) or not squad_id:
        raise IncidentError("context.squad_id is missing")
    roster = context.get("roster")
    if not isinstance(roster, list):
        raise IncidentError("context.roster must be a list")
    if context.get("roster_complete") is not True:
        raise IncidentError("context.roster must be declared complete")
    normalized = []
    integrators = []
    reviewers = []
    for item in roster:
        if not isinstance(item, dict):
            raise IncidentError("context.roster contains an invalid member")
        member = {
            "agent_id": item.get("agent_id"),
            "member_type": item.get("member_type"),
            "role_key": item.get("role_key"),
            "active": item.get("active") is True,
            "archived": item.get("archived") is True,
        }
        if not isinstance(member["agent_id"], str) or not member["agent_id"]:
            raise IncidentError("context.roster member agent_id is missing")
        normalized.append(member)
        if (
            member["member_type"] == "agent"
            and member["active"]
            and not member["archived"]
        ):
            if member["role_key"] == "integrator":
                integrators.append(member["agent_id"])
            if member["role_key"] == "code_reviewer":
                reviewers.append(member["agent_id"])
    if len(integrators) != 1:
        raise IncidentError(
            f"expected one active Integrator in the current roster, found {len(integrators)}"
        )
    if len(reviewers) != 1:
        raise IncidentError(
            f"expected one active Code Reviewer in the current roster, found {len(reviewers)}"
        )
    normalized.sort(key=lambda item: canonical_json(item))
    roster_digest = sha256_value(
        {"workspace_id": workspace_id, "squad_id": squad_id, "roster": normalized}
    )
    return workspace_id, squad_id, roster_digest, integrators[0], reviewers[0]


def _integration_review_tuple(
    data: dict[str, Any], action: str
) -> tuple[
    dict[str, Any],
    dict[str, Any],
    str,
    str,
    str,
    str,
    str,
    str,
]:
    context = _require_object(data.get("context"), "integration review context")
    issue = _require_object(data.get("issue"), "integration review issue")
    workspace_id, squad_id, roster_digest, owner_id, reviewer_id = _active_roster(
        context
    )
    reasons: list[str] = []
    issue_id = issue.get("issue_id")
    _add(reasons, isinstance(issue_id, str) and bool(issue_id), "issue_id is missing")
    _add(
        reasons,
        issue.get("workflow_id") == WORKFLOW_ID,
        "integration validation is not bound to this workflow",
    )
    _add(
        reasons,
        issue.get("protocol_revision") == PROTOCOL_REVISION,
        "integration validation does not use protocol v4",
    )
    _add(
        reasons,
        issue.get("workflow_object_type") == "integration_validation",
        "review target is not an integration validation",
    )
    _add(
        reasons,
        issue.get("workflow_instance_id") == workspace_id,
        "integration validation workspace binding drifted",
    )
    _add(reasons, owner_id != reviewer_id, "Integrator and Code Reviewer must be different")
    revision = issue.get("plan_revision")
    _add(
        reasons,
        isinstance(revision, int) and not isinstance(revision, bool) and revision > 0,
        "plan_revision must be a positive integer",
    )
    _add(
        reasons,
        _is_digest(issue.get("delivery_policy_digest")),
        "delivery_policy_digest is invalid",
    )
    _add(reasons, _is_sha(issue.get("base_commit_sha")), "base_commit_sha is invalid")
    _add(
        reasons,
        _is_sha(issue.get("reviewed_commit_sha")),
        "reviewed_commit_sha is invalid",
    )
    dependency = _require_object(
        data.get("dependency"), "integration review dependency evidence"
    )
    _add(
        reasons,
        dependency.get("satisfied") is True,
        "dependency contract is not satisfied",
    )
    _add(
        reasons,
        isinstance(dependency.get("contract"), str)
        and bool(dependency.get("contract")),
        "dependency contract is missing",
    )
    lease = _require_object(data.get("lease"), "integration review lease evidence")
    lease_target = lease
    if action == "recover":
        _require_object(lease.get("current"), "recovery current lease evidence")
        lease_target = _require_object(
            lease.get("target"), "recovery target lease evidence"
        )
    _add(
        reasons,
        lease_target.get("scope") == "requirement",
        "integration review lease scope must be requirement",
    )
    _add(reasons, lease_target.get("state") == "held", "integration review lease is not held")
    _add(
        reasons,
        lease_target.get("owner_issue_id") == issue_id,
        "integration review lease owner Issue is stale",
    )
    _add(
        reasons,
        lease_target.get("owner_agent_id") == owner_id,
        "integration review lease owner must be the Integrator",
    )
    if action not in {"prepare", "recover"}:
        _add(
            reasons,
            issue.get("assignee_id") == owner_id,
            "integration validation assignee must remain the Integrator",
        )
        _add(
            reasons,
            issue.get("original_owner_id") == owner_id,
            "integration validation original owner must be the Integrator",
        )
        _add(
            reasons,
            issue.get("reviewer_id") == reviewer_id,
            "integration validation reviewer must be the Code Reviewer",
        )
    if reasons:
        raise IncidentError("; ".join(reasons))
    return (
        context,
        issue,
        workspace_id,
        squad_id,
        roster_digest,
        owner_id,
        reviewer_id,
        sha256_value(dependency),
    )


def _role_record_base(
    data: dict[str, Any],
    issue: dict[str, Any],
    workspace_id: str,
    squad_id: str,
    roster_digest: str,
    owner_id: str,
    reviewer_id: str,
    dependency_digest: str,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "record_type": "integration_review_role",
        "workspace_id": workspace_id,
        "squad_id": squad_id,
        "roster_digest": roster_digest,
        "issue_id": issue["issue_id"],
        "owner_id": owner_id,
        "reviewer_id": reviewer_id,
        "plan_revision": issue["plan_revision"],
        "delivery_policy_digest": issue["delivery_policy_digest"],
        "base_commit_sha": issue["base_commit_sha"],
        "reviewed_commit_sha": issue["reviewed_commit_sha"],
        "dependency_digest": dependency_digest,
        "lease_digest": sha256_value(data["lease"]),
    }


def _validate_role_record(
    value: Any, expected: dict[str, Any], states: set[str]
) -> dict[str, Any]:
    record = decode_metadata_record(
        value, "integration_review_role_record", "integration_review_role"
    )
    for key, expected_value in expected.items():
        if record.get(key) != expected_value:
            raise IncidentError(f"integration_review_role_record {key} is stale")
    if record.get("state") not in states:
        raise IncidentError("integration_review_role_record state is invalid for this action")
    recovery_record = record.get("recovery_record_digest")
    if recovery_record is not None and not _is_digest(recovery_record):
        raise IncidentError("integration_review_role_record recovery binding is invalid")
    if record.get("state") in {"handed_off", "approved"}:
        if record.get("review_binding_digest") != _review_binding_digest(record):
            raise IncidentError("integration_review_role_record review binding is invalid")
        if record.get("review_epoch_id") != _review_epoch_id(record):
            raise IncidentError("integration_review_role_record review epoch is invalid")
    return record


def integration_review_transition(snapshot: Any, action: str) -> dict[str, Any]:
    if action not in INTEGRATION_REVIEW_ACTIONS:
        raise IncidentError(f"unsupported integration review action: {action}")
    data = _require_object(snapshot, "integration review snapshot")
    issue = _require_object(data.get("issue"), "integration review issue")
    try:
        (
            _context,
            issue,
            workspace_id,
            squad_id,
            roster_digest,
            owner_id,
            reviewer_id,
            dependency_digest,
        ) = _integration_review_tuple(data, action)
    except IncidentError as exc:
        return _integration_review_rejected(issue, action, [str(exc)])
    base = _role_record_base(
        data,
        issue,
        workspace_id,
        squad_id,
        roster_digest,
        owner_id,
        reviewer_id,
        dependency_digest,
    )
    current_value = issue.get("integration_review_role_record")
    updates: dict[str, Any]
    assignee_write: str | None = None
    outcome = "allowed"

    if action == "prepare":
        record = {**base, "state": "prepared"}
        if current_value not in {None, ""}:
            try:
                current = _validate_role_record(
                    current_value,
                    base,
                    {"prepared", "started", "recovered", "handed_off", "approved"},
                )
            except IncidentError as exc:
                return _integration_review_rejected(issue, action, [str(exc)])
            record = current
            outcome = "already_prepared"
        updates = {
            "original_owner_id": owner_id,
            "reviewer_id": reviewer_id,
            "integration_review_role_record": encode_metadata_record(record),
        }
        assignee_write = owner_id if issue.get("assignee_id") != owner_id else None
    elif action == "recover":
        recovery = _require_object(data.get("recovery"), "integration review recovery")
        reasons: list[str] = []
        _add(
            reasons,
            isinstance(recovery.get("recovery_id"), str)
            and bool(recovery.get("recovery_id")),
            "recovery_id is missing",
        )
        _add(
            reasons,
            isinstance(recovery.get("incident_id"), str)
            and bool(recovery.get("incident_id")),
            "recovery incident_id is missing",
        )
        _add(
            reasons,
            issue.get("workflow_blocked_by_incident_id") == recovery.get("incident_id"),
            "recovery is not bound to the active workflow Incident",
        )
        _add(
            reasons,
            issue.get("status") == "blocked",
            "legacy recovery target must remain blocked during preflight",
        )
        if reasons:
            return _integration_review_rejected(issue, action, reasons, stale_evidence=True)
        current_recovery_binding = {
            "schema_version": 1,
            "record_type": "integration_review_recovery",
            "workspace_id": workspace_id,
            "squad_id": squad_id,
            "roster_digest": roster_digest,
            "issue_id": issue["issue_id"],
            "incident_id": recovery["incident_id"],
            "recovery_id": recovery["recovery_id"],
            "owner_id": owner_id,
            "reviewer_id": reviewer_id,
            "plan_revision": issue["plan_revision"],
            "delivery_policy_digest": issue["delivery_policy_digest"],
            "base_commit_sha": issue["base_commit_sha"],
            "reviewed_commit_sha": issue["reviewed_commit_sha"],
            "dependency_digest": dependency_digest,
            "lease_digest": sha256_value(data["lease"]["target"]),
            "lease_transition_digest": sha256_value(data["lease"]),
            "blocker_digest": sha256_value(
                {
                    key: issue.get(key)
                    for key in (
                        "status",
                        "workflow_blocked_by_incident_id",
                        "workflow_blocked_previous_status",
                        "waiting_on",
                        "blocked_reason",
                    )
                }
            ),
        }
        existing_recovery = issue.get("integration_review_recovery_record")
        if existing_recovery not in {None, ""}:
            try:
                recovery_record = decode_metadata_record(
                    existing_recovery,
                    "integration_review_recovery_record",
                    "integration_review_recovery",
                )
            except IncidentError as exc:
                return _integration_review_rejected(issue, action, [str(exc)])
            for key, value in current_recovery_binding.items():
                if recovery_record.get(key) != value:
                    return _integration_review_rejected(
                        issue,
                        action,
                        [f"integration_review_recovery_record {key} is stale"],
                    )
            for key in (
                "from_assignee_id",
                "from_original_owner_id",
                "from_reviewer_id",
            ):
                if not isinstance(recovery_record.get(key), str) or not recovery_record.get(
                    key
                ):
                    return _integration_review_rejected(
                        issue,
                        action,
                        [f"integration_review_recovery_record {key} is invalid"],
                    )
            recovery_value = existing_recovery
            outcome = "already_recovered"
        else:
            recovery_record = {
                **current_recovery_binding,
                "from_assignee_id": issue.get("assignee_id"),
                "from_original_owner_id": issue.get("original_owner_id"),
                "from_reviewer_id": issue.get("reviewer_id"),
            }
            recovery_value = encode_metadata_record(recovery_record)
        record = {
            **base,
            "lease_digest": sha256_value(data["lease"]["target"]),
            "state": "recovered",
            "recovery_record_digest": _record_digest(
                recovery_value, "integration_review_recovery_record"
            ),
        }
        if existing_recovery not in {None, ""}:
            record["recovery_record_digest"] = _record_digest(
                recovery_value, "integration_review_recovery_record"
            )
            existing_role = issue.get("integration_review_role_record")
            if existing_role not in {None, ""}:
                try:
                    record = _validate_role_record(
                        existing_role,
                        {key: value for key, value in record.items() if key != "state"},
                        {"recovered", "started", "handed_off", "approved"},
                    )
                except IncidentError as exc:
                    return _integration_review_rejected(issue, action, [str(exc)])
        updates = {
            "original_owner_id": owner_id,
            "reviewer_id": reviewer_id,
            "integration_review_role_record": encode_metadata_record(record),
            "integration_review_recovery_record": recovery_value,
        }
        assignee_write = owner_id if issue.get("assignee_id") != owner_id else None
    else:
        states = {"prepared", "started", "recovered", "handed_off", "approved"}
        if action == "approve":
            states = {"handed_off", "approved"}
        elif action == "handoff":
            states = {"started", "handed_off", "approved"}
        try:
            current = _validate_role_record(current_value, base, states)
        except IncidentError as exc:
            return _integration_review_rejected(
                issue, action, [str(exc)], stale_evidence=action == "approve"
            )
        if current.get("recovery_record_digest") is not None:
            recovery_value = issue.get("integration_review_recovery_record")
            try:
                recovery_digest = _record_digest(
                    recovery_value, "integration_review_recovery_record"
                )
            except IncidentError as exc:
                return _integration_review_rejected(issue, action, [str(exc)])
            if current["recovery_record_digest"] != recovery_digest:
                return _integration_review_rejected(
                    issue, action, ["integration recovery evidence is stale"]
                )
        if action == "start":
            if current.get("state") in {"started", "handed_off", "approved"}:
                outcome = "already_started"
                updates = {}
            else:
                record = {**current, "state": "started"}
                outcome = "started"
                updates = {"integration_review_role_record": encode_metadata_record(record)}
        elif action == "handoff":
            handoff = _require_object(data.get("handoff"), "integration review handoff")
            attempt = handoff.get("attempt")
            max_attempts = handoff.get("max_attempts")
            previous_attempts = handoff.get("previous_attempts")
            reasons: list[str] = []
            _add(
                reasons,
                isinstance(attempt, int)
                and not isinstance(attempt, bool)
                and attempt > 0,
                "handoff attempt must be a positive integer",
            )
            _add(
                reasons,
                isinstance(max_attempts, int)
                and not isinstance(max_attempts, bool)
                and 1 <= max_attempts <= 3,
                "handoff max_attempts must be between 1 and 3",
            )
            if isinstance(attempt, int) and isinstance(max_attempts, int):
                _add(reasons, attempt <= max_attempts, "handoff attempt exceeds max_attempts")
            _add(
                reasons,
                isinstance(previous_attempts, list),
                "handoff previous_attempts must be a list",
            )
            _add(
                reasons,
                handoff.get("previous_attempts_complete") is True,
                "handoff previous_attempts must be declared complete",
            )
            if isinstance(previous_attempts, list):
                normalized_attempts = []
                for expected_attempt, item in enumerate(previous_attempts, start=1):
                    if not isinstance(item, dict):
                        reasons.append("handoff previous_attempts contains an invalid entry")
                        continue
                    comment_id = item.get("comment_id")
                    run_id = item.get("trigger_run_id")
                    prior_attempt = item.get("attempt")
                    prior_outcome = item.get("trigger_outcome")
                    if not isinstance(comment_id, str) or not comment_id:
                        reasons.append("prior handoff comment_id is missing")
                    if not isinstance(run_id, str) or not run_id:
                        reasons.append("prior handoff trigger_run_id is missing")
                    if prior_attempt != expected_attempt:
                        reasons.append("prior handoff attempt sequence is invalid")
                    if prior_outcome not in RETRYABLE_HANDOFF_OUTCOMES:
                        reasons.append("prior handoff outcome is not retryable")
                    normalized_attempts.append((comment_id, run_id))
                if len(normalized_attempts) != len(set(normalized_attempts)):
                    reasons.append("handoff previous_attempts contains duplicates")
                if isinstance(attempt, int):
                    _add(
                        reasons,
                        attempt == len(previous_attempts) + 1,
                        "handoff attempt does not follow canonical retry history",
                    )
            _add(
                reasons,
                handoff.get("issue_id") == issue.get("issue_id"),
                "handoff comment must target the integration validation",
            )
            _add(
                reasons,
                handoff.get("author_type") == "agent",
                "handoff comment author_type must be agent",
            )
            _add(
                reasons,
                handoff.get("author_id") == owner_id,
                "handoff comment author must be the Integrator",
            )
            _add(
                reasons,
                handoff.get("mentioned_agent_id") == reviewer_id,
                "handoff must mention the Code Reviewer by exact agent ID",
            )
            _add(
                reasons,
                isinstance(handoff.get("comment_id"), str)
                and bool(handoff.get("comment_id")),
                "handoff comment_id is missing",
            )
            _add(
                reasons,
                isinstance(handoff.get("trigger_run_id"), str)
                and bool(handoff.get("trigger_run_id")),
                "handoff trigger_run_id is missing",
            )
            if isinstance(previous_attempts, list):
                current_attempt = (
                    handoff.get("comment_id"),
                    handoff.get("trigger_run_id"),
                )
                if current_attempt in {
                    (item.get("comment_id"), item.get("trigger_run_id"))
                    for item in previous_attempts
                    if isinstance(item, dict)
                }:
                    reasons.append("handoff current attempt duplicates prior retry evidence")
            try:
                handoff_time = _parse_timestamp(
                    handoff.get("created_at"), "handoff created_at"
                )
            except IncidentError as exc:
                reasons.append(str(exc))
                handoff_time = None
            matched_outcome = None
            outcomes = handoff.get("trigger_outcomes")
            matching_outcomes = []
            if isinstance(outcomes, list):
                for item in outcomes:
                    if not isinstance(item, dict):
                        continue
                    recipient = item.get("recipient_id") or item.get("agent_id")
                    outcome_value = item.get("status") or item.get("outcome")
                    run_id = item.get("run_id") or item.get("task_id")
                    if recipient == reviewer_id and run_id == handoff.get("trigger_run_id"):
                        matching_outcomes.append(outcome_value)
            else:
                reasons.append("handoff trigger_outcomes must be a list")
            if len(matching_outcomes) > 1:
                reasons.append(
                    "trigger_outcomes contains duplicate or conflicting Code Reviewer routing"
                )
            elif len(matching_outcomes) == 1 and matching_outcomes[0] in HANDOFF_OUTCOMES:
                matched_outcome = matching_outcomes[0]
            if matched_outcome is None and len(matching_outcomes) <= 1:
                reasons.append(
                    "trigger_outcomes did not confirm queued, coalesced, or deferred Code Reviewer routing"
                )
            if reasons:
                exhausted = (
                    isinstance(attempt, int)
                    and isinstance(max_attempts, int)
                    and attempt >= max_attempts
                )
                return _integration_review_rejected(
                    issue,
                    action,
                    reasons,
                    retry_required=True,
                    retries_exhausted=exhausted,
                )
            same_handoff_identity = (
                current.get("handoff_comment_id") == handoff.get("comment_id")
                and current.get("trigger_run_id") == handoff.get("trigger_run_id")
            )
            same_handoff = (
                same_handoff_identity
                and current.get("trigger_outcome") == matched_outcome
                and current.get("handoff_created_at") == handoff.get("created_at")
            )
            if current.get("state") == "approved" and not same_handoff:
                return _integration_review_rejected(
                    issue,
                    action,
                    ["a new handoff cannot replace an approved review epoch"],
                    stale_evidence=True,
                )
            if same_handoff_identity and not same_handoff:
                return _integration_review_rejected(
                    issue,
                    action,
                    ["handoff evidence conflicts with the recorded comment and run"],
                    stale_evidence=True,
                )
            if current.get("state") in {"handed_off", "approved"} and same_handoff:
                return _integration_review_allowed(
                    issue,
                    action,
                    "already_handed_off",
                    {},
                    None,
                )
            record = {
                **current,
                "state": "handed_off",
                "handoff_comment_id": handoff["comment_id"],
                "handoff_created_at": handoff["created_at"],
                "trigger_run_id": handoff["trigger_run_id"],
                "trigger_outcome": matched_outcome,
            }
            record["review_binding_digest"] = _review_binding_digest(record)
            record["review_epoch_id"] = _review_epoch_id(record)
            updates = {"integration_review_role_record": encode_metadata_record(record)}
            outcome = "reviewer_handoff_confirmed"
        else:
            review = _require_object(data.get("review"), "integration review evidence")
            if current.get("state") == "approved":
                replay_matches = (
                    review.get("issue_id") == issue.get("issue_id")
                    and review.get("author_type") == "agent"
                    and review.get("author_id") == current.get("review_author_id")
                    and review.get("comment_id") == current.get("review_comment_id")
                    and review.get("created_at") == current.get("review_created_at")
                    and review.get("verdict") == "APPROVED"
                    and review.get("review_epoch_id") == current.get("review_epoch_id")
                    and review.get("trigger_comment_id")
                    == current.get("handoff_comment_id")
                    and review.get("source_run_id") == current.get("trigger_run_id")
                )
                if replay_matches:
                    return _integration_review_allowed(
                        issue,
                        action,
                        "already_approved",
                        {},
                        None,
                    )
                return _integration_review_rejected(
                    issue,
                    action,
                    ["a different Review comment cannot replace the approved review epoch"],
                    stale_evidence=True,
                )
            reasons: list[str] = []
            _add(
                reasons,
                review.get("issue_id") == issue.get("issue_id"),
                "Review comment must target the integration validation",
            )
            _add(reasons, review.get("author_type") == "agent", "Review author must be an agent")
            _add(
                reasons,
                review.get("author_id") == reviewer_id,
                "Review author does not match the current Code Reviewer",
            )
            _add(reasons, review.get("verdict") == "APPROVED", "Review verdict is not APPROVED")
            _add(
                reasons,
                isinstance(review.get("comment_id"), str)
                and bool(review.get("comment_id")),
                "Review comment_id is missing",
            )
            _add(
                reasons,
                review.get("review_epoch_id") == current.get("review_epoch_id"),
                "Review evidence is not bound to the current review epoch",
            )
            _add(
                reasons,
                review.get("trigger_comment_id") == current.get("handoff_comment_id"),
                "Review evidence is not bound to the current handoff comment",
            )
            _add(
                reasons,
                review.get("source_run_id") == current.get("trigger_run_id"),
                "Review evidence is not bound to the current trigger run",
            )
            used = data.get("used_review_comment_ids", [])
            _add(
                reasons,
                data.get("used_review_comment_ids_complete") is True,
                "used_review_comment_ids must be declared complete",
            )
            _add(
                reasons,
                isinstance(used, list)
                and all(isinstance(item, str) for item in used),
                "used_review_comment_ids is invalid",
            )
            if isinstance(used, list):
                _add(
                    reasons,
                    review.get("comment_id") not in used,
                    "Review comment was already consumed by an older review epoch",
                )
            try:
                review_time = _parse_timestamp(review.get("created_at"), "Review created_at")
                handoff_time = _parse_timestamp(
                    current.get("handoff_created_at"), "recorded handoff created_at"
                )
                _add(
                    reasons,
                    review_time > handoff_time,
                    "Review comment predates or coincides with the current handoff",
                )
            except IncidentError as exc:
                reasons.append(str(exc))
            if reasons:
                return _integration_review_rejected(
                    issue, action, reasons, stale_evidence=True
                )
            record = {
                **current,
                "state": "approved",
                "review_comment_id": review["comment_id"],
                "review_author_id": review["author_id"],
                "review_created_at": review["created_at"],
            }
            updates = {"integration_review_role_record": encode_metadata_record(record)}
            outcome = "review_approved"

    return _integration_review_allowed(
        issue,
        action,
        outcome,
        updates,
        assignee_write,
    )


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


BLOCKER_FIELDS = (
    "status",
    "workflow_blocked_by_incident_id",
    "workflow_blocked_previous_status",
    "waiting_on",
    "blocked_reason",
    "workflow_block_transition_record",
)


def _block_projection(source: dict[str, Any]) -> dict[str, Any]:
    return {key: source.get(key, "") for key in BLOCKER_FIELDS}


def _apply_projected_write(
    projection: dict[str, Any], write: dict[str, Any]
) -> dict[str, Any]:
    result = dict(projection)
    if write.get("kind") == "status":
        result["status"] = write.get("value")
    else:
        result[str(write.get("key"))] = write.get("value")
    return result


def _block_record_value(plan: dict[str, Any], completed: int) -> str:
    return encode_metadata_record({**plan, "completed": completed})


def _block_data_writes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    source_id = plan["source_issue_id"]
    target = plan["target"]
    writes: list[dict[str, Any]] = []
    if target["status"] == "blocked":
        writes.extend(
            [
                {
                    "kind": "metadata",
                    "issue_id": source_id,
                    "key": "waiting_on",
                    "value": target["waiting_on"],
                },
                {
                    "kind": "metadata",
                    "issue_id": source_id,
                    "key": "blocked_reason",
                    "value": target["blocked_reason"],
                },
                {
                    "kind": "metadata",
                    "issue_id": source_id,
                    "key": "workflow_blocked_by_incident_id",
                    "value": "",
                },
                {
                    "kind": "metadata",
                    "issue_id": source_id,
                    "key": "workflow_blocked_previous_status",
                    "value": "",
                },
            ]
        )
    else:
        writes.extend(
            [
                {
                    "kind": "metadata",
                    "issue_id": source_id,
                    "key": "workflow_blocked_by_incident_id",
                    "value": "",
                },
                {
                    "kind": "metadata",
                    "issue_id": source_id,
                    "key": "workflow_blocked_previous_status",
                    "value": "",
                },
                {
                    "kind": "metadata",
                    "issue_id": source_id,
                    "key": "waiting_on",
                    "value": "",
                },
                {
                    "kind": "metadata",
                    "issue_id": source_id,
                    "key": "blocked_reason",
                    "value": "",
                },
                {
                    "kind": "status",
                    "issue_id": source_id,
                    "value": target["status"],
                },
            ]
        )
    return writes


def _block_full_writes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    source_id = plan["source_issue_id"]
    writes: list[dict[str, Any]] = [
        {
            "kind": "metadata",
            "issue_id": source_id,
            "key": "workflow_block_transition_record",
            "value": _block_record_value(plan, 0),
        }
    ]
    for completed, write in enumerate(_block_data_writes(plan), start=1):
        writes.append(write)
        writes.append(
            {
                "kind": "metadata",
                "issue_id": source_id,
                "key": "workflow_block_transition_record",
                "value": _block_record_value(plan, completed),
            }
        )
    return writes


def _block_plan_from_record(value: Any) -> tuple[dict[str, Any], int]:
    record = decode_metadata_record(
        value, "workflow_block_transition_record", "workflow_block_transition"
    )
    completed = record.get("completed")
    if not isinstance(completed, int) or isinstance(completed, bool) or completed < 0:
        raise IncidentError("workflow_block_transition_record completed is invalid")
    plan = {key: item for key, item in record.items() if key != "completed"}
    _validate_block_plan(plan)
    if completed > len(_block_data_writes(plan)):
        raise IncidentError("workflow_block_transition_record completed is out of range")
    return plan, completed


def _validate_block_plan(plan: dict[str, Any]) -> None:
    if plan.get("schema_version") != 1 or plan.get("record_type") != "workflow_block_transition":
        raise IncidentError("workflow block transition record schema is invalid")
    if not isinstance(plan.get("incident_id"), str) or not plan.get("incident_id"):
        raise IncidentError("workflow block transition incident_id is missing")
    if not isinstance(plan.get("source_issue_id"), str) or not plan.get("source_issue_id"):
        raise IncidentError("workflow block transition source_issue_id is missing")
    if plan.get("source_issue_id") == plan.get("incident_id"):
        raise IncidentError("workflow block transition source cannot be the Incident")
    initial = _require_object(plan.get("initial"), "workflow block transition initial tuple")
    target = _require_object(plan.get("target"), "workflow block transition target tuple")
    if initial.get("status") != "blocked":
        raise IncidentError("workflow block transition must start from blocked")
    if initial.get("workflow_blocked_by_incident_id") != plan.get("incident_id"):
        raise IncidentError("workflow block transition initial owner is invalid")
    if initial.get("waiting_on") != "workflow_fix":
        raise IncidentError("workflow block transition initial waiting_on is invalid")
    if initial.get("blocked_reason") != f"workflow Incident {plan.get('incident_id')}":
        raise IncidentError("workflow block transition initial blocked_reason is invalid")
    if initial.get("workflow_blocked_previous_status") not in NON_BLOCKED_ACTIVE_STATUSES:
        raise IncidentError("workflow block transition previous status is invalid")
    if initial.get("workflow_block_transition_record") not in {None, ""}:
        raise IncidentError("workflow block transition initial record must be empty")
    if target.get("status") == "blocked":
        if not isinstance(target.get("waiting_on"), str) or not target.get("waiting_on"):
            raise IncidentError("successor blocker waiting_on is missing")
        if not isinstance(target.get("blocked_reason"), str) or not target.get("blocked_reason"):
            raise IncidentError("successor blocker reason is missing")
    elif target.get("status") in NON_BLOCKED_ACTIVE_STATUSES:
        if target.get("waiting_on") not in {None, ""} or target.get("blocked_reason") not in {None, ""}:
            raise IncidentError("restored target must not retain blocker fields")
    else:
        raise IncidentError("workflow block transition target status is invalid")
    binding = _require_object(plan.get("binding"), "workflow block transition binding")
    if sha256_value(binding) != plan.get("binding_digest"):
        raise IncidentError("workflow block transition binding digest is invalid")
    for key in (
        "workflow_instance_id",
        "fixed_source_commit",
        "deployment_plan_digest",
    ):
        if not isinstance(binding.get(key), str) or not binding.get(key):
            raise IncidentError(f"workflow block transition binding {key} is missing")
    if not _is_sha(binding.get("fixed_source_commit")):
        raise IncidentError("workflow block transition fixed source commit is invalid")
    if not _is_digest(binding.get("deployment_plan_digest")):
        raise IncidentError("workflow block transition deployment Plan digest is invalid")
    if binding.get("plan_revision") not in {None, ""}:
        revision = binding.get("plan_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
            raise IncidentError("workflow block transition Plan revision is invalid")
    for key in (
        "delivery_policy_digest",
        "integration_review_role_record_digest",
        "integration_review_recovery_record_digest",
        "workspace_lease_transition_record_digest",
    ):
        if binding.get(key) not in {None, ""} and not _is_digest(binding.get(key)):
            raise IncidentError(f"workflow block transition binding {key} is invalid")
    for key in ("base_commit_sha", "reviewed_commit_sha"):
        if binding.get(key) not in {None, ""} and not _is_sha(binding.get(key)):
            raise IncidentError(f"workflow block transition binding {key} is invalid")
    if binding.get("integration_review_recovery_record_digest") not in {None, ""}:
        if not _is_digest(binding.get("integration_review_role_record_digest")):
            raise IncidentError(
                "workflow block transition recovered Review role binding is missing"
            )
        if not _is_digest(binding.get("workspace_lease_transition_record_digest")):
            raise IncidentError(
                "workflow block transition recovered lease transition binding is missing"
            )
        if binding.get("workspace_lease_transition_complete") is not True:
            raise IncidentError(
                "workflow block transition requires a completed recovered lease transition"
            )
        if binding.get("integration_review_recovery_approved") is not True:
            raise IncidentError(
                "workflow block transition requires a fresh approved recovery Review"
            )
        if binding.get("integration_review_recovery_incident_id") != plan.get(
            "incident_id"
        ):
            raise IncidentError(
                "workflow block transition recovery is bound to a different Incident"
            )


def block_transition_preflight(snapshot: Any) -> dict[str, Any]:
    data = _require_object(snapshot, "workflow block transition snapshot")
    incident = _require_object(data.get("incident"), "workflow block transition incident")
    source = _require_object(data.get("source"), "workflow block transition source")
    incident_id = incident.get("issue_id")
    source_id = source.get("issue_id")
    if not isinstance(incident_id, str) or not incident_id:
        raise IncidentError("incident issue_id is missing")
    if incident.get("incident_status") not in {"open", "in_fix"}:
        raise IncidentError("workflow Incident is not active")
    if not isinstance(source_id, str) or not source_id:
        raise IncidentError("source issue_id is missing")
    current_record = source.get("workflow_block_transition_record")
    superseded_complete_record = False
    if current_record not in {None, ""}:
        recorded_plan, recorded_completed = _block_plan_from_record(current_record)
        recorded_complete = recorded_completed == len(_block_data_writes(recorded_plan))
        if recorded_complete and (
            recorded_plan.get("incident_id") == incident_id
            and recorded_plan.get("source_issue_id") == source_id
        ):
            if source.get("workflow_blocked_by_incident_id") == incident_id:
                raise IncidentError(
                    "completed workflow block transition regained Incident ownership"
                )
            return {
                "allowed": True,
                "outcome": "complete",
                "reasons": [],
                "record": current_record,
                "progress": len(_block_full_writes(recorded_plan)),
                "total_writes": len(_block_full_writes(recorded_plan)),
                "writes": [],
                "complete": True,
                "target": recorded_plan["target"],
            }
        if recorded_complete:
            superseded_complete_record = True
        else:
            plan = recorded_plan
            if plan.get("incident_id") != incident_id or plan.get("source_issue_id") != source_id:
                raise IncidentError("workflow block transition record targets different Issues")
            if data.get("target") is not None:
                requested = _require_object(data.get("target"), "workflow block transition target")
                normalized = {
                    "status": requested.get("status"),
                    "waiting_on": requested.get("waiting_on", ""),
                    "blocked_reason": requested.get("blocked_reason", ""),
                }
                if normalized != plan.get("target"):
                    raise IncidentError("workflow block transition target conflicts with the record")
            if data.get("binding") is not None:
                binding = _require_object(data.get("binding"), "workflow block transition binding")
                if binding != plan.get("binding"):
                    raise IncidentError("workflow block transition binding drifted")
    if current_record in {None, ""} or superseded_complete_record:
        initial = _block_projection(source)
        initial["workflow_block_transition_record"] = ""
        target = _require_object(data.get("target"), "workflow block transition target")
        binding = _require_object(data.get("binding", {}), "workflow block transition binding")
        plan = {
            "schema_version": 1,
            "record_type": "workflow_block_transition",
            "incident_id": incident_id,
            "source_issue_id": source_id,
            "initial": initial,
            "target": {
                "status": target.get("status"),
                "waiting_on": target.get("waiting_on", ""),
                "blocked_reason": target.get("blocked_reason", ""),
            },
            "binding": binding,
            "binding_digest": sha256_value(binding),
        }
    _validate_block_plan(plan)
    capacity_reasons = _metadata_capacity_reasons(
        source.get("metadata_keys"), ["workflow_block_transition_record"], source_id
    )
    if capacity_reasons:
        return {
            "allowed": False,
            "outcome": "rejected",
            "reasons": capacity_reasons,
            "record": None,
            "writes": [],
            "complete": False,
        }
    full_writes = _block_full_writes(plan)
    expected = dict(plan["initial"])
    expected["workflow_block_transition_record"] = ""
    current = _block_projection(source)
    if superseded_complete_record:
        current["workflow_block_transition_record"] = ""
    matching_prefixes = []
    if expected == current:
        matching_prefixes.append(0)
    for index, write in enumerate(full_writes, start=1):
        expected = _apply_projected_write(expected, write)
        if expected == current:
            matching_prefixes.append(index)
    if not matching_prefixes:
        raise IncidentError("workflow block transition state is not a valid retry prefix")
    progress = max(matching_prefixes)
    remaining = full_writes[progress:]
    return {
        "allowed": True,
        "outcome": "complete" if not remaining else "resume_required",
        "reasons": [],
        "record": _block_record_value(plan, len(_block_data_writes(plan))),
        "progress": progress,
        "total_writes": len(full_writes),
        "writes": remaining,
        "complete": not remaining,
        "target": plan["target"],
    }


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


def _integration_recovery_ready(
    source_metadata: dict[str, Any],
    role_value: Any,
    recovery_value: Any,
    lease_record: dict[str, Any] | None,
    lease_complete: bool,
) -> tuple[bool, str]:
    if not isinstance(recovery_value, str) or not recovery_value:
        return False, ""
    try:
        role = decode_metadata_record(
            role_value, "integration_review_role_record", "integration_review_role"
        )
        recovery = decode_metadata_record(
            recovery_value,
            "integration_review_recovery_record",
            "integration_review_recovery",
        )
        if role.get("state") != "approved" or not lease_complete or lease_record is None:
            return False, str(recovery.get("incident_id") or "")
        if role.get("recovery_record_digest") != _record_digest(
            recovery_value, "integration_review_recovery_record"
        ):
            return False, str(recovery.get("incident_id") or "")
        if role.get("review_binding_digest") != _review_binding_digest(role):
            return False, str(recovery.get("incident_id") or "")
        if role.get("review_epoch_id") != _review_epoch_id(role):
            return False, str(recovery.get("incident_id") or "")
        if role.get("review_author_id") != role.get("reviewer_id"):
            return False, str(recovery.get("incident_id") or "")
        if role.get("trigger_outcome") not in HANDOFF_OUTCOMES:
            return False, str(recovery.get("incident_id") or "")
        if _parse_timestamp(
            role.get("review_created_at"), "integration Review review_created_at"
        ) <= _parse_timestamp(
            role.get("handoff_created_at"), "integration Review handoff_created_at"
        ):
            return False, str(recovery.get("incident_id") or "")
        for key in (
            "workspace_id",
            "squad_id",
            "roster_digest",
            "issue_id",
            "owner_id",
            "reviewer_id",
            "plan_revision",
            "delivery_policy_digest",
            "base_commit_sha",
            "reviewed_commit_sha",
            "dependency_digest",
            "lease_digest",
        ):
            if recovery.get(key) != role.get(key):
                return False, str(recovery.get("incident_id") or "")
        if (
            source_metadata.get("assignee_id") != role.get("owner_id")
            or source_metadata.get("original_owner_id") != role.get("owner_id")
            or source_metadata.get("reviewer_id") != role.get("reviewer_id")
        ):
            return False, str(recovery.get("incident_id") or "")
        initial = _require_object(
            lease_record.get("initial_mirror"), "recovered lease initial mirror"
        )
        initial_authority = _require_object(
            lease_record.get("initial_authority"),
            "recovered lease initial authority",
        )
        desired = _require_object(
            lease_record.get("desired"), "recovered lease desired tuple"
        )
        if lease_record.get("direction") != "acquire":
            return False, str(recovery.get("incident_id") or "")
        lease_keys = (
            "workspace_lease_state",
            "workspace_lease_owner_issue_id",
            "workspace_lease_owner_agent_id",
        )
        if {key: initial_authority.get(key, "") for key in lease_keys} != {
            key: initial.get(key, "") for key in lease_keys
        }:
            return False, str(recovery.get("incident_id") or "")
        current_lease = {
            "scope": "requirement",
            "state": initial.get("workspace_lease_state"),
            "owner_issue_id": initial.get("workspace_lease_owner_issue_id", ""),
            "owner_agent_id": initial.get("workspace_lease_owner_agent_id", ""),
        }
        target_lease = {
            "scope": "requirement",
            "state": desired.get("workspace_lease_state"),
            "owner_issue_id": desired.get("workspace_lease_owner_issue_id", ""),
            "owner_agent_id": desired.get("workspace_lease_owner_agent_id", ""),
        }
        current_source_lease = {
            "scope": source_metadata.get("workspace_lease_scope"),
            "state": source_metadata.get("workspace_lease_state"),
            "owner_issue_id": source_metadata.get("workspace_lease_owner_issue_id", ""),
            "owner_agent_id": source_metadata.get("workspace_lease_owner_agent_id", ""),
        }
        if current_source_lease != target_lease:
            return False, str(recovery.get("incident_id") or "")
        if role.get("lease_digest") != sha256_value(target_lease):
            return False, str(recovery.get("incident_id") or "")
        if recovery.get("lease_transition_digest") != sha256_value(
            {"current": current_lease, "target": target_lease}
        ):
            return False, str(recovery.get("incident_id") or "")
        return True, str(recovery.get("incident_id") or "")
    except IncidentError:
        return False, ""


def _live_lease_transition_complete(
    cli: CLI,
    source_metadata: dict[str, Any],
    record_value: str,
    record: dict[str, Any],
) -> bool:
    try:
        if (
            record.get("record_type") != "workspace_lease_transition"
            or record.get("direction") != "acquire"
            or record.get("completed") != 6
            or record.get("workspace_id") != cli.workspace_id
            or record.get("plan_revision") != source_metadata.get("plan_revision")
            or record.get("delivery_policy_digest")
            != source_metadata.get("delivery_policy_digest")
            or record.get("roster_digest")
            != decode_metadata_record(
                source_metadata.get("integration_review_recovery_record"),
                "integration_review_recovery_record",
                "integration_review_recovery",
            ).get("roster_digest")
        ):
            return False
        for key in ("roster_digest", "guard_digest", "mirror_blocker_digest"):
            if not _is_digest(record.get(key)):
                return False
        authority_id = record.get("authority_issue_id")
        mirror_id = record.get("mirror_issue_id")
        if (
            not isinstance(authority_id, str)
            or not authority_id
            or not isinstance(mirror_id, str)
            or not mirror_id
            or authority_id == mirror_id
            or source_metadata.get("issue_id") not in {authority_id, mirror_id}
        ):
            return False
        initial_authority = _require_object(
            record.get("initial_authority"), "recovered lease initial authority"
        )
        initial_mirror = _require_object(
            record.get("initial_mirror"), "recovered lease initial mirror"
        )
        desired = _require_object(
            record.get("desired"), "recovered lease desired tuple"
        )
        lease_keys = (
            "workspace_lease_state",
            "workspace_lease_owner_issue_id",
            "workspace_lease_owner_agent_id",
        )
        released = {
            "workspace_lease_state": "released",
            "workspace_lease_owner_issue_id": "",
            "workspace_lease_owner_agent_id": "",
        }
        if (
            {key: initial_authority.get(key, "") for key in lease_keys} != released
            or {key: initial_mirror.get(key, "") for key in lease_keys} != released
            or initial_authority.get("workspace_lease_transition_record") not in {None, ""}
            or initial_mirror.get("workspace_lease_transition_record") not in {None, ""}
            or desired.get("workspace_lease_state") != "held"
            or not desired.get("workspace_lease_owner_issue_id")
            or not desired.get("workspace_lease_owner_agent_id")
        ):
            return False
        blocker = _require_object(
            record.get("mirror_blocker"), "recovered lease mirror blocker"
        )
        if sha256_value(blocker) != record.get("mirror_blocker_digest"):
            return False
        other_leases = record.get("other_leases")
        if (
            not isinstance(other_leases, list)
            or sha256_value(other_leases) != record.get("other_leases_digest")
        ):
            return False
        for item in other_leases:
            if not isinstance(item, dict):
                return False
            for endpoint_name in ("authority", "mirror"):
                endpoint = item.get(endpoint_name)
                if (
                    not isinstance(endpoint, dict)
                    or endpoint.get("workspace_lease_state") != "released"
                    or endpoint.get("workspace_lease_owner_issue_id") not in {None, ""}
                    or endpoint.get("workspace_lease_owner_agent_id") not in {None, ""}
                ):
                    return False
        live_endpoints = {}
        for endpoint_name, issue_id in (
            ("authority", authority_id),
            ("mirror", mirror_id),
        ):
            if issue_id == source_metadata.get("issue_id"):
                metadata = source_metadata
            else:
                endpoint_issue = cli.json(
                    ["issue", "get", issue_id, "--output", "json"]
                )
                if not isinstance(endpoint_issue, dict):
                    return False
                metadata = {
                    **metadata_map(cli, issue_id),
                    "status": endpoint_issue.get("status"),
                }
            live_endpoints[endpoint_name] = metadata
            if metadata.get("workspace_lease_transition_record") != record_value:
                return False
            if {key: metadata.get(key, "") for key in lease_keys} != {
                key: desired.get(key, "") for key in lease_keys
            }:
                return False
        if {
            key: live_endpoints["mirror"].get(key, "")
            for key in (
                "status",
                "waiting_on",
                "blocked_reason",
                "workflow_blocked_by_incident_id",
                "workflow_blocked_previous_status",
            )
        } != blocker:
            return False
        return True
    except IncidentError:
        return False


def _live_block_binding(
    cli: CLI,
    source_metadata: dict[str, Any],
    source_commit: str,
    deployment_plan_digest: str,
) -> dict[str, Any]:
    role_record = source_metadata.get("integration_review_role_record")
    recovery_record = source_metadata.get("integration_review_recovery_record")
    lease_record = source_metadata.get("workspace_lease_transition_record")
    lease_complete = False
    decoded_lease: dict[str, Any] | None = None
    if isinstance(lease_record, str) and lease_record:
        try:
            decoded_lease = decode_metadata_record(
                lease_record, "workspace_lease_transition_record"
            )
            lease_complete = _live_lease_transition_complete(
                cli,
                source_metadata,
                lease_record,
                decoded_lease,
            )
        except IncidentError:
            lease_complete = False
            decoded_lease = None
    recovery_ready, recovery_incident_id = _integration_recovery_ready(
        source_metadata,
        role_record,
        recovery_record,
        decoded_lease,
        lease_complete,
    )
    return {
        "workflow_instance_id": cli.workspace_id,
        "source_object_type": source_metadata.get("workflow_object_type", ""),
        "plan_revision": source_metadata.get("plan_revision", ""),
        "delivery_policy_digest": source_metadata.get("delivery_policy_digest", ""),
        "base_commit_sha": source_metadata.get("base_commit_sha", ""),
        "reviewed_commit_sha": source_metadata.get("reviewed_commit_sha", ""),
        "integration_review_role_record_digest": (
            hashlib.sha256(role_record.encode("utf-8")).hexdigest()
            if isinstance(role_record, str) and role_record
            else ""
        ),
        "integration_review_recovery_record_digest": (
            hashlib.sha256(recovery_record.encode("utf-8")).hexdigest()
            if isinstance(recovery_record, str) and recovery_record
            else ""
        ),
        "workspace_lease_transition_record_digest": (
            hashlib.sha256(lease_record.encode("utf-8")).hexdigest()
            if isinstance(lease_record, str) and lease_record
            else ""
        ),
        "workspace_lease_transition_complete": lease_complete,
        "integration_review_recovery_approved": recovery_ready,
        "integration_review_recovery_incident_id": recovery_incident_id,
        "fixed_source_commit": source_commit.lower(),
        "deployment_plan_digest": deployment_plan_digest.lower(),
    }


def _live_block_snapshot(
    cli: CLI,
    incident_id: str,
    incident_metadata: dict[str, Any],
    source_id: str,
    source_commit: str,
    deployment_plan_digest: str,
) -> dict[str, Any]:
    source_issue = cli.json(["issue", "get", source_id, "--output", "json"])
    if not isinstance(source_issue, dict):
        raise IncidentError(f"blocked source is unreadable: {source_id}")
    source_metadata = metadata_map(cli, source_id)
    source = {
        "issue_id": source_id,
        "status": source_issue.get("status"),
        "metadata_keys": sorted(source_metadata),
        **source_metadata,
    }
    snapshot: dict[str, Any] = {
        "incident": {
            "issue_id": incident_id,
            "incident_status": incident_metadata.get("incident_status"),
        },
        "source": source,
        "binding": _live_block_binding(
            cli,
            {
                **source_metadata,
                "issue_id": source_id,
                "status": source_issue.get("status"),
                "assignee_id": source_issue.get("assignee_id"),
            },
            source_commit,
            deployment_plan_digest,
        ),
    }
    if source_metadata.get("workflow_block_transition_record") in {None, ""}:
        previous = str(
            source_metadata.get("workflow_blocked_previous_status") or ""
        )
        snapshot["target"] = {
            "status": previous,
            "waiting_on": "",
            "blocked_reason": "",
        }
    return snapshot


def _apply_block_write(cli: CLI, write: dict[str, Any]) -> None:
    issue_id = str(write["issue_id"])
    if write.get("kind") == "status":
        cli.json(
            [
                "issue",
                "update",
                issue_id,
                "--status",
                str(write.get("value")),
                "--output",
                "json",
            ]
        )
        return
    set_metadata(cli, issue_id, str(write["key"]), write.get("value", ""))


def _converge_block_transition(
    cli: CLI,
    incident_id: str,
    incident_metadata: dict[str, Any],
    source_id: str,
    source_commit: str,
    deployment_plan_digest: str,
) -> dict[str, Any]:
    limit = 100
    for _ in range(limit):
        snapshot = _live_block_snapshot(
            cli,
            incident_id,
            incident_metadata,
            source_id,
            source_commit,
            deployment_plan_digest,
        )
        result = block_transition_preflight(snapshot)
        if not result["allowed"]:
            raise IncidentError("; ".join(result["reasons"]))
        if result["complete"]:
            return result
        _apply_block_write(cli, result["writes"][0])
    raise IncidentError("workflow block transition exceeded 100 retry steps")


def close_incident(cli: CLI, args: argparse.Namespace) -> dict[str, Any]:
    incident, metadata = require_incident(cli, args.incident)
    incident_id = issue_ref(incident) or args.incident
    if metadata.get("incident_status") == "closed":
        if str(incident.get("status") or "") != "done":
            cli.json(
                [
                    "issue",
                    "update",
                    incident_id,
                    "--status",
                    "done",
                    "--output",
                    "json",
                ]
            )
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
        primary_metadata = metadata_map(cli, primary_source)
        if (
            primary_metadata.get("workflow_blocked_by_incident_id") == incident_id
            or primary_metadata.get("workflow_block_transition_record") not in {None, ""}
        ):
            blocked_sources.append(primary_source)
    preflight_results = []
    for source_id in blocked_sources:
        snapshot = _live_block_snapshot(
            cli,
            incident_id,
            metadata,
            source_id,
            args.source_commit,
            args.deployment_plan_digest,
        )
        source_metadata = snapshot["source"]
        if (
            source_metadata.get("workflow_blocked_by_incident_id") != incident_id
            and source_metadata.get("workflow_block_transition_record") in {None, ""}
        ):
            raise IncidentError(
                f"blocked source {source_id} is no longer owned by Incident {incident_id}"
            )
        result = block_transition_preflight(snapshot)
        if not result["allowed"]:
            raise IncidentError("; ".join(result["reasons"]))
        preflight_results.append((source_id, result["target"]))
    restored_count = 0
    successor_blocked_count = 0
    for source_id, target in preflight_results:
        _converge_block_transition(
            cli,
            incident_id,
            metadata,
            source_id,
            args.source_commit,
            args.deployment_plan_digest,
        )
        if target["status"] == "blocked":
            successor_blocked_count += 1
        else:
            restored_count += 1
    values = {
        "waiting_on": "",
        "last_verification_result": "passed",
        "last_verification_evidence": evidence,
        "verified_at": utc_now(),
        "verified_by": os.environ.get("MULTICA_AGENT_ID", "human_host"),
        "fixed_source_commit": args.source_commit,
        "deployment_plan_digest": args.deployment_plan_digest,
        "incident_status": "closed",
    }
    set_metadata_map(cli, incident_id, values)
    cli.json(["issue", "update", incident_id, "--status", "done", "--output", "json"])
    add_comment(cli, incident_id, f"Verification passed.\n\n{evidence}")
    return {
        "incident_id": incident_id,
        "closed": True,
        "result": "passed",
        "sources_restored": restored_count,
        "sources_successor_blocked": successor_blocked_count,
    }


def build_cli(args: argparse.Namespace) -> CLI:
    workspace = args.workspace or os.environ.get("MULTICA_WORKSPACE_ID")
    if not workspace:
        raise IncidentError("workspace is required; pass --workspace or set MULTICA_WORKSPACE_ID")
    profile = args.profile or os.environ.get("MULTICA_REQUIREMENT_PROFILE")
    return CLI(discover_multica(args.multica_bin), profile, workspace)


def load_snapshot(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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

    review = sub.add_parser("integration-review")
    review.add_argument(
        "--action", required=True, choices=sorted(INTEGRATION_REVIEW_ACTIONS)
    )
    review.add_argument("--snapshot", required=True)
    review.add_argument("--output", choices=["json"], default="json")

    transition = sub.add_parser("incident-transition")
    transition.add_argument("--snapshot", required=True)
    transition.add_argument("--output", choices=["json"], default="json")
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "integration-review":
            result = integration_review_transition(
                load_snapshot(args.snapshot), args.action
            )
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            return 0 if result["allowed"] else 1
        if args.command == "incident-transition":
            result = block_transition_preflight(load_snapshot(args.snapshot))
            print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
            return 0 if result["allowed"] else 1
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
    except (IncidentError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
