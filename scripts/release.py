#!/usr/bin/env python3
"""Digest-bound release planning and tag preflight for the workflow product."""

from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from workflow_lib import (  # noqa: E402
    MulticaCLI,
    WorkflowError,
    discover_multica,
    parse_marker,
    resolve_profile,
    resolve_workspace,
    runtime_instruction_versions,
)


class ReleaseError(RuntimeError):
    pass


RC2_RECOVERY_MODE = "external_pending_incident_exception"
RC2_RECOVERY_VERSION = "1.1.0-rc.2"
RC2_AFFECTED_VERSION = "v1.1.0-rc.1"
PROTECTED_ENVIRONMENT_MODE = "protected_environment"
RELEASE_CONTROL_PATH = "docs/release-control.json"
RELEASE_VERSION_RE = re.compile(
    r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)
RELEASE_CONTROL_KEYS = [
    "repository",
    "required_visibility",
    "environment",
    "deployment_branch",
    "operator_type",
    "dispatcher_app_slug",
    "publisher_app_slug",
    "publisher_app_id_variable",
    "publisher_private_key_secret",
    "tag_ruleset",
    "administrator_evidence_path",
]
RC2_RECOVERY_DECISION = (
    "DECISION: 允许将 WOR-1 中的 durable pending Incident 作为本次部分部署恢复的临时 Intake；"
    "允许外部恢复 Agent 在 WOR-1 下创建并推进 Maintenance Change 和 v1.1.0-rc.2。"
    "rc.2 部署补全 squad 后，必须由 Observer 自动恢复并链接正式 Incident；"
    "在此之前不得关闭维护树、完成 Canary 验收或发布稳定版本。"
)


def as_list(value: Any, key: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict) and isinstance(value.get(key), list):
        return [item for item in value[key] if isinstance(item, dict)]
    return []


def metadata_map(value: Any) -> dict[str, Any]:
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


def comment_author(comment: dict[str, Any]) -> tuple[str, str]:
    author = comment.get("author") if isinstance(comment.get("author"), dict) else {}
    return (
        str(
            comment.get("author_id")
            or comment.get("creator_id")
            or comment.get("user_id")
            or author.get("id")
            or ""
        ),
        str(
            comment.get("author_type")
            or comment.get("creator_type")
            or comment.get("user_type")
            or author.get("type")
            or ""
        ).lower(),
    )


def managed_match(items: list[dict[str, Any]], object_key: str, field: str) -> dict[str, Any]:
    matches = []
    for item in items:
        marker = parse_marker(str(item.get(field) or ""))
        if (
            marker
            and marker.get("managed_by") == "multica-dev-workflow"
            and marker.get("workflow_id") == "development-delivery"
            and marker.get("object_key") == object_key
        ):
            matches.append(item)
    if len(matches) != 1:
        raise ReleaseError(f"expected one managed {object_key}, found {len(matches)}")
    return matches[0]


def release_cli(
    multica_bin: str | None, profile: str | None, workspace: str | None
) -> tuple[MulticaCLI, dict[str, Any]]:
    binary = discover_multica(multica_bin)
    resolved_profile = resolve_profile(binary, profile)
    cli = MulticaCLI(binary=binary, profile=resolved_profile)
    resolved_workspace = resolve_workspace(cli, workspace)
    return cli, resolved_workspace


def managed_agent_identity(cli: MulticaCLI, object_key: str, label: str) -> str:
    agents = as_list(cli.json(["agent", "list", "--output", "json"]), "agents")
    agent = managed_match(agents, object_key, "instructions")
    agent_id = str(agent.get("id") or "")
    if not agent_id:
        raise ReleaseError(f"managed {label} identity is incomplete")
    return agent_id


def control_agent_identities(cli: MulticaCLI) -> dict[str, str]:
    identities = {
        "maintainer_id": managed_agent_identity(
            cli, "agent.workflow-maintainer", "Maintainer"
        ),
        "maintenance_reviewer_id": managed_agent_identity(
            cli, "agent.workflow-maintenance-reviewer", "Maintenance Reviewer"
        ),
    }
    if not all(identities.values()):
        raise ReleaseError("managed maintenance Agent identities are incomplete")
    if identities["maintainer_id"] == identities["maintenance_reviewer_id"]:
        raise ReleaseError("Maintenance Reviewer must differ from Maintainer")
    return identities


def human_approver_identity(root: Path, cli: MulticaCLI) -> str:
    squads = as_list(cli.json(["squad", "list", "--output", "json"]), "squads")
    squad = managed_match(squads, "squad.development-delivery", "instructions")
    manifest = json.loads((root / "workflow.json").read_text(encoding="utf-8"))
    approver_role = str(manifest["workflow"]["approver_role"])
    members = as_list(
        cli.json(["squad", "member", "list", str(squad["id"]), "--output", "json"]),
        "members",
    )
    approvers = [
        item
        for item in members
        if item.get("member_type") == "member" and str(item.get("role") or "") == approver_role
    ]
    if len(approvers) != 1:
        raise ReleaseError(f"expected one human approver with role {approver_role}, found {len(approvers)}")
    human_approver_id = str(approvers[0].get("member_id") or "")
    if not human_approver_id:
        raise ReleaseError("managed human approver identity is incomplete")
    return human_approver_id


def control_identities(root: Path, cli: MulticaCLI) -> dict[str, str]:
    identities = control_agent_identities(cli)
    identities["human_approver_id"] = human_approver_identity(root, cli)
    if not all(identities.values()):
        raise ReleaseError("managed maintenance identities are incomplete")
    if identities["maintainer_id"] == identities["maintenance_reviewer_id"]:
        raise ReleaseError("Maintenance Reviewer must differ from Maintainer")
    return identities


def truthy(value: Any) -> bool:
    return value is True or str(value).lower() == "true"


def first_metadata_value(metadata: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = str(metadata.get(key) or "")
        if value:
            return value
    return ""


def metadata_issue_ids(value: Any) -> list[str]:
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return []
        try:
            value = json.loads(stripped)
        except json.JSONDecodeError:
            value = [item.strip() for item in stripped.split(",") if item.strip()]
    if not isinstance(value, list):
        raise ReleaseError("implementation_issue_ids must be a JSON array")
    result = [str(item) for item in value if str(item)]
    if len(result) != len(set(result)):
        raise ReleaseError("implementation_issue_ids contains duplicates")
    return result


def exact_review_comment(
    cli: MulticaCLI,
    issue_id: str,
    review_comment_id: str,
    reviewer_id: str,
    plan_revision: str,
    reviewed_sha: str,
    merged_at: str,
    label: str,
) -> str:
    comments = as_list(
        cli.json(
            ["issue", "comment", "list", issue_id, "--full", "--output", "json"]
        ),
        "comments",
    )
    matches = [
        item
        for item in comments
        if str(item.get("id") or "") == review_comment_id
    ]
    if len(matches) != 1:
        raise ReleaseError(
            f"expected one {label} comment {review_comment_id}, found {len(matches)}"
        )
    comment = matches[0]
    content = str(comment.get("content") or "")
    if (
        comment.get("author_type") != "agent"
        or str(comment.get("author_id") or "") != reviewer_id
    ):
        raise ReleaseError(f"{label} was not authored by the managed Code Reviewer")
    first_verdict = next(
        (line.strip() for line in content.splitlines() if line.strip()), ""
    )
    if first_verdict != "APPROVED":
        raise ReleaseError(f"{label} first non-empty line is not exactly APPROVED")
    if re.findall(r"(?m)^\s*plan_revision=([^\s]+)\s*$", content) != [
        plan_revision
    ]:
        raise ReleaseError(
            f"{label} does not bind exactly one exact plan_revision line"
        )
    if re.findall(r"(?m)^\s*reviewed_commit_sha=([^\s]+)\s*$", content) != [
        reviewed_sha
    ]:
        raise ReleaseError(
            f"{label} does not bind exactly one exact reviewed_commit_sha line"
        )
    review_created_at = str(comment.get("created_at") or comment.get("createdAt") or "")
    if parse_iso_datetime(review_created_at, label) >= parse_iso_datetime(
        merged_at, "merged PR"
    ):
        raise ReleaseError(f"{label} must be created before the PR is merged")
    return review_created_at


def phase1_release_evidence(
    root: Path,
    cli: MulticaCLI,
    issue_id: str,
    issue: dict[str, Any],
    metadata: dict[str, Any],
    pr: dict[str, Any],
) -> dict[str, Any]:
    object_type = str(metadata.get("workflow_object_type") or "")
    if str(metadata.get("workflow_id") or "") != "development-delivery":
        raise ReleaseError("Phase 1 release source workflow_id is not development-delivery")
    if object_type == "requirement" and str(issue.get("status") or "") != "done":
        raise ReleaseError("release Requirement must be done")

    human_approver_id = str(metadata.get("human_approver_id") or "")
    plan_revision = str(metadata.get("plan_revision") or "")
    review_issue_id = str(metadata.get("review_issue_id") or "")
    if not human_approver_id or not plan_revision or not review_issue_id:
        raise ReleaseError(
            "Phase 1 release source is missing human_approver_id, plan_revision or review_issue_id"
        )
    if human_approver_id != human_approver_identity(root, cli):
        raise ReleaseError(
            "Phase 1 release human_approver_id differs from the managed Squad approver"
        )

    selected_refs = issue_refs(issue_id, issue)
    release_path = "requirement"
    incident_evidence: dict[str, str] = {}
    if object_type == "maintenance_case":
        release_path = "incident_fix"
        case_status = str(
            metadata.get("logical_status")
            or metadata.get("maintenance_case_status")
            or ""
        )
        if case_status not in {
            "fix_ready",
            "awaiting_deployment",
            "awaiting_observer_verification",
        }:
            raise ReleaseError("Maintenance Case is not ready for release")
        if str(metadata.get("executor") or "") != "ordinary_development_workflow":
            raise ReleaseError(
                "Phase 1 Maintenance Case must use the ordinary development workflow"
            )
        linked_issues = metadata_issue_ids(metadata.get("implementation_issue_ids"))
        if review_issue_id not in linked_issues:
            raise ReleaseError(
                "Maintenance Case review_issue_id is not listed in implementation_issue_ids"
            )
        incident_id = str(metadata.get("incident_id") or "")
        if not incident_id:
            raise ReleaseError("Maintenance Case incident_id is missing")
        incident = cli.json(["issue", "get", incident_id, "--output", "json"])
        if not isinstance(incident, dict):
            raise ReleaseError(f"Incident is unreadable: {incident_id}")
        incident_metadata = metadata_map(
            cli.json(["issue", "metadata", "list", incident_id, "--output", "json"])
        )
        if (
            str(incident_metadata.get("workflow_id") or "")
            != "development-delivery"
            or str(incident_metadata.get("workflow_object_type") or "") != "incident"
        ):
            raise ReleaseError(
                "Maintenance Case incident_id is not a development-delivery Incident"
            )
        require_issue_ref(
            incident_metadata.get("maintenance_case_id"),
            selected_refs,
            "Incident maintenance_case_id",
        )
        if str(incident_metadata.get("human_approver_id") or "") != human_approver_id:
            raise ReleaseError(
                "Maintenance Case human_approver_id differs from its Incident"
            )
        approval_comment_id = str(metadata.get("approval_comment_id") or "")
        intake_digest = str(metadata.get("maintenance_intake_digest") or "")
        if not approval_comment_id or not intake_digest:
            raise ReleaseError(
                "Maintenance Case approval_comment_id or maintenance_intake_digest is missing"
            )
        if (
            str(incident_metadata.get("maintenance_intake_digest") or "")
            != intake_digest
        ):
            raise ReleaseError(
                "Maintenance Case maintenance_intake_digest differs from its Incident"
            )
        if (
            str(incident_metadata.get("maintenance_decision_comment_id") or "")
            != approval_comment_id
        ):
            raise ReleaseError(
                "Maintenance Case approval_comment_id differs from its Incident"
            )
        decision_comments = as_list(
            cli.json(
                [
                    "issue",
                    "comment",
                    "list",
                    incident_id,
                    "--full",
                    "--output",
                    "json",
                ]
            ),
            "comments",
        )
        decision_matches = [
            item
            for item in decision_comments
            if str(item.get("id") or "") == approval_comment_id
        ]
        expected_decision = f"APPROVE WORKFLOW MAINTENANCE {intake_digest[:16]}"
        if len(decision_matches) != 1:
            raise ReleaseError(
                "Maintenance Case approval comment is missing or ambiguous"
            )
        decision = decision_matches[0]
        decision_lines = [
            line.strip()
            for line in str(decision.get("content") or "").splitlines()
            if line.strip()
        ]
        decision_author_id, decision_author_type = comment_author(decision)
        if (
            decision_author_type not in {"member", "user"}
            or decision_author_id != human_approver_id
            or decision_lines != [expected_decision]
        ):
            raise ReleaseError(
                "Maintenance Case approval comment has the wrong author or digest"
            )
        incident_evidence = {
            "incident_id": str(incident.get("identifier") or incident_id),
            "maintenance_intake_digest": intake_digest,
            "maintenance_approval_comment_id": approval_comment_id,
        }

    review_issue = cli.json(["issue", "get", review_issue_id, "--output", "json"])
    if not isinstance(review_issue, dict):
        raise ReleaseError(f"Phase 1 release Review issue is unreadable: {review_issue_id}")
    if str(review_issue.get("status") or "") != "done":
        raise ReleaseError("Phase 1 release Review issue must be done")
    review_metadata = metadata_map(
        cli.json(["issue", "metadata", "list", review_issue_id, "--output", "json"])
    )
    if str(review_metadata.get("workflow_id") or "") != "development-delivery":
        raise ReleaseError("Phase 1 release Review issue workflow_id is not development-delivery")
    if str(review_metadata.get("workflow_object_type") or "") != "integration_validation":
        raise ReleaseError(
            "Phase 1 release Review issue must be an integration_validation"
        )
    if str(review_metadata.get("plan_revision") or "") != plan_revision:
        raise ReleaseError("Phase 1 release Review Plan revision differs from its source")

    root_requirement_id = str(review_metadata.get("root_requirement_id") or "")
    if not root_requirement_id:
        raise ReleaseError("Phase 1 release Review root_requirement_id is missing")
    if object_type == "requirement":
        require_issue_ref(
            root_requirement_id, selected_refs, "Phase 1 release Review root_requirement_id"
        )
    else:
        source_root = str(metadata.get("root_requirement_id") or "")
        if not source_root or source_root != root_requirement_id:
            raise ReleaseError(
                "Maintenance Case root_requirement_id differs from its Review issue"
            )
        root_requirement = cli.json(
            ["issue", "get", root_requirement_id, "--output", "json"]
        )
        if not isinstance(root_requirement, dict):
            raise ReleaseError(
                f"Maintenance Case root Requirement is unreadable: {root_requirement_id}"
            )
        if str(root_requirement.get("status") or "") != "done":
            raise ReleaseError("Maintenance Case root Requirement must be done")
        root_metadata = metadata_map(
            cli.json(
                [
                    "issue",
                    "metadata",
                    "list",
                    root_requirement_id,
                    "--output",
                    "json",
                ]
            )
        )
        if (
            str(root_metadata.get("workflow_id") or "") != "development-delivery"
            or str(root_metadata.get("workflow_object_type") or "") != "requirement"
        ):
            raise ReleaseError(
                "Maintenance Case root_requirement_id is not a development Requirement"
            )
        if str(root_metadata.get("human_approver_id") or "") != human_approver_id:
            raise ReleaseError(
                "Maintenance Case root Requirement human approver differs from the Case"
            )
        if str(root_metadata.get("plan_revision") or "") != plan_revision:
            raise ReleaseError(
                "Maintenance Case root Requirement Plan revision differs from the Case"
            )
        require_issue_ref(
            root_metadata.get("review_issue_id"),
            issue_refs(review_issue_id, review_issue),
            "Maintenance Case root Requirement review_issue_id",
        )
        selected_number = str(pr.get("number") or "")
        selected_merge = str((pr.get("mergeCommit") or {}).get("oid") or "")
        if first_metadata_value(
            root_metadata, "github_pr_number", "pr_number"
        ) != selected_number:
            raise ReleaseError(
                "Maintenance Case root Requirement PR differs from the selected PR"
            )
        if first_metadata_value(
            root_metadata, "github_merge_commit_sha", "merge_commit_sha"
        ) != selected_merge:
            raise ReleaseError(
                "Maintenance Case root Requirement merge commit differs from the release commit"
            )

    code_reviewer_id = managed_agent_identity(
        cli, "agent.code-reviewer", "Code Reviewer"
    )
    reviewer_id = str(review_metadata.get("reviewer_id") or "")
    original_owner_id = str(review_metadata.get("original_owner_id") or "")
    if reviewer_id != code_reviewer_id:
        raise ReleaseError(
            "Phase 1 release Review reviewer_id differs from the managed Code Reviewer"
        )
    if not original_owner_id or original_owner_id == reviewer_id:
        raise ReleaseError("Phase 1 release Review is not independent from its owner")

    reviewed_sha = first_metadata_value(
        review_metadata, "reviewed_commit_sha", "review_commit_sha"
    )
    review_comment_id = str(review_metadata.get("review_comment_id") or "")
    pr_head_sha = str(review_metadata.get("pr_head_sha") or "")
    if not reviewed_sha or not review_comment_id or not pr_head_sha:
        raise ReleaseError(
            "Phase 1 release Review is missing reviewed SHA, pr_head_sha or review_comment_id"
        )
    selected_head = str(pr.get("headRefOid") or "")
    if reviewed_sha != selected_head or pr_head_sha != selected_head:
        raise ReleaseError("Phase 1 release Review is stale for the merged PR head SHA")

    selected_pr_number = str(pr.get("number") or "")
    selected_merge_sha = str((pr.get("mergeCommit") or {}).get("oid") or "")
    source_pr_number = first_metadata_value(metadata, "github_pr_number", "pr_number")
    source_merge_sha = first_metadata_value(
        metadata, "github_merge_commit_sha", "merge_commit_sha"
    )
    review_pr_number = first_metadata_value(
        review_metadata, "github_pr_number", "pr_number"
    )
    review_merge_sha = first_metadata_value(
        review_metadata, "github_merge_commit_sha", "merge_commit_sha"
    )
    if not all(
        [source_pr_number, source_merge_sha, review_pr_number, review_merge_sha]
    ):
        raise ReleaseError("Phase 1 release source or Review is missing merged PR evidence")
    if source_pr_number != selected_pr_number or review_pr_number != selected_pr_number:
        raise ReleaseError("Phase 1 release PR number differs from the selected PR")
    if source_merge_sha != selected_merge_sha or review_merge_sha != selected_merge_sha:
        raise ReleaseError("Phase 1 release merge commit differs from the release commit")

    merged_at = str(pr.get("mergedAt") or pr.get("merged_at") or "")
    review_created_at = exact_review_comment(
        cli,
        review_issue_id,
        review_comment_id,
        code_reviewer_id,
        plan_revision,
        reviewed_sha,
        merged_at,
        "Phase 1 Code Review comment",
    )
    return {
        "mode": "maintenance",
        "authorization_type": "phase1_development",
        "release_path": release_path,
        "issue_id": str(issue.get("identifier") or issue_id),
        "workflow_object_type": object_type,
        "workspace_id": cli.workspace_id,
        "profile": cli.profile,
        "human_approver_id": human_approver_id,
        "code_reviewer_id": code_reviewer_id,
        "plan_revision": plan_revision,
        "review_issue_id": str(review_issue.get("identifier") or review_issue_id),
        "root_requirement_id": root_requirement_id,
        "review_comment_id": review_comment_id,
        "reviewed_commit_sha": reviewed_sha,
        "review_created_at": review_created_at,
        "github_pr_number": int(pr["number"]),
        "github_merge_commit_sha": selected_merge_sha,
        "github_merged_at": merged_at,
        **incident_evidence,
    }


def recovery_exception_evidence(
    cli: MulticaCLI,
    issue_id: str,
    issue: dict[str, Any],
    metadata: dict[str, Any],
    version: str | None,
) -> tuple[dict[str, str], dict[str, str]]:
    if version != RC2_RECOVERY_VERSION:
        raise ReleaseError(
            f"{RC2_RECOVERY_MODE} is limited to {RC2_RECOVERY_VERSION}"
        )
    if str(metadata.get("target_release") or "") != f"v{RC2_RECOVERY_VERSION}":
        raise ReleaseError("recovery Maintenance target_release is not v1.1.0-rc.2")
    if str(metadata.get("affected_release") or "") != RC2_AFFECTED_VERSION:
        raise ReleaseError("recovery Maintenance affected_release is not v1.1.0-rc.1")

    identities = control_agent_identities(cli)
    human_approver_id = str(metadata.get("human_approver_id") or "")
    identities["human_approver_id"] = human_approver_id
    if not human_approver_id:
        raise ReleaseError("recovery Maintenance human_approver_id is missing")
    for key, expected in identities.items():
        if str(metadata.get(key) or "") != expected:
            raise ReleaseError(
                f"recovery Maintenance {key} does not match its durable identity"
            )

    source_id = str(
        metadata.get("pending_incident_source") or metadata.get("source_issue") or ""
    )
    if not source_id:
        raise ReleaseError("recovery Maintenance pending Incident source is missing")
    source_issue = cli.json(["issue", "get", source_id, "--output", "json"])
    if not isinstance(source_issue, dict):
        raise ReleaseError(f"pending Incident source is unreadable: {source_id}")
    if str(issue.get("parent_issue_id") or "") != str(source_issue.get("id") or ""):
        raise ReleaseError("recovery Maintenance Issue is not a child of its pending source")
    source_metadata = metadata_map(
        cli.json(["issue", "metadata", "list", source_id, "--output", "json"])
    )
    if not truthy(source_metadata.get("workflow_incident_pending")):
        raise ReleaseError("pending Incident source is no longer marked pending")
    if source_metadata.get("workflow_incident_id"):
        raise ReleaseError("pending Incident source is already linked to a standard Incident")
    if str(source_metadata.get("maintenance_change_id") or "") != issue_id:
        raise ReleaseError("pending Incident source does not link this Maintenance Change")

    raw_payload = source_metadata.get("workflow_incident_pending_payload")
    if isinstance(raw_payload, str):
        try:
            payload = json.loads(raw_payload)
        except json.JSONDecodeError as exc:
            raise ReleaseError("pending Incident payload is invalid JSON") from exc
    else:
        payload = raw_payload
    if not isinstance(payload, dict):
        raise ReleaseError("pending Incident payload is missing")
    dedupe_key = str(payload.get("dedupe_key") or "")
    rule_id = str(payload.get("rule_id") or "")
    pending_index = str(source_metadata.get("workflow_incident_pending_index") or "")
    if not dedupe_key or not rule_id or not pending_index:
        raise ReleaseError("pending Incident evidence is missing its stable index/dedupe/rule")

    decision_comment_id = str(metadata.get("recovery_decision_comment_id") or "")
    source_comments = as_list(
        cli.json(["issue", "comment", "list", source_id, "--full", "--output", "json"]),
        "comments",
    )
    decision_matches = [
        item
        for item in source_comments
        if str(item.get("id") or "") == decision_comment_id
    ]
    if len(decision_matches) != 1:
        raise ReleaseError("recovery decision comment is missing or ambiguous")
    decision = decision_matches[0]
    decision_author_id, decision_author_type = comment_author(decision)
    if (
        decision_author_type not in {"member", "user"}
        or decision_author_id != human_approver_id
        or str(decision.get("content") or "").strip() != RC2_RECOVERY_DECISION
    ):
        raise ReleaseError("recovery decision has the wrong author or exact content")

    bounded = {
        "recovery_mode": RC2_RECOVERY_MODE,
        "pending_incident_source": str(source_issue.get("identifier") or source_id),
        "recovery_decision_comment_id": decision_comment_id,
        "affected_release": RC2_AFFECTED_VERSION,
        "target_release": f"v{RC2_RECOVERY_VERSION}",
        "control_identity_sha256": digest(identities),
        "pending_incident_evidence_sha256": digest(
            {
                "source_issue": str(source_issue.get("identifier") or source_id),
                "maintenance_issue": issue_id,
                "pending_index": pending_index,
                "dedupe_key": dedupe_key,
                "rule_id": rule_id,
            }
        ),
        "recovery_decision_sha256": digest(
            {
                "comment_id": decision_comment_id,
                "author_type": decision_author_type,
                "author_id": decision_author_id,
                "content": str(decision.get("content") or "").strip(),
            }
        ),
    }
    return identities, bounded


def maintenance_evidence(
    root: Path,
    cli: MulticaCLI,
    issue_id: str,
    pr: dict[str, Any],
    version: str | None = None,
) -> dict[str, Any]:
    issue = cli.json(["issue", "get", issue_id, "--output", "json"])
    if not isinstance(issue, dict):
        raise ReleaseError(f"Maintenance Issue is unreadable: {issue_id}")
    metadata = metadata_map(
        cli.json(["issue", "metadata", "list", issue_id, "--output", "json"])
    )
    object_type = str(metadata.get("workflow_object_type") or "")
    if object_type in {"requirement", "maintenance_case"}:
        return phase1_release_evidence(root, cli, issue_id, issue, metadata, pr)
    if object_type != "maintenance_change":
        raise ReleaseError(
            "release requires a requirement, maintenance_case or legacy maintenance_change Issue"
        )
    recovery_mode = str(metadata.get("recovery_mode") or "")
    recovery_evidence: dict[str, str] = {}
    if recovery_mode:
        if recovery_mode != RC2_RECOVERY_MODE:
            raise ReleaseError(f"unsupported Maintenance recovery_mode: {recovery_mode}")
        identities, recovery_evidence = recovery_exception_evidence(
            cli, issue_id, issue, metadata, version
        )
    else:
        identities = control_identities(root, cli)
        for key, expected in identities.items():
            if str(metadata.get(key) or "") != expected:
                raise ReleaseError(f"Maintenance Issue {key} does not match the managed control-plane identity")
    plan_revision = str(metadata.get("plan_revision") or "")
    reviewed_sha = str(metadata.get("reviewed_commit_sha") or "")
    review_comment_id = str(metadata.get("review_comment_id") or "")
    if not plan_revision or not reviewed_sha or not review_comment_id:
        raise ReleaseError("Maintenance Issue is missing plan_revision, reviewed_commit_sha or review_comment_id")
    if reviewed_sha != str(pr.get("headRefOid") or ""):
        raise ReleaseError("Maintenance Review is stale or does not bind the merged PR head SHA")
    if str(metadata.get("github_pr_number") or "") != str(pr.get("number") or ""):
        raise ReleaseError("Maintenance Issue github_pr_number does not match the selected PR")
    merge_sha = str((pr.get("mergeCommit") or {}).get("oid") or "")
    if str(metadata.get("github_merge_commit_sha") or "") != merge_sha:
        raise ReleaseError("Maintenance Issue github_merge_commit_sha does not match the release commit")
    review_issue_id = str(metadata.get("review_issue_id") or "")
    comment_issue_id = issue_id
    if review_issue_id:
        if review_issue_id not in issue_refs(issue_id, issue):
            bounded_review_issue(
                cli, issue_id, issue, metadata, identities, review_issue_id
            )
        comment_issue_id = review_issue_id
    elif str(metadata.get("delivery_batch") or ""):
        raise ReleaseError("delivery batch Maintenance releases require review_issue_id")
    comments = as_list(
        cli.json(
            [
                "issue",
                "comment",
                "list",
                comment_issue_id,
                "--full",
                "--output",
                "json",
            ]
        ),
        "comments",
    )
    matches = [item for item in comments if str(item.get("id") or "") == review_comment_id]
    if len(matches) != 1:
        raise ReleaseError(f"expected one Maintenance Review comment {review_comment_id}, found {len(matches)}")
    comment = matches[0]
    content = str(comment.get("content") or "")
    if comment.get("author_type") != "agent" or str(comment.get("author_id") or "") != identities["maintenance_reviewer_id"]:
        raise ReleaseError("Maintenance Review comment was not authored by the managed Maintenance Reviewer")
    first_verdict = next(
        (line.strip() for line in content.splitlines() if line.strip()), ""
    )
    if first_verdict != "APPROVED":
        raise ReleaseError(
            "Maintenance Review comment first non-empty line is not exactly APPROVED"
        )
    plan_bindings = re.findall(r"(?m)^\s*plan_revision=([^\s]+)\s*$", content)
    if plan_bindings != [plan_revision]:
        raise ReleaseError(
            "Maintenance Review comment does not bind exactly one exact plan_revision line"
        )
    commit_bindings = re.findall(
        r"(?m)^\s*reviewed_commit_sha=([^\s]+)\s*$", content
    )
    if commit_bindings != [reviewed_sha]:
        raise ReleaseError(
            "Maintenance Review comment does not bind exactly one exact reviewed_commit_sha line"
        )
    review_created_at = str(comment.get("created_at") or comment.get("createdAt") or "")
    merged_at = str(pr.get("mergedAt") or pr.get("merged_at") or "")
    review_time = parse_iso_datetime(review_created_at, "Maintenance Review comment")
    merge_time = parse_iso_datetime(merged_at, "merged PR")
    if review_time >= merge_time:
        raise ReleaseError("Maintenance Review must be created before the PR is merged")
    batch_review_mappings = normalized_batch_review_mappings(
        metadata.get("batch_review_mappings")
    )
    batch_evidence: dict[str, str] = {}
    if str(metadata.get("delivery_batch") or "") or batch_review_mappings:
        if not review_issue_id:
            raise ReleaseError("delivery batch Maintenance releases require review_issue_id")
        if not batch_review_mappings:
            raise ReleaseError("delivery batch Maintenance releases require batch_review_mappings")
        if not any(item["implementation"] == review_issue_id for item in batch_review_mappings):
            raise ReleaseError("review_issue_id is not one of the batch_review_mappings implementation issues")
        observed_mappings = review_comment_batch_mappings(content)
        if observed_mappings != batch_review_mappings:
            raise ReleaseError("Maintenance Review comment batch mappings do not exactly match batch_review_mappings")
        batch_evidence = {
            "batch_review_mappings_sha256": digest(
                {
                    "review_issue_id": review_issue_id,
                    "batch_review_mappings": batch_review_mappings,
                }
            )
        }
    evidence = {
        "mode": "maintenance",
        "issue_id": issue_id,
        "workspace_id": cli.workspace_id,
        "profile": cli.profile,
        **({} if recovery_evidence else identities),
        **recovery_evidence,
        "plan_revision": plan_revision,
        "reviewed_commit_sha": reviewed_sha,
        "review_comment_id": review_comment_id,
        "review_created_at": review_created_at,
        "github_pr_number": int(pr["number"]),
        "github_merge_commit_sha": merge_sha,
        "github_merged_at": merged_at,
    }
    if review_issue_id:
        evidence["review_issue_id"] = review_issue_id
    evidence.update(batch_evidence)
    return evidence


def merged_pr_by_number(root: Path, number: int) -> dict[str, Any]:
    value = gh_json(
        root,
        [
            "pr",
            "view",
            str(number),
            "--json",
            "number,url,title,state,mergeCommit,headRefOid,baseRefName,mergedAt",
        ],
    )
    if (
        not isinstance(value, dict)
        or value.get("state") != "MERGED"
        or value.get("baseRefName") != "main"
        or not value.get("mergedAt")
    ):
        raise ReleaseError(f"implementation provenance PR #{number} is not merged to main")
    return value


def development_implementation_evidence(
    root: Path,
    cli: MulticaCLI,
    issue_id: str,
    issue: dict[str, Any],
    metadata: dict[str, Any],
    planned: dict[str, Any] | None = None,
) -> dict[str, Any]:
    object_type = str(metadata.get("workflow_object_type") or "")
    if str(metadata.get("workflow_id") or "") != "development-delivery":
        raise ReleaseError("development provenance workflow_id is not development-delivery")
    if object_type != "integration_validation":
        raise ReleaseError(
            "Phase 1 release provenance Issue is not an integration_validation"
        )
    if str(issue.get("status") or "") != "done":
        raise ReleaseError("development provenance Issue must be done")

    reviewer_id = managed_agent_identity(cli, "agent.code-reviewer", "Code Reviewer")
    if str(metadata.get("reviewer_id") or "") != reviewer_id:
        raise ReleaseError(
            "development provenance reviewer identity differs from the managed Code Reviewer"
        )
    original_owner_id = str(metadata.get("original_owner_id") or "")
    if not original_owner_id or original_owner_id == reviewer_id:
        raise ReleaseError("development provenance Review is not independent from its owner")

    reviewed_sha = first_metadata_value(
        metadata, "reviewed_commit_sha", "review_commit_sha"
    )
    required = {
        "root_requirement_id": str(metadata.get("root_requirement_id") or ""),
        "plan_revision": str(metadata.get("plan_revision") or ""),
        "reviewed_commit_sha": reviewed_sha,
        "pr_head_sha": str(metadata.get("pr_head_sha") or ""),
        "review_comment_id": str(metadata.get("review_comment_id") or ""),
        "github_pr_number": first_metadata_value(
            metadata, "github_pr_number", "pr_number"
        ),
        "github_merge_commit_sha": first_metadata_value(
            metadata, "github_merge_commit_sha", "merge_commit_sha"
        ),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ReleaseError(f"development provenance is missing fields: {missing}")
    if required["pr_head_sha"] != required["reviewed_commit_sha"]:
        raise ReleaseError("development provenance pr_head_sha differs from reviewed SHA")
    try:
        pr_number = int(required["github_pr_number"])
    except ValueError as exc:
        raise ReleaseError("development provenance github_pr_number is invalid") from exc

    if planned is not None:
        refs = issue_refs(issue_id, issue)
        if str(planned.get("issue_id") or "") not in refs:
            raise ReleaseError(
                "planned development provenance does not match its Multica Issue"
            )
        if int(planned.get("github_pr_number") or 0) != pr_number:
            raise ReleaseError(
                "planned development provenance PR differs from Multica metadata"
            )
        pr = {
            "number": pr_number,
            "state": "MERGED",
            "baseRefName": "main",
            "headRefOid": str(planned.get("reviewed_commit_sha") or ""),
            "mergeCommit": {
                "oid": str(planned.get("github_merge_commit_sha") or "")
            },
            "mergedAt": str(planned.get("github_merged_at") or ""),
        }
    else:
        pr = merged_pr_by_number(root, pr_number)

    merge_sha = str((pr.get("mergeCommit") or {}).get("oid") or "")
    if required["reviewed_commit_sha"] != str(pr.get("headRefOid") or ""):
        raise ReleaseError("development provenance Review is stale for its merged PR")
    if required["github_merge_commit_sha"] != merge_sha:
        raise ReleaseError("development provenance merge commit differs from its merged PR")
    review_created_at = exact_review_comment(
        cli,
        issue_id,
        required["review_comment_id"],
        reviewer_id,
        required["plan_revision"],
        required["reviewed_commit_sha"],
        str(pr.get("mergedAt") or ""),
        "Development Review comment",
    )
    return {
        "issue_id": str(issue.get("identifier") or issue_id),
        "workflow_object_type": object_type,
        "root_requirement_id": required["root_requirement_id"],
        "plan_revision": required["plan_revision"],
        "review_comment_id": required["review_comment_id"],
        "reviewed_commit_sha": required["reviewed_commit_sha"],
        "review_created_at": review_created_at,
        "github_pr_number": pr_number,
        "github_merge_commit_sha": merge_sha,
        "github_merged_at": str(pr.get("mergedAt") or ""),
    }


def maintenance_implementation_evidence(
    root: Path,
    cli: MulticaCLI,
    issue_id: str,
    planned: dict[str, Any] | None = None,
) -> dict[str, Any]:
    issue = cli.json(["issue", "get", issue_id, "--output", "json"])
    if not isinstance(issue, dict):
        raise ReleaseError(f"Maintenance Implementation is unreadable: {issue_id}")
    metadata = metadata_map(
        cli.json(["issue", "metadata", "list", issue_id, "--output", "json"])
    )
    if str(metadata.get("workflow_object_type") or "") in {
        "development_task",
        "integration_validation",
    }:
        return development_implementation_evidence(
            root, cli, issue_id, issue, metadata, planned
        )
    if str(metadata.get("workflow_id") or "") != "development-delivery":
        raise ReleaseError("implementation provenance workflow_id is not development-delivery")
    if str(metadata.get("workflow_object_type") or "") != "maintenance_implementation":
        raise ReleaseError("implementation provenance Issue is not a maintenance_implementation")
    if str(issue.get("status") or "") != "done":
        raise ReleaseError("implementation provenance Issue must be done")
    reviewer_id = control_agent_identities(cli)["maintenance_reviewer_id"]
    if str(metadata.get("maintenance_reviewer_id") or reviewer_id) != reviewer_id:
        raise ReleaseError("implementation provenance reviewer identity differs from the managed Reviewer")
    required = {
        key: str(metadata.get(key) or "")
        for key in [
            "maintenance_change_id",
            "plan_revision",
            "reviewed_commit_sha",
            "review_comment_id",
            "github_pr_number",
            "github_merge_commit_sha",
        ]
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        raise ReleaseError(f"implementation provenance is missing fields: {missing}")
    try:
        pr_number = int(required["github_pr_number"])
    except ValueError as exc:
        raise ReleaseError("implementation provenance github_pr_number is invalid") from exc
    if planned is not None:
        issue_refs = {
            str(issue_id),
            str(issue.get("id") or ""),
            str(issue.get("identifier") or ""),
        }
        if str(planned.get("issue_id") or "") not in issue_refs:
            raise ReleaseError(
                "planned implementation provenance does not match its Multica Issue"
            )
        if int(planned.get("github_pr_number") or 0) != pr_number:
            raise ReleaseError(
                "planned implementation provenance PR differs from Multica metadata"
            )
        pr = {
            "number": pr_number,
            "state": "MERGED",
            "baseRefName": "main",
            "headRefOid": str(planned.get("reviewed_commit_sha") or ""),
            "mergeCommit": {
                "oid": str(planned.get("github_merge_commit_sha") or "")
            },
            "mergedAt": str(planned.get("github_merged_at") or ""),
        }
    else:
        pr = merged_pr_by_number(root, pr_number)
    merge_sha = str((pr.get("mergeCommit") or {}).get("oid") or "")
    if required["reviewed_commit_sha"] != str(pr.get("headRefOid") or ""):
        raise ReleaseError("implementation provenance Review is stale for its merged PR")
    if required["github_merge_commit_sha"] != merge_sha:
        raise ReleaseError("implementation provenance merge commit differs from its merged PR")
    comments = as_list(
        cli.json(
            ["issue", "comment", "list", issue_id, "--full", "--output", "json"]
        ),
        "comments",
    )
    matches = [
        item
        for item in comments
        if str(item.get("id") or "") == required["review_comment_id"]
    ]
    if len(matches) != 1:
        raise ReleaseError("implementation provenance Review comment is missing or ambiguous")
    comment = matches[0]
    content = str(comment.get("content") or "")
    first_verdict = next(
        (line.strip() for line in content.splitlines() if line.strip()), ""
    )
    if (
        comment.get("author_type") != "agent"
        or str(comment.get("author_id") or "") != reviewer_id
        or first_verdict != "APPROVED"
    ):
        raise ReleaseError("implementation provenance Review was not approved by the managed Reviewer")
    if re.findall(r"(?m)^\s*plan_revision=([^\s]+)\s*$", content) != [
        required["plan_revision"]
    ]:
        raise ReleaseError("implementation provenance Review does not bind its Plan revision")
    if re.findall(r"(?m)^\s*reviewed_commit_sha=([^\s]+)\s*$", content) != [
        required["reviewed_commit_sha"]
    ]:
        raise ReleaseError("implementation provenance Review does not bind its commit SHA")
    review_created_at = str(comment.get("created_at") or comment.get("createdAt") or "")
    if parse_iso_datetime(review_created_at, "Implementation Review comment") >= parse_iso_datetime(
        str(pr.get("mergedAt") or ""), "Implementation merged PR"
    ):
        raise ReleaseError("implementation provenance Review must predate its PR merge")
    return {
        "issue_id": str(issue.get("identifier") or issue_id),
        "maintenance_change_id": required["maintenance_change_id"],
        "plan_revision": required["plan_revision"],
        "review_comment_id": required["review_comment_id"],
        "reviewed_commit_sha": required["reviewed_commit_sha"],
        "github_pr_number": pr_number,
        "github_merge_commit_sha": merge_sha,
        "github_merged_at": str(pr.get("mergedAt") or ""),
    }


def implementation_provenance(
    root: Path,
    cli: MulticaCLI,
    issue_ids: list[str],
    source_commit: str,
    version: str,
    planned: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    normalized_ids = [str(item) for item in issue_ids if str(item)]
    if len(normalized_ids) != len(set(normalized_ids)):
        raise ReleaseError("implementation provenance Issue IDs contain duplicates")
    planned_by_issue = {
        str(item.get("issue_id") or ""): item for item in planned or []
    }
    if planned is not None and set(planned_by_issue) != set(normalized_ids):
        raise ReleaseError(
            "planned implementation provenance Issue IDs differ from the release Plan"
        )
    evidence = sorted(
        [
            maintenance_implementation_evidence(
                root, cli, item, planned_by_issue.get(item)
            )
            for item in normalized_ids
        ],
        key=lambda item: (item["github_pr_number"], item["issue_id"]),
    )
    legacy_records = [
        item
        for item in evidence
        if item.get("workflow_object_type") in {None, "maintenance_implementation"}
    ]
    development_records = [
        item
        for item in evidence
        if item.get("workflow_object_type")
        in {"development_task", "integration_validation"}
    ]
    if legacy_records and development_records:
        raise ReleaseError(
            "implementation provenance cannot mix Phase 1 and legacy maintenance Issues"
        )
    if version == "1.1.0-rc.4" and legacy_records:
        if len(evidence) != 2:
            raise ReleaseError("v1.1.0-rc.4 requires exactly two Implementation provenance Issues")
        source_matches = [
            item for item in evidence if item["github_merge_commit_sha"] == source_commit
        ]
        if len(source_matches) != 1:
            raise ReleaseError("v1.1.0-rc.4 provenance must bind the final release PR")
        prior = next(item for item in evidence if item not in source_matches)
        reachable = run(
            [
                "git",
                "merge-base",
                "--is-ancestor",
                prior["github_merge_commit_sha"],
                source_commit,
            ],
            root,
            check=False,
        )
        if reachable.returncode != 0:
            raise ReleaseError("v1.1.0-rc.4 prior Implementation PR is not in release history")
    elif development_records:
        if len(evidence) != 1:
            raise ReleaseError(
                "Phase 1 provenance must bind exactly one final integration-validation Issue"
            )
        source_matches = [
            item for item in evidence if item["github_merge_commit_sha"] == source_commit
        ]
        if len(source_matches) != 1:
            raise ReleaseError(
                "Phase 1 provenance must bind exactly one final release PR"
            )
        for item in evidence:
            if item in source_matches:
                continue
            reachable = run(
                [
                    "git",
                    "merge-base",
                    "--is-ancestor",
                    item["github_merge_commit_sha"],
                    source_commit,
                ],
                root,
                check=False,
            )
            if reachable.returncode != 0:
                raise ReleaseError(
                    "Phase 1 implementation provenance is not in release history"
                )
    return evidence


def bootstrap_evidence(
    root: Path, version: str, bootstrap_plan: str, pr: dict[str, Any]
) -> dict[str, Any]:
    if version != "1.1.0-rc.1":
        raise ReleaseError("the one-time bootstrap exception is limited to v1.1.0-rc.1")
    if bootstrap_plan not in {"v6", "docs/design-plan-v6.md"}:
        raise ReleaseError("the one-time bootstrap exception requires docs/design-plan-v6.md")
    path = root / "docs/design-plan-v6.md"
    text = path.read_text(encoding="utf-8")
    if "APPROVE WORKFLOW PLAN v6" not in text:
        raise ReleaseError("the v6 bootstrap Plan does not contain its approval gate")
    record_path = root / "docs/bootstrap-v6.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    if record.get("plan") != "docs/design-plan-v6.md" or record.get("plan_version") != "v6":
        raise ReleaseError("bootstrap approval record does not bind the v6 Plan")
    plan_hash = hashlib.sha256(path.read_bytes()).hexdigest()
    if plan_hash != str(record.get("plan_sha256") or ""):
        raise ReleaseError("current v6 Plan differs from the immutable approved Plan hash")
    approved_plan = run(
        ["git", "show", f"{record['plan_pr_merge_commit']}:{record['plan']}"], root
    ).stdout.encode("utf-8")
    if hashlib.sha256(approved_plan).hexdigest() != plan_hash:
        raise ReleaseError("approved Plan blob at the recorded merge commit differs from the current Plan")
    plan_pr = gh_json(
        root,
        [
            "pr",
            "view",
            str(record["plan_pr_number"]),
            "--repo",
            str(record["repository"]),
            "--json",
            "comments,mergeCommit",
        ],
    )
    if str((plan_pr.get("mergeCommit") or {}).get("oid") or "") != str(record["plan_pr_merge_commit"]):
        raise ReleaseError("bootstrap Plan PR merge commit does not match the reviewed approval record")
    approval_comments = [
        item
        for item in as_list(plan_pr, "comments")
        if str(item.get("id") or "") == str(record["approval_comment_id"])
        and str((item.get("author") or {}).get("login") or "").lower()
        == str(record["approver_github_login"]).lower()
        and any(
            line.strip() == str(record["approval_text"])
            for line in str(item.get("body") or "").splitlines()
        )
    ]
    if len(approval_comments) != 1:
        raise ReleaseError("bootstrap Plan approval comment is missing or has the wrong author/content")
    return {
        "mode": "bootstrap",
        "plan": "docs/design-plan-v6.md",
        "plan_sha256": plan_hash,
        "approval_record_sha256": hashlib.sha256(record_path.read_bytes()).hexdigest(),
        "plan_approval_comment_id": str(record["approval_comment_id"]),
        "approver_login": str(record["approver_github_login"]),
        "repository": str(record["repository"]),
        "pr_number": int(pr["number"]),
    }


def run(args: list[str], cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, cwd=cwd, text=True, encoding="utf-8", errors="replace", capture_output=True)
    if check and result.returncode != 0:
        raise ReleaseError(result.stderr.strip() or result.stdout.strip() or f"command failed: {' '.join(args)}")
    return result


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def issue_refs(issue_id: str, issue: dict[str, Any]) -> set[str]:
    return {
        value
        for value in {
            str(issue_id or ""),
            str(issue.get("id") or ""),
            str(issue.get("identifier") or ""),
        }
        if value
    }


def require_issue_ref(value: Any, refs: set[str], label: str) -> None:
    if str(value or "") not in refs:
        raise ReleaseError(f"{label} does not resolve to the selected Maintenance Change")


def compare_if_present(
    left: dict[str, Any],
    right: dict[str, Any],
    key: str,
    label: str,
) -> None:
    left_value = str(left.get(key) or "")
    right_value = str(right.get(key) or "")
    if left_value and right_value and left_value != right_value:
        raise ReleaseError(f"bounded Maintenance Review issue {label} does not match the selected Maintenance Change")


def normalized_batch_review_mappings(value: Any) -> list[dict[str, str]]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ReleaseError("batch_review_mappings must be a JSON array") from exc
    if not isinstance(value, list):
        raise ReleaseError("batch_review_mappings must be a JSON array")
    normalized = []
    for item in value:
        if not isinstance(item, dict):
            raise ReleaseError("batch_review_mappings items must be objects")
        mapping = {
            key: str(item.get(key) or "")
            for key in ["incident", "plan_issue", "plan_revision", "implementation"]
        }
        if not all(mapping.values()):
            raise ReleaseError("batch_review_mappings items must bind incident, plan_issue, plan_revision and implementation")
        normalized.append(mapping)
    deduped = {canonical(item): item for item in normalized}
    if len(deduped) != len(normalized):
        raise ReleaseError("batch_review_mappings contains duplicate entries")
    return sorted(
        normalized,
        key=lambda item: (
            item["incident"],
            item["plan_issue"],
            item["plan_revision"],
            item["implementation"],
        ),
    )


def review_comment_batch_mappings(content: str) -> list[dict[str, str]]:
    matches = re.findall(
        r"(?m)^\s*-\s*incident=([^\s]+)\s+plan_issue=([^\s]+)\s+plan_revision=([^\s]+)\s+implementation=([^\s]+)\s*$",
        content,
    )
    return normalized_batch_review_mappings(
        [
            {
                "incident": incident,
                "plan_issue": plan_issue,
                "plan_revision": plan_revision,
                "implementation": implementation,
            }
            for incident, plan_issue, plan_revision, implementation in matches
        ]
    )


def bounded_review_issue(
    cli: MulticaCLI,
    selected_issue_id: str,
    selected_issue: dict[str, Any],
    selected_metadata: dict[str, Any],
    identities: dict[str, str],
    review_issue_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    review_issue = cli.json(["issue", "get", review_issue_id, "--output", "json"])
    if not isinstance(review_issue, dict):
        raise ReleaseError(f"Maintenance Review issue is unreadable: {review_issue_id}")
    review_metadata = metadata_map(
        cli.json(["issue", "metadata", "list", review_issue_id, "--output", "json"])
    )
    if str(review_metadata.get("workflow_id") or "") != "development-delivery":
        raise ReleaseError("bounded Maintenance Review issue workflow_id is not development-delivery")
    if str(review_metadata.get("workflow_object_type") or "") != "maintenance_implementation":
        raise ReleaseError("bounded Maintenance Review issue is not a maintenance_implementation")
    selected_refs = issue_refs(selected_issue_id, selected_issue)
    require_issue_ref(review_issue.get("parent_issue_id"), selected_refs, "bounded Maintenance Review issue parent")
    require_issue_ref(
        review_metadata.get("maintenance_change_id"),
        selected_refs,
        "bounded Maintenance Review issue maintenance_change_id",
    )
    for key in [
        "source_incident_id",
        "source_issue",
        "source_issue_id",
        "source_requirement_id",
        "workflow_incident_id",
    ]:
        compare_if_present(selected_metadata, review_metadata, key, key)
    for key in [
        "review_comment_id",
        "reviewed_commit_sha",
        "plan_revision",
        "github_pr_number",
        "github_merge_commit_sha",
    ]:
        compare_if_present(selected_metadata, review_metadata, key, key)
    for key in ["maintenance_reviewer_id", "reviewer_agent_id"]:
        value = str(review_metadata.get(key) or "")
        if value and value != identities["maintenance_reviewer_id"]:
            raise ReleaseError("bounded Maintenance Review issue reviewer identity does not match the managed Maintenance Reviewer")
    return review_issue, review_metadata


def maintenance_github_provenance(
    authorization: dict[str, Any], release_approval: dict[str, Any]
) -> dict[str, str]:
    issue_id = str(authorization.get("issue_id") or "")
    review_comment_id = str(authorization.get("review_comment_id") or "")
    approval_comment_id = str(release_approval.get("comment_id") or "")
    approval_author_id = str(release_approval.get("author_id") or "")
    if not all([issue_id, review_comment_id, approval_comment_id, approval_author_id]):
        raise ReleaseError("maintenance release provenance is incomplete")
    provenance = {
        "maintenance_issue": issue_id,
        "review_comment_id": review_comment_id,
        "multica_approval_comment_id": approval_comment_id,
        "maintenance_evidence_sha256": digest(authorization),
        "multica_approval_author_sha256": hashlib.sha256(
            approval_author_id.encode("utf-8")
        ).hexdigest(),
    }
    for key in [
        "authorization_type",
        "release_path",
        "workflow_object_type",
        "review_issue_id",
        "root_requirement_id",
        "incident_id",
        "batch_review_mappings_sha256",
    ]:
        value = str(authorization.get(key) or "")
        if value:
            provenance[key] = value
    if authorization.get("recovery_mode") == RC2_RECOVERY_MODE:
        for key in [
            "recovery_mode",
            "pending_incident_source",
            "recovery_decision_comment_id",
            "control_identity_sha256",
            "pending_incident_evidence_sha256",
            "recovery_decision_sha256",
        ]:
            value = str(authorization.get(key) or "")
            if not value:
                raise ReleaseError(f"recovery release provenance is missing {key}")
            provenance[key] = value
    return provenance


def github_release_approval_block(
    digest_value: str, provenance: dict[str, str] | None = None
) -> str:
    lines = [f"APPROVE WORKFLOW RELEASE {digest_value[:12]}"]
    lines.extend(f"{key}={value}" for key, value in (provenance or {}).items())
    return "\n".join(lines)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_iso_datetime(value: str, label: str) -> datetime:
    if not value:
        raise ReleaseError(f"{label} timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReleaseError(f"{label} timestamp is invalid: {value}") from exc
    if parsed.tzinfo is None:
        raise ReleaseError(f"{label} timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def git_head(root: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], root).stdout.strip()


def git_dirty(root: Path) -> bool:
    return bool(run(["git", "status", "--porcelain"], root).stdout.strip())


def verify_origin_main_reachability(
    root: Path, commit: str, expected_origin_main: str | None = None
) -> str:
    run(["git", "fetch", "--no-tags", "origin", "main"], root)
    origin_main = run(["git", "rev-parse", "origin/main"], root).stdout.strip()
    if not origin_main:
        raise ReleaseError("origin/main is unavailable after fetch")
    if expected_origin_main and origin_main != expected_origin_main:
        raise ReleaseError("origin/main changed after release planning")
    reachable = run(
        ["git", "merge-base", "--is-ancestor", commit, origin_main],
        root,
        check=False,
    )
    if reachable.returncode != 0:
        raise ReleaseError("release commit is not reachable from current origin/main")
    return origin_main


def gh_json(root: Path, args: list[str]) -> Any:
    result = run(["gh", *args], root)
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"gh returned invalid JSON for {' '.join(args[:3])}") from exc


def gh_api_json(
    root: Path, method: str, endpoint: str, payload: dict[str, Any]
) -> Any:
    result = subprocess.run(
        ["gh", "api", "--method", method, endpoint, "--input", "-"],
        cwd=root,
        input=canonical(payload),
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode != 0:
        raise ReleaseError(
            result.stderr.strip()
            or result.stdout.strip()
            or f"GitHub API {method} {endpoint} failed"
        )
    try:
        return json.loads(result.stdout) if result.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        raise ReleaseError(f"GitHub API returned invalid JSON for {method} {endpoint}") from exc


def release_control(root: Path) -> dict[str, Any]:
    path = root / RELEASE_CONTROL_PATH
    try:
        control = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"release control is unreadable: {path}") from exc
    required = {
        "schema_version": 2,
        "repository": None,
        "required_visibility": "public",
        "environment": None,
        "deployment_branch": None,
        "operator_type": "github_app",
        "dispatcher_app_slug": None,
        "publisher_app_slug": None,
        "publisher_app_id_variable": None,
        "publisher_private_key_secret": None,
        "tag_ruleset": None,
        "administrator_evidence_path": None,
    }
    missing = [key for key, value in required.items() if key not in control or not control.get(key)]
    if missing:
        raise ReleaseError(f"release control is missing fields: {missing}")
    if control.get("schema_version") != 2:
        raise ReleaseError("unsupported release control schema_version")
    if control.get("operator_type") != required["operator_type"]:
        raise ReleaseError("release operator must be a dedicated GitHub App")
    if control.get("required_visibility") != required["required_visibility"]:
        raise ReleaseError("release repository visibility must be public")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", str(control["repository"])):
        raise ReleaseError("release control repository is invalid")
    for key in [
        "environment",
        "deployment_branch",
        "tag_ruleset",
        "dispatcher_app_slug",
        "publisher_app_slug",
        "publisher_app_id_variable",
        "publisher_private_key_secret",
    ]:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(control[key])):
            raise ReleaseError(f"release control {key} is invalid")
    evidence_path = Path(str(control["administrator_evidence_path"]))
    if evidence_path.is_absolute() or ".." in evidence_path.parts:
        raise ReleaseError("release control administrator evidence path must be repository-relative")
    return control


def reviewed_app_installation(
    control: dict[str, Any],
    evidence: dict[str, Any],
    *,
    evidence_key: str,
    control_slug_key: str,
    expected_permissions: dict[str, str],
    label: str,
) -> dict[str, Any]:
    app = evidence.get(evidence_key) or {}
    if str(app.get("slug") or "") != str(control[control_slug_key]):
        raise ReleaseError(
            f"release-control administrator evidence {label} App slug differs"
        )
    if int(app.get("id") or 0) <= 0:
        raise ReleaseError(
            f"release-control administrator evidence {label} App ID is invalid"
        )
    if int(app.get("installation_id") or 0) <= 0:
        raise ReleaseError(
            f"release-control administrator evidence {label} installation ID is invalid"
        )
    expected_owner = str(control["repository"]).split("/", 1)[0].lower()
    if str(app.get("account_login") or "").lower() != expected_owner:
        raise ReleaseError(
            f"release-control administrator evidence {label} account differs"
        )
    if app.get("repository_selection") != "selected":
        raise ReleaseError(f"{label} App installation must use selected repositories")
    repositories = sorted(str(item) for item in app.get("repositories") or [])
    if repositories != [str(control["repository"])]:
        raise ReleaseError(
            f"{label} App installation must be limited to the workflow repository"
        )
    permissions = dict(sorted((app.get("permissions") or {}).items()))
    if permissions != dict(sorted(expected_permissions.items())):
        raise ReleaseError(
            f"{label} App permissions differ from the least-privilege contract"
        )
    return app


def app_installation_snapshot(app: dict[str, Any]) -> dict[str, Any]:
    snapshot = {
        "app_id": int(app.get("id") or app.get("app_id") or 0),
        "app_slug": str(app.get("slug") or app.get("app_slug") or ""),
        "installation_id": int(app.get("installation_id") or 0),
        "account_login": str(app.get("account_login") or ""),
        "repository_selection": str(app.get("repository_selection") or ""),
        "repositories": sorted(str(item) for item in app.get("repositories") or []),
        "permissions": dict(sorted((app.get("permissions") or {}).items())),
    }
    snapshot["sha256"] = digest(snapshot)
    return snapshot


def github_app_actor_login(slug: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9-]+", slug):
        raise ReleaseError("GitHub App slug is invalid for actor binding")
    return f"{slug.lower()}[bot]"


def load_release_control_evidence(root: Path, control: dict[str, Any]) -> dict[str, Any]:
    path = root / str(control["administrator_evidence_path"])
    try:
        evidence = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("reviewed release-control administrator evidence is unreadable") from exc
    if not isinstance(evidence, dict) or evidence.get("schema_version") != 1:
        raise ReleaseError("release-control administrator evidence schema is invalid")
    if evidence.get("status") != "reviewed":
        raise ReleaseError("release-control administrator evidence is still pending human administration")
    if str(evidence.get("repository") or "") != str(control["repository"]):
        raise ReleaseError("release-control administrator evidence repository differs from control")
    if not str(evidence.get("multica_evidence_id") or ""):
        raise ReleaseError("release-control administrator evidence has no Multica evidence ID")
    reviewed_app_installation(
        control,
        evidence,
        evidence_key="dispatcher_app",
        control_slug_key="dispatcher_app_slug",
        expected_permissions={
            "actions": "write",
            "contents": "read",
            "metadata": "read",
        },
        label="Dispatcher",
    )
    publisher = reviewed_app_installation(
        control,
        evidence,
        evidence_key="publisher_app",
        control_slug_key="publisher_app_slug",
        expected_permissions={"contents": "write", "metadata": "read"},
        label="Publisher",
    )
    ruleset = evidence.get("tag_ruleset") or {}
    bypass = ruleset.get("bypass_actors") or []
    expected_bypass = [
        {
            "actor_type": "Integration",
            "actor_id": int(publisher["id"]),
            "bypass_mode": "always",
        }
    ]
    if bypass != expected_bypass:
        raise ReleaseError("release-control administrator evidence must bind the Publisher App as sole bypass")
    evidence["sha256"] = digest({key: value for key, value in evidence.items() if key != "sha256"})
    return evidence


def repository_control_state(root: Path, control: dict[str, Any]) -> dict[str, Any]:
    repository = str(control["repository"])
    detail = gh_json(root, ["api", f"repos/{repository}"])
    if not isinstance(detail, dict):
        raise ReleaseError("GitHub repository response is invalid")
    visibility = str(detail.get("visibility") or "").lower()
    if not visibility:
        visibility = "private" if detail.get("private") else "public"
    expected_visibility = str(control["required_visibility"]).lower()
    if visibility != expected_visibility:
        raise ReleaseError(
            f"GitHub repository visibility must be {expected_visibility}; found {visibility}"
        )
    expected_owner = repository.split("/", 1)[0].lower()
    owner = str(((detail.get("owner") or {}).get("login")) or "").lower()
    if owner != expected_owner:
        raise ReleaseError(
            f"GitHub repository owner differs from release control: {owner or '<missing>'}"
        )
    default_branch = str(detail.get("default_branch") or "")
    if default_branch != str(control["deployment_branch"]):
        raise ReleaseError("GitHub repository default branch differs from release control")
    permissions = detail.get("permissions") or {}
    return {
        "repository": repository,
        "owner_login": owner,
        "visibility": visibility,
        "default_branch": default_branch,
        "permissions": {
            key: bool(permissions.get(key))
            for key in ["admin", "maintain", "push", "triage", "pull"]
        },
    }


def verify_release_tag_ruleset(
    root: Path, control: dict[str, Any], evidence: dict[str, Any]
) -> dict[str, Any]:
    repository = str(control["repository"])
    expected = evidence.get("tag_ruleset") or {}
    try:
        rulesets = gh_json(root, ["api", f"repos/{repository}/rulesets"])
    except ReleaseError as exc:
        raise ReleaseError("GitHub release tag rulesets are missing or unreadable") from exc
    matches = [
        item
        for item in rulesets
        if isinstance(item, dict)
        and str(item.get("name") or "") == str(control["tag_ruleset"])
    ] if isinstance(rulesets, list) else []
    if len(matches) != 1:
        raise ReleaseError(
            f"expected one GitHub tag ruleset {control['tag_ruleset']}, found {len(matches)}"
        )
    ruleset_id = str(matches[0].get("id") or "")
    if ruleset_id != str(expected.get("id") or ""):
        raise ReleaseError("GitHub release tag Ruleset ID differs from administrator evidence")
    detail = gh_json(root, ["api", f"repos/{repository}/rulesets/{ruleset_id}"])
    if not isinstance(detail, dict):
        raise ReleaseError("GitHub release tag ruleset response is invalid")
    if detail.get("target") != "tag" or detail.get("enforcement") != "active":
        raise ReleaseError("GitHub release tag ruleset must be active and target tags")
    conditions = detail.get("conditions") or {}
    if set(conditions) != {"ref_name"}:
        raise ReleaseError("GitHub release tag ruleset must use only ref_name conditions")
    ref_name = conditions.get("ref_name") or {}
    if set(ref_name.get("include") or []) != {"refs/tags/v*"} or ref_name.get("exclude"):
        raise ReleaseError("GitHub release tag ruleset must match only refs/tags/v*")
    rule_types = {
        str(item.get("type") or "")
        for item in detail.get("rules") or []
        if isinstance(item, dict)
    }
    required_rules = {"creation", "update", "deletion"}
    if not required_rules.issubset(rule_types):
        raise ReleaseError(
            f"GitHub release tag ruleset is missing restrictions: {sorted(required_rules - rule_types)}"
        )
    publisher = evidence.get("publisher_app") or {}
    app = gh_json(root, ["api", f"apps/{publisher['slug']}"])
    if (
        not isinstance(app, dict)
        or int(app.get("id") or 0) != int(publisher["id"])
        or str(app.get("slug") or "") != str(publisher["slug"])
    ):
        raise ReleaseError("GitHub Publisher App public identity differs from administrator evidence")
    observed_bypass = [item for item in detail.get("bypass_actors") or [] if isinstance(item, dict)]
    if observed_bypass and observed_bypass != expected.get("bypass_actors"):
        raise ReleaseError("GitHub release tag Ruleset bypass actors differ from administrator evidence")
    normalized_rules = []
    for item in detail.get("rules") or []:
        if not isinstance(item, dict):
            raise ReleaseError("GitHub release tag ruleset contains an invalid rule")
        normalized = {"type": str(item.get("type") or "")}
        if "parameters" in item:
            normalized["parameters"] = item["parameters"]
        normalized_rules.append(normalized)
    snapshot = {
        "name": str(control["tag_ruleset"]),
        "id": ruleset_id,
        "source": str(detail.get("source") or expected.get("source") or ""),
        "target": str(detail.get("target") or ""),
        "enforcement": str(detail.get("enforcement") or ""),
        "updated_at": str(detail.get("updated_at") or ""),
        "publisher_app_id": int(publisher["id"]),
        "publisher_app_slug": str(publisher["slug"]),
        "rules": sorted(normalized_rules, key=canonical),
        "include": sorted(ref_name.get("include") or []),
        "exclude": sorted(ref_name.get("exclude") or []),
        "bypass": expected.get("bypass_actors"),
        "administrator_evidence_sha256": evidence["sha256"],
        "administrator_evidence_id": str(evidence["multica_evidence_id"]),
    }
    expected_public = {
        "id": str(expected.get("id") or ""),
        "name": str(expected.get("name") or ""),
        "source": str(expected.get("source") or ""),
        "target": str(expected.get("target") or ""),
        "enforcement": str(expected.get("enforcement") or ""),
        "updated_at": str(expected.get("updated_at") or ""),
        "rules": sorted(
            [
                {
                    **{"type": str(item.get("type") or "")},
                    **({"parameters": item["parameters"]} if "parameters" in item else {}),
                }
                for item in expected.get("rules") or []
                if isinstance(item, dict)
            ],
            key=canonical,
        ),
        "include": sorted(((expected.get("conditions") or {}).get("ref_name") or {}).get("include") or []),
        "exclude": sorted(((expected.get("conditions") or {}).get("ref_name") or {}).get("exclude") or []),
    }
    observed_public = {key: snapshot[key] for key in expected_public}
    if observed_public != expected_public:
        raise ReleaseError("GitHub release tag Ruleset public readback differs from administrator evidence")
    snapshot["sha256"] = digest(snapshot)
    return snapshot


def verify_app_installation(
    root: Path,
    *,
    expected: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    if not os.environ.get("GH_TOKEN"):
        raise ReleaseError(
            f"{label} App token is missing; provide it explicitly through GH_TOKEN"
        )
    installation = gh_json(root, ["api", "installation"])
    repositories_value = gh_json(
        root, ["api", "installation/repositories?per_page=100"]
    )
    if not isinstance(installation, dict) or not isinstance(repositories_value, dict):
        raise ReleaseError(f"{label} App installation readback is invalid")
    repositories = sorted(
        str(item.get("full_name") or "")
        for item in repositories_value.get("repositories") or []
        if isinstance(item, dict) and item.get("full_name")
    )
    observed = {
        "app_id": int(installation.get("app_id") or 0),
        "app_slug": str(installation.get("app_slug") or ""),
        "installation_id": int(installation.get("id") or 0),
        "account_login": str(((installation.get("account") or {}).get("login")) or ""),
        "repository_selection": str(installation.get("repository_selection") or ""),
        "repositories": repositories,
        "permissions": dict(sorted((installation.get("permissions") or {}).items())),
    }
    required = app_installation_snapshot(expected)
    required.pop("sha256")
    if observed != required:
        raise ReleaseError(
            f"{label} App token does not match reviewed installation evidence"
        )
    if int(repositories_value.get("total_count") or len(repositories)) != len(
        repositories
    ):
        raise ReleaseError(f"{label} App repository readback is incomplete")
    observed["sha256"] = digest(observed)
    return observed


def environment_protection_snapshot(
    detail: dict[str, Any],
) -> tuple[list[dict[str, Any]], set[str], bool]:
    normalized_rules = []
    reviewers: set[str] = set()
    prevent_self_review = False
    reviewer_rule_count = 0
    for rule in detail.get("protection_rules") or []:
        if not isinstance(rule, dict):
            raise ReleaseError("GitHub release Environment contains an invalid protection rule")
        rule_type = str(rule.get("type") or "")
        if rule_type == "wait_timer":
            normalized_rules.append(
                {"type": rule_type, "wait_timer": int(rule.get("wait_timer") or 0)}
            )
            continue
        if rule_type != "required_reviewers":
            raise ReleaseError(
                f"GitHub release Environment has an unsupported protection rule: {rule_type or '<missing>'}"
            )
        reviewer_rule_count += 1
        prevent_self_review = bool(rule.get("prevent_self_review"))
        normalized_reviewers = []
        for item in rule.get("reviewers") or []:
            if not isinstance(item, dict) or str(item.get("type") or "").lower() != "user":
                raise ReleaseError(
                    "GitHub release Environment reviewers must be explicit human users"
                )
            reviewer = item.get("reviewer") or {}
            login = str(reviewer.get("login") or "").lower()
            reviewer_id = int(reviewer.get("id") or 0)
            if not login or not reviewer_id:
                raise ReleaseError("GitHub release Environment reviewer identity is incomplete")
            reviewers.add(login)
            normalized_reviewers.append({"type": "User", "id": reviewer_id, "login": login})
        normalized_rules.append(
            {
                "type": rule_type,
                "prevent_self_review": prevent_self_review,
                "reviewers": sorted(normalized_reviewers, key=canonical),
            }
        )
    if reviewer_rule_count != 1:
        raise ReleaseError(
            "GitHub release Environment must have exactly one required_reviewers rule"
        )
    return sorted(normalized_rules, key=canonical), reviewers, prevent_self_review


def environment_admin_bypass_evidence(detail: dict[str, Any]) -> dict[str, Any]:
    if "can_admins_bypass" in detail:
        can_bypass = bool(detail.get("can_admins_bypass"))
        if can_bypass:
            raise ReleaseError(
                "GitHub release Environment must disable administrator bypass"
            )
        return {
            "api_field": "can_admins_bypass",
            "readback_supported": True,
            "can_admins_bypass": False,
        }
    return {
        "api_field": "can_admins_bypass",
        "readback_supported": False,
        "status": "not_exposed_by_rest_api",
        "residual_risk": "repository_owner_can_reconfigure_release_controls",
    }


def verify_release_environment(
    root: Path,
    *,
    verify_dispatcher_token: bool = True,
    dispatcher_installation: dict[str, Any] | None = None,
) -> dict[str, Any]:
    control = release_control(root)
    evidence = load_release_control_evidence(root, control)
    dispatcher_evidence = evidence.get("dispatcher_app") or {}
    expected_dispatcher = app_installation_snapshot(dispatcher_evidence)
    if dispatcher_installation is not None:
        if dispatcher_installation != expected_dispatcher:
            raise ReleaseError(
                "Dispatcher installation differs from reviewed release-control evidence"
            )
        dispatcher = dispatcher_installation
    elif verify_dispatcher_token:
        dispatcher = verify_dispatcher_installation(root, control, evidence)
    else:
        dispatcher = expected_dispatcher
    repository = str(control["repository"])
    repository_state = repository_control_state(root, control)
    environment = str(control["environment"])
    try:
        detail = gh_json(
            root,
            ["api", f"repos/{repository}/environments/{environment}"],
        )
    except ReleaseError as exc:
        raise ReleaseError(
            f"GitHub release Environment is missing or unreadable: {environment}"
        ) from exc
    if not isinstance(detail, dict):
        raise ReleaseError("GitHub release Environment response is invalid")
    environment_rules, reviewers, prevent_self_review = environment_protection_snapshot(
        detail
    )
    if not reviewers:
        raise ReleaseError("GitHub release Environment has no required human reviewer")
    if not prevent_self_review:
        raise ReleaseError("GitHub release Environment must prevent self-review")
    admin_bypass = environment_admin_bypass_evidence(detail)

    policy = detail.get("deployment_branch_policy") or {}
    if policy.get("protected_branches") or not policy.get("custom_branch_policies"):
        raise ReleaseError("GitHub release Environment must use a custom main-only branch policy")
    policies = gh_json(
        root,
        [
            "api",
            f"repos/{repository}/environments/{environment}/deployment-branch-policies",
        ],
    )
    branches = {
        str(item.get("name") or "")
        for item in as_list(policies, "branch_policies")
        if item.get("name")
    }
    expected_branch = str(control["deployment_branch"])
    if branches != {expected_branch}:
        raise ReleaseError(
            f"GitHub release Environment branch policies must equal [{expected_branch}]"
        )
    environment_snapshot = {
        "id": int(detail.get("id") or 0),
        "name": environment,
        "protection_rules": environment_rules,
        "admin_bypass": admin_bypass,
        "deployment_branch_policy": {
            "protected_branches": bool(policy.get("protected_branches")),
            "custom_branch_policies": bool(policy.get("custom_branch_policies")),
        },
        "deployment_branches": sorted(branches),
    }
    if not environment_snapshot["id"]:
        raise ReleaseError("GitHub release Environment identity is incomplete")
    environment_snapshot["sha256"] = digest(environment_snapshot)
    evidence_environment = evidence.get("environment") or {}
    if (
        int(evidence_environment.get("id") or 0) != environment_snapshot["id"]
        or str(evidence_environment.get("name") or "") != environment
        or str(evidence_environment.get("sha256") or "") != environment_snapshot["sha256"]
    ):
        raise ReleaseError("GitHub release Environment differs from administrator evidence")
    tag_ruleset = verify_release_tag_ruleset(root, control, evidence)
    publisher = evidence.get("publisher_app") or {}
    return {
        "repository": repository,
        "repository_owner": repository_state["owner_login"],
        "repository_visibility": repository_state["visibility"],
        "repository_default_branch": repository_state["default_branch"],
        "environment": environment,
        "deployment_branch": expected_branch,
        "operator_type": str(control["operator_type"]),
        "dispatcher_app_id": int(dispatcher["app_id"]),
        "dispatcher_app_slug": str(dispatcher["app_slug"]),
        "dispatcher_actor_login": github_app_actor_login(
            str(dispatcher["app_slug"])
        ),
        "dispatcher_installation_id": int(dispatcher["installation_id"]),
        "dispatcher_account_login": str(dispatcher["account_login"]),
        "dispatcher_repository_selection": str(dispatcher["repository_selection"]),
        "dispatcher_repositories": sorted(str(item) for item in dispatcher["repositories"]),
        "dispatcher_permissions": dict(sorted(dispatcher["permissions"].items())),
        "dispatcher_installation_sha256": str(dispatcher["sha256"]),
        "publisher_app_id": int(publisher.get("id") or 0),
        "publisher_app_slug": str(publisher.get("slug") or ""),
        "publisher_installation_id": int(publisher.get("installation_id") or 0),
        "publisher_account_login": str(publisher.get("account_login") or ""),
        "publisher_repository_selection": str(publisher.get("repository_selection") or ""),
        "publisher_repositories": sorted(str(item) for item in publisher.get("repositories") or []),
        "publisher_permissions": dict(sorted((publisher.get("permissions") or {}).items())),
        "administrator_evidence_sha256": str(evidence["sha256"]),
        "administrator_evidence_id": str(evidence["multica_evidence_id"]),
        "reviewers": sorted(reviewers),
        "prevent_self_review": prevent_self_review,
        "admin_bypass": admin_bypass,
        "environment_sha256": environment_snapshot["sha256"],
        "tag_ruleset": tag_ruleset,
        "dispatcher_token_verified": verify_dispatcher_token,
    }


def release_boundary_snapshot(boundary: dict[str, Any]) -> dict[str, Any]:
    ruleset = boundary.get("tag_ruleset") or {}
    return {
        "repository": str(boundary["repository"]),
        "repository_owner": str(boundary["repository_owner"]),
        "repository_visibility": str(boundary["repository_visibility"]),
        "repository_default_branch": str(boundary["repository_default_branch"]),
        "environment": str(boundary["environment"]),
        "environment_sha256": str(boundary["environment_sha256"]),
        "environment_admin_bypass": boundary["admin_bypass"],
        "deployment_branch": str(boundary["deployment_branch"]),
        "operator_type": str(boundary["operator_type"]),
        "dispatcher_app_id": int(boundary["dispatcher_app_id"]),
        "dispatcher_app_slug": str(boundary["dispatcher_app_slug"]),
        "dispatcher_actor_login": str(boundary["dispatcher_actor_login"]),
        "dispatcher_installation_id": int(boundary["dispatcher_installation_id"]),
        "dispatcher_account_login": str(boundary["dispatcher_account_login"]),
        "dispatcher_repository_selection": str(boundary["dispatcher_repository_selection"]),
        "dispatcher_repositories": sorted(str(item) for item in boundary["dispatcher_repositories"]),
        "dispatcher_permissions": dict(sorted(boundary["dispatcher_permissions"].items())),
        "dispatcher_installation_sha256": str(boundary["dispatcher_installation_sha256"]),
        "publisher_app_id": int(boundary["publisher_app_id"]),
        "publisher_app_slug": str(boundary["publisher_app_slug"]),
        "publisher_installation_id": int(boundary["publisher_installation_id"]),
        "publisher_account_login": str(boundary["publisher_account_login"]),
        "publisher_repository_selection": str(boundary["publisher_repository_selection"]),
        "publisher_repositories": sorted(str(item) for item in boundary["publisher_repositories"]),
        "publisher_permissions": dict(sorted(boundary["publisher_permissions"].items())),
        "administrator_evidence_sha256": str(boundary["administrator_evidence_sha256"]),
        "administrator_evidence_id": str(boundary["administrator_evidence_id"]),
        "tag_ruleset_name": str(ruleset.get("name") or ""),
        "tag_ruleset_id": str(ruleset.get("id") or ""),
        "tag_ruleset_sha256": str(ruleset.get("sha256") or ""),
        "tag_ruleset_updated_at": str(ruleset.get("updated_at") or ""),
    }


def verify_dispatcher_installation(
    root: Path,
    control: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    control = control or release_control(root)
    evidence = evidence or load_release_control_evidence(root, control)
    return verify_app_installation(
        root,
        expected=evidence.get("dispatcher_app") or {},
        label="Dispatcher",
    )


def verify_publisher_installation(
    root: Path,
    control: dict[str, Any] | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    control = control or release_control(root)
    evidence = evidence or load_release_control_evidence(root, control)
    return verify_app_installation(
        root,
        expected=evidence.get("publisher_app") or {},
        label="Publisher",
    )


def approval_environment_names(value: dict[str, Any]) -> set[str]:
    return {
        str(item.get("name") or "")
        for item in value.get("environments") or []
        if isinstance(item, dict) and item.get("name")
    }


def verify_environment_approval(
    root: Path,
    run_id: str,
    *,
    expected_actor: str | None = None,
    verify_configuration: bool = True,
    verify_dispatcher_token: bool = False,
) -> dict[str, str]:
    if verify_configuration:
        boundary = verify_release_environment(
            root, verify_dispatcher_token=verify_dispatcher_token
        )
        reviewers = set(boundary["reviewers"])
    else:
        control = release_control(root)
        boundary = {
            "repository": str(control["repository"]),
            "environment": str(control["environment"]),
        }
        reviewers = set()
    value = gh_json(
        root,
        [
            "api",
            f"repos/{boundary['repository']}/actions/runs/{run_id}/approvals",
        ],
    )
    history = value if isinstance(value, list) else as_list(value, "approvals")
    matches = []
    for item in history:
        if str(item.get("state") or "").lower() != "approved":
            continue
        if boundary["environment"] not in approval_environment_names(item):
            continue
        actor = str(((item.get("user") or {}).get("login")) or "").lower()
        if not actor:
            continue
        if reviewers and actor not in reviewers:
            continue
        if expected_actor and actor != expected_actor.lower():
            continue
        matches.append((item, actor))
    if len(matches) != 1:
        raise ReleaseError(
            "expected exactly one protected Environment approval from an isolated reviewer; "
            f"found {len(matches)}"
        )
    item, actor = matches[0]
    approval_evidence = {
        "run_id": str(run_id),
        "environment": str(boundary["environment"]),
        "actor_login": actor,
        "state": "approved",
        "submitted_at": str(
            item.get("submitted_at") or item.get("created_at") or ""
        ),
    }
    return {
        "run_id": str(run_id),
        "environment": str(boundary["environment"]),
        "actor_login": actor,
        "approval_sha256": digest(approval_evidence),
    }


def publish_gate_record(
    request: dict[str, Any],
    boundary: dict[str, Any],
    approval: dict[str, str],
) -> dict[str, Any]:
    snapshot = release_boundary_snapshot(boundary)
    if snapshot != request.get("release_boundary"):
        raise ReleaseError("protected release control changed after request dispatch")
    record = {
        "schema_version": 1,
        "created_at": utc_now(),
        "repository": snapshot["repository"],
        "workflow_run_id": str(approval["run_id"]),
        "environment": str(approval["environment"]),
        "release_request_digest": str(request["release_request_digest"]),
        "release_plan_digest": str(request["release_plan_digest"]),
        "source_commit": str(request["source_commit"]),
        "tag": str(request["tag"]),
        "release_boundary_sha256": digest(snapshot),
        "environment_approval": approval,
        "dispatcher": {
            "app_id": int(snapshot["dispatcher_app_id"]),
            "app_slug": str(snapshot["dispatcher_app_slug"]),
            "actor_login": str(snapshot["dispatcher_actor_login"]),
            "installation_id": int(snapshot["dispatcher_installation_id"]),
            "installation_sha256": str(snapshot["dispatcher_installation_sha256"]),
        },
        "publisher": {
            "app_id": int(snapshot["publisher_app_id"]),
            "app_slug": str(snapshot["publisher_app_slug"]),
            "installation_id": int(snapshot["publisher_installation_id"]),
        },
    }
    record["publish_gate_digest"] = digest(record)
    return record


def load_publish_gate(path: str | Path) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError("publish gate record is unreadable") from exc
    if not isinstance(value, dict):
        raise ReleaseError("publish gate record must be an object")
    return value


def verify_publish_gate(
    gate: dict[str, Any],
    request: dict[str, Any],
    approval: str,
    *,
    workflow_run_id: str,
    environment: str,
) -> str:
    expected = str(gate.get("publish_gate_digest") or "")
    payload = {key: value for key, value in gate.items() if key != "publish_gate_digest"}
    if not expected or digest(payload) != expected:
        raise ReleaseError("publish gate digest is invalid or the gate was modified")
    if approval not in {expected, expected[:12]}:
        raise ReleaseError("publish gate approval digest does not match")
    if gate.get("schema_version") != 1:
        raise ReleaseError("unsupported publish gate schema_version")
    expected_values = {
        "repository": str((request.get("release_boundary") or {}).get("repository") or ""),
        "workflow_run_id": str(workflow_run_id),
        "environment": str(environment),
        "release_request_digest": str(request.get("release_request_digest") or ""),
        "release_plan_digest": str(request.get("release_plan_digest") or ""),
        "source_commit": str(request.get("source_commit") or ""),
        "tag": str(request.get("tag") or ""),
        "release_boundary_sha256": digest(request.get("release_boundary") or {}),
    }
    changed = {
        key: {"expected": value, "observed": gate.get(key)}
        for key, value in expected_values.items()
        if str(gate.get(key) or "") != value
    }
    if changed:
        raise ReleaseError(f"publish gate does not match the release Request: {changed}")
    boundary = request.get("release_boundary") or {}
    expected_dispatcher = {
        "app_id": int(boundary.get("dispatcher_app_id") or 0),
        "app_slug": str(boundary.get("dispatcher_app_slug") or ""),
        "actor_login": str(boundary.get("dispatcher_actor_login") or ""),
        "installation_id": int(boundary.get("dispatcher_installation_id") or 0),
        "installation_sha256": str(
            boundary.get("dispatcher_installation_sha256") or ""
        ),
    }
    if gate.get("dispatcher") != expected_dispatcher:
        raise ReleaseError("publish gate Dispatcher identity differs from the release Request")
    expected_publisher = {
        "app_id": int(boundary.get("publisher_app_id") or 0),
        "app_slug": str(boundary.get("publisher_app_slug") or ""),
        "installation_id": int(boundary.get("publisher_installation_id") or 0),
    }
    if gate.get("publisher") != expected_publisher:
        raise ReleaseError("publish gate Publisher identity differs from the release Request")
    return expected


def release_request(
    root: Path,
    plan: dict[str, Any],
    release_approval: dict[str, Any],
    boundary: dict[str, Any],
) -> dict[str, Any]:
    authorization = plan.get("release_authorization") or {}
    if authorization.get("mode") != "maintenance":
        raise ReleaseError(
            "protected Environment releases require Phase 1 development authorization"
        )
    provenance = maintenance_github_provenance(authorization, release_approval)
    control = release_control(root)
    request = {
        "schema_version": 1,
        "created_at": plan["created_at"],
        "version": plan["version"],
        "tag": plan["tag"],
        "source_commit": plan["source_commit"],
        "origin_main_sha": plan["origin_main_sha"],
        "source_hash": plan["source_hash"],
        "merged_pr": plan["merged_pr"],
        "validation": plan["validation"],
        "version_files": plan["version_files"],
        "changelog_hash": plan["changelog_hash"],
        "expected_assets": plan["expected_assets"],
        "release_plan_digest": plan["release_plan_digest"],
        "maintenance_provenance": provenance,
        "implementation_provenance": plan.get("implementation_provenance") or [],
        "implementation_provenance_sha256": digest(
            plan.get("implementation_provenance") or []
        ),
        "release_control": {
            key: control[key] for key in RELEASE_CONTROL_KEYS
        },
        "release_boundary": release_boundary_snapshot(boundary),
    }
    request["release_request_digest"] = digest(request)
    return request


def phase1_expected_assets(version: str) -> list[str]:
    return sorted(
        [
            "checksums.txt",
            f"multica-dev-workflow-v{version}.zip",
            f"multica-requirement-intake-v{version}.zip",
            f"multica-workflow-manager-v{version}.zip",
            f"multica-workflow-observer-v{version}.zip",
            f"multica-workflow-console-v{version}.zip",
        ]
    )


def validate_release_version(version: str) -> str:
    if not RELEASE_VERSION_RE.fullmatch(version):
        raise ReleaseError("release version must be a valid semantic version")
    return version


def verify_release_request_payload(request: dict[str, Any]) -> str:
    expected = str(request.get("release_request_digest") or "")
    payload = {
        key: value for key, value in request.items() if key != "release_request_digest"
    }
    if not expected or digest(payload) != expected:
        raise ReleaseError("release Request digest is invalid or the request was modified")
    if request.get("schema_version") != 1:
        raise ReleaseError("unsupported release Request schema_version")
    version = validate_release_version(str(request.get("version") or ""))
    if str(request.get("tag") or "") != f"v{version}":
        raise ReleaseError("release Request tag must equal v plus version")
    source_commit = str(request.get("source_commit") or "")
    if not re.fullmatch(r"[a-f0-9]{40,64}", source_commit):
        raise ReleaseError("release Request source_commit is invalid")
    if str(request.get("origin_main_sha") or "") != source_commit:
        raise ReleaseError("release Request origin_main_sha must equal source_commit")
    for key in [
        "source_hash",
        "changelog_hash",
        "release_plan_digest",
    ]:
        if not re.fullmatch(r"[a-f0-9]{64}", str(request.get(key) or "")):
            raise ReleaseError(f"release Request {key} is invalid")
    if normalized_expected_assets(request.get("expected_assets")) != phase1_expected_assets(
        version
    ):
        raise ReleaseError("release Request does not contain the exact Phase 1 asset set")
    provenance = request.get("maintenance_provenance")
    if not isinstance(provenance, dict):
        raise ReleaseError("release Request Maintenance provenance must be an object")
    if provenance.get("authorization_type") != "phase1_development":
        raise ReleaseError("release Request is not authorized by the Phase 1 development flow")
    implementations = request.get("implementation_provenance") or []
    if (
        not isinstance(implementations, list)
        or len(implementations) != 1
        or not isinstance(implementations[0], dict)
    ):
        raise ReleaseError(
            "Phase 1 release Request must bind exactly one integration-validation Issue"
        )
    if str(request.get("implementation_provenance_sha256") or "") != digest(
        implementations
    ):
        raise ReleaseError("release Request Implementation provenance digest is invalid")
    if str(implementations[0].get("github_merge_commit_sha") or "") != source_commit:
        raise ReleaseError("release Request integration-validation does not bind source_commit")
    merged_pr = request.get("merged_pr")
    if not isinstance(merged_pr, dict):
        raise ReleaseError("release Request merged_pr must be an object")
    if str(merged_pr.get("merge_commit_sha") or "") != source_commit:
        raise ReleaseError("release Request merged PR does not bind source_commit")
    validation = request.get("validation")
    if not isinstance(validation, dict):
        raise ReleaseError("release Request validation must be an object")
    if (
        str(validation.get("headSha") or "") != source_commit
        or validation.get("status") != "completed"
        or validation.get("conclusion") != "success"
    ):
        raise ReleaseError("release Request validation does not bind a green source commit")
    return expected


def verify_release_request(
    root: Path,
    request: dict[str, Any],
    *,
    expected_source: str | None = None,
    verify_state: bool = True,
) -> str:
    expected = verify_release_request_payload(request)
    if expected_source and str(request.get("source_commit") or "") != expected_source:
        raise ReleaseError("release Request source commit differs from the workflow commit")
    control = release_control(root)
    if request.get("release_control") != {
        key: control[key] for key in RELEASE_CONTROL_KEYS
    }:
        raise ReleaseError("release Request control boundary differs from the repository contract")
    boundary = request.get("release_boundary") or {}
    required_boundary = [
        "repository",
        "repository_owner",
        "repository_visibility",
        "repository_default_branch",
        "environment",
        "environment_sha256",
        "environment_admin_bypass",
        "deployment_branch",
        "operator_type",
        "dispatcher_app_id",
        "dispatcher_app_slug",
        "dispatcher_actor_login",
        "dispatcher_installation_id",
        "dispatcher_account_login",
        "dispatcher_repository_selection",
        "dispatcher_repositories",
        "dispatcher_permissions",
        "dispatcher_installation_sha256",
        "publisher_app_id",
        "publisher_app_slug",
        "publisher_installation_id",
        "publisher_account_login",
        "publisher_repository_selection",
        "publisher_repositories",
        "publisher_permissions",
        "administrator_evidence_sha256",
        "administrator_evidence_id",
        "tag_ruleset_name",
        "tag_ruleset_id",
        "tag_ruleset_sha256",
        "tag_ruleset_updated_at",
    ]
    missing_boundary = [key for key in required_boundary if not boundary.get(key)]
    if missing_boundary:
        raise ReleaseError(
            f"release Request is missing protected boundary evidence: {missing_boundary}"
        )
    trusted_dispatcher_actor = github_app_actor_login(
        str(control["dispatcher_app_slug"])
    )
    if str(boundary["dispatcher_actor_login"]).lower() != trusted_dispatcher_actor:
        raise ReleaseError(
            "release Request Dispatcher actor differs from trusted release control"
        )
    if os.environ.get("GITHUB_ACTIONS") == "true":
        observed_actor = str(os.environ.get("GITHUB_ACTOR") or "").lower()
        if observed_actor != trusted_dispatcher_actor:
            raise ReleaseError(
                "release workflow was not dispatched by the reviewed Dispatcher App"
            )
    provenance = request.get("maintenance_provenance") or {}
    required = [
        "maintenance_issue",
        "review_comment_id",
        "multica_approval_comment_id",
        "maintenance_evidence_sha256",
        "multica_approval_author_sha256",
    ]
    missing = [key for key in required if not provenance.get(key)]
    if missing:
        raise ReleaseError(f"release Request is missing Maintenance provenance: {missing}")
    if verify_state:
        verify_current_state(root, request)
    return expected


def save_release_request(root: Path, request: dict[str, Any]) -> Path:
    path = root / ".multica/releases" / (
        f"request-{request['release_request_digest'][:12]}.json"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return path


def load_release_request(path: str) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"release Request is unreadable: {path}") from exc
    if not isinstance(value, dict):
        raise ReleaseError("release Request must be a JSON object")
    return value


def embedded_release_request(annotation: str) -> dict[str, Any]:
    encoded = annotation_value(annotation, "release_request_b64", r"[A-Za-z0-9_-]+")
    try:
        padding = "=" * (-len(encoded) % 4)
        value = json.loads(
            base64.urlsafe_b64decode(encoded + padding).decode("utf-8")
        )
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReleaseError("annotated tag release Request is invalid") from exc
    if not isinstance(value, dict):
        raise ReleaseError("annotated tag release Request must be a JSON object")
    verify_release_request_payload(value)
    if annotation_value(annotation, "release_request_digest", r"[a-f0-9]{64}") != str(
        value["release_request_digest"]
    ):
        raise ReleaseError("annotated tag release Request digest differs from its payload")
    return value


def annotation_value(annotation: str, key: str, pattern: str = r"\S+") -> str:
    matches = re.findall(rf"(?m)^{re.escape(key)}=({pattern})$", annotation)
    if len(matches) != 1:
        raise ReleaseError(f"annotated tag must contain exactly one {key}")
    return matches[0]




def frontmatter_version(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    match = re.search(r"(?m)^\s*version:\s*(\S+)\s*$", text)
    if not match:
        raise ReleaseError(f"missing metadata.version in {path}")
    return match.group(1)


def verify_versions(root: Path, version: str) -> list[str]:
    workflow = json.loads((root / "workflow.json").read_text(encoding="utf-8"))
    values = {
        "VERSION": (root / "VERSION").read_text(encoding="utf-8").strip(),
        "workflow.json": workflow["workflow"]["version"],
    }
    active_skill_paths = [
        root / str(item["path"]) / "SKILL.md"
        for item in workflow.get("skills", [])
    ]
    for path in sorted(active_skill_paths):
        values[path.relative_to(root).as_posix()] = frontmatter_version(path)
    mismatches = [f"{name}={value}" for name, value in values.items() if value != version]
    instruction_files = []
    for relative, versions in runtime_instruction_versions(root, workflow).items():
        if not versions:
            continue
        instruction_files.append(relative)
        mismatches.extend(
            f"{relative}:workflow_version={value}"
            for value in versions
            if value != version
        )
    if mismatches:
        raise ReleaseError(f"release version mismatch for {version}: {mismatches}")
    return sorted([*values, *instruction_files])


def verify_versions_at_ref(root: Path, ref: str, version: str) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="multica-release-") as temporary:
        snapshot = Path(temporary) / "snapshot"
        archive = Path(temporary) / "snapshot.zip"
        run(
            ["git", "archive", "--format=zip", f"--output={archive}", ref],
            root,
        )
        shutil.unpack_archive(str(archive), str(snapshot))
        return verify_versions(snapshot, version)


def normalized_expected_assets(value: Any) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ReleaseError("release Plan expected_assets must be a non-empty list")
    assets = [str(item) for item in value]
    invalid = [
        item
        for item in assets
        if not item or Path(item).name != item or item in {".", ".."}
    ]
    if invalid:
        raise ReleaseError(f"release Plan contains invalid asset names: {invalid}")
    if len(assets) != len(set(assets)):
        raise ReleaseError("release Plan expected_assets contains duplicates")
    return sorted(assets)


def expected_assets_sha256(value: Any) -> str:
    return digest(normalized_expected_assets(value))


def expected_assets_from_annotation(annotation: str) -> list[str]:
    hash_matches = re.findall(r"(?m)^expected_assets_sha256=([a-f0-9]{64})$", annotation)
    if len(hash_matches) != 1:
        raise ReleaseError("annotated tag must contain exactly one expected_assets_sha256")
    assets = normalized_expected_assets(
        re.findall(r"(?m)^expected_asset=([^\r\n]+)$", annotation)
    )
    if expected_assets_sha256(assets) != hash_matches[0]:
        raise ReleaseError("annotated tag expected asset manifest hash does not match")
    return assets


def annotated_tag_contents(root: Path, tag: str) -> str:
    if not tag.startswith("v"):
        raise ReleaseError("release tag must start with v")
    object_type = run(
        ["git", "cat-file", "-t", f"refs/tags/{tag}"], root, check=False
    )
    if object_type.returncode != 0 or object_type.stdout.strip() != "tag":
        raise ReleaseError("release tag must be an annotated tag")
    annotation = run(
        ["git", "tag", "-l", tag, "--format=%(contents)"], root
    ).stdout
    if not annotation.strip():
        raise ReleaseError("annotated release tag has no contents")
    return annotation


def verify_asset_directory(annotation: str, directory: Path) -> list[str]:
    expected = expected_assets_from_annotation(annotation)
    if not directory.is_dir():
        raise ReleaseError(f"release asset directory does not exist: {directory}")
    actual = sorted(path.name for path in directory.iterdir() if path.is_file())
    if actual != expected:
        missing = sorted(set(expected) - set(actual))
        extra = sorted(set(actual) - set(expected))
        raise ReleaseError(
            f"release asset set differs from the approved manifest; missing={missing}, extra={extra}"
        )
    return actual


def merged_pr_for_commit(root: Path, commit: str) -> dict[str, Any]:
    prs = gh_json(
        root,
        [
            "pr",
            "list",
            "--state",
            "merged",
            "--base",
            "main",
            "--limit",
            "100",
            "--json",
            "number,url,title,mergeCommit,headRefOid,baseRefName,mergedAt",
        ],
    )
    matches = [
        item
        for item in prs
        if str((item.get("mergeCommit") or {}).get("oid") or "") == commit
        and item.get("baseRefName") == "main"
    ]
    if len(matches) != 1:
        raise ReleaseError(f"release commit must equal exactly one merged main PR merge commit; found {len(matches)}")
    return matches[0]


def successful_validation(root: Path, commit: str) -> dict[str, Any]:
    runs = gh_json(
        root,
        [
            "run",
            "list",
            "--commit",
            commit,
            "--workflow",
            "validate.yml",
            "--limit",
            "20",
            "--json",
            "databaseId,status,conclusion,url,event,headSha,createdAt",
        ],
    )
    matches = [item for item in runs if item.get("status") == "completed" and item.get("conclusion") == "success"]
    if not matches:
        raise ReleaseError("release commit has no successful completed Phase 1 validation run")
    return sorted(matches, key=lambda item: str(item.get("createdAt") or ""), reverse=True)[0]


def verify_validation_record(root: Path, expected: dict[str, Any], commit: str) -> dict[str, Any]:
    run_id = str(expected.get("databaseId") or "")
    if not run_id:
        raise ReleaseError("release Plan validation record has no databaseId")
    current = gh_json(
        root,
        [
            "run",
            "view",
            run_id,
            "--json",
            "databaseId,status,conclusion,url,event,headSha,createdAt",
        ],
    )
    keys = ["databaseId", "status", "conclusion", "url", "event", "headSha", "createdAt"]
    changed = {
        key: {"planned": expected.get(key), "current": current.get(key)}
        for key in keys
        if expected.get(key) != current.get(key)
    }
    if changed:
        raise ReleaseError(f"bound validation run changed after release planning: {changed}")
    if current.get("status") != "completed" or current.get("conclusion") != "success":
        raise ReleaseError("bound validation run is not a completed success")
    if str(current.get("headSha") or "") != commit:
        raise ReleaseError("bound validation run head SHA does not match the release commit")
    return current


def tracked_source_hash(root: Path) -> str:
    files = run(["git", "ls-files", "-z"], root).stdout.split("\0")
    value = hashlib.sha256()
    for relative in sorted(item for item in files if item):
        data = (root / relative).read_bytes()
        encoded = relative.encode("utf-8")
        value.update(len(encoded).to_bytes(4, "big"))
        value.update(encoded)
        value.update(len(data).to_bytes(8, "big"))
        value.update(data)
    return value.hexdigest()


def build_plan(
    root: Path,
    version: str,
    bootstrap_plan: str | None,
    maintenance_issue: str | None,
    multica: MulticaCLI | None = None,
    implementation_issues: list[str] | None = None,
) -> dict[str, Any]:
    validate_release_version(version)
    commit = git_head(root)
    dirty = git_dirty(root)
    origin_main_sha = verify_origin_main_reachability(root, commit)
    if origin_main_sha != commit:
        raise ReleaseError(
            "release source commit must equal the current origin/main tip"
        )
    checked_versions = verify_versions(root, version)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if f"## {version}" not in changelog:
        raise ReleaseError(f"CHANGELOG.md has no {version} section")
    pr = merged_pr_for_commit(root, commit)
    validation = successful_validation(root, commit)
    if bool(bootstrap_plan) == bool(maintenance_issue):
        raise ReleaseError(
            "release planning requires exactly one of --development-issue or --bootstrap-plan"
        )
    if bootstrap_plan:
        authorization = bootstrap_evidence(root, version, bootstrap_plan, pr)
    else:
        if multica is None:
            raise ReleaseError(
                "development release planning requires a resolved Multica context"
            )
        authorization = maintenance_evidence(
            root, multica, str(maintenance_issue), pr, version
        )
    implementation_records = []
    if maintenance_issue:
        implementation_records = implementation_provenance(
            root,
            multica,
            implementation_issues or [],
            commit,
            version,
        )
        if authorization.get("authorization_type") == "phase1_development":
            if len(implementation_records) != 1:
                raise ReleaseError(
                    "Phase 1 release planning requires exactly one final integration-validation provenance Issue"
                )
            review_issue_id = str(authorization.get("review_issue_id") or "")
            if review_issue_id != str(
                implementation_records[0].get("issue_id") or ""
            ):
                raise ReleaseError(
                    "Phase 1 release review_issue_id is not included in implementation provenance"
                )
    plan = {
        "schema_version": 1,
        "created_at": utc_now(),
        "draft": dirty,
        "version": version,
        "tag": f"v{version}",
        "source_commit": commit,
        "origin_main_sha": origin_main_sha,
        "source_hash": tracked_source_hash(root),
        "merged_pr": {
            "number": pr["number"],
            "url": pr["url"],
            "title": pr["title"],
            "head_sha": pr["headRefOid"],
            "merge_commit_sha": (pr.get("mergeCommit") or {}).get("oid"),
            "merged_at": pr.get("mergedAt"),
        },
        "validation": validation,
        "version_files": checked_versions,
        "changelog_hash": hashlib.sha256(changelog.encode("utf-8")).hexdigest(),
        "expected_assets": phase1_expected_assets(version),
        "bootstrap_plan": bootstrap_plan,
        "maintenance_issue": maintenance_issue,
        "release_authorization": authorization,
        "implementation_provenance": implementation_records,
    }
    plan["release_plan_digest"] = digest(plan)
    return plan


def save_plan(root: Path, plan: dict[str, Any]) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = root / f".multica/releases/{stamp}-{plan['release_plan_digest'][:12]}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def verify_plan_file(plan: dict[str, Any], approval: str) -> str:
    expected = str(plan.get("release_plan_digest") or "")
    payload = {key: value for key, value in plan.items() if key != "release_plan_digest"}
    if digest(payload) != expected:
        raise ReleaseError("release Plan digest is invalid or the file was modified")
    if approval not in {expected, expected[:12]}:
        raise ReleaseError("release approval digest does not match")
    if plan.get("draft"):
        raise ReleaseError("draft release Plan from a dirty worktree cannot be applied")
    return expected


def verify_current_state(
    root: Path, plan: dict[str, Any], *, verify_merged_pr: bool = True
) -> None:
    if git_dirty(root):
        raise ReleaseError("working tree is dirty")
    if git_head(root) != plan["source_commit"]:
        raise ReleaseError("Git HEAD changed after release planning")
    if tracked_source_hash(root) != plan["source_hash"]:
        raise ReleaseError("tracked source changed after release planning")
    origin_main_sha = verify_origin_main_reachability(
        root, plan["source_commit"], str(plan.get("origin_main_sha") or "")
    )
    if origin_main_sha != plan["source_commit"]:
        raise ReleaseError(
            "release source commit is no longer the current origin/main tip"
        )
    verify_versions(root, plan["version"])
    if verify_merged_pr:
        pr = merged_pr_for_commit(root, plan["source_commit"])
        if (
            pr["number"] != plan["merged_pr"]["number"]
            or pr["headRefOid"] != plan["merged_pr"]["head_sha"]
            or pr.get("mergedAt") != plan["merged_pr"].get("merged_at")
        ):
            raise ReleaseError("merged PR provenance changed after release planning")
    verify_validation_record(root, plan["validation"], plan["source_commit"])


def verify_bootstrap_authorization(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    authorization = plan.get("release_authorization") or {}
    if authorization.get("mode") != "bootstrap":
        raise ReleaseError("release Plan is not authorized by the v6 bootstrap")
    bootstrap_plan = str(plan.get("bootstrap_plan") or "")
    version = str(plan.get("version") or "")
    pr_number = (plan.get("merged_pr") or {}).get("number")
    if not bootstrap_plan or not version or pr_number is None:
        raise ReleaseError("bootstrap release Plan provenance is incomplete")
    current = bootstrap_evidence(
        root,
        version,
        bootstrap_plan,
        {"number": int(pr_number)},
    )
    if current != authorization:
        raise ReleaseError("bootstrap authorization evidence changed after release planning")
    return current


def verify_release_approval(
    root: Path, cli: MulticaCLI, plan: dict[str, Any]
) -> dict[str, Any]:
    authorization = plan.get("release_authorization") or {}
    if authorization.get("mode") != "maintenance":
        raise ReleaseError(
            "Multica release approval is only valid for a Phase 1 development release"
        )
    current = maintenance_evidence(
        root,
        cli,
        str(authorization["issue_id"]),
        {
            "number": plan["merged_pr"]["number"],
            "headRefOid": plan["merged_pr"]["head_sha"],
            "mergeCommit": {"oid": plan["merged_pr"]["merge_commit_sha"]},
            "mergedAt": plan["merged_pr"].get("merged_at"),
        },
        str(plan.get("version") or ""),
    )
    if current != authorization:
        raise ReleaseError("Maintenance Review evidence changed after release planning")
    planned_implementations = plan.get("implementation_provenance") or []
    if planned_implementations:
        current_implementations = implementation_provenance(
            root,
            cli,
            [str(item.get("issue_id") or "") for item in planned_implementations],
            str(plan["source_commit"]),
            str(plan.get("version") or ""),
            planned_implementations,
        )
        if current_implementations != planned_implementations:
            raise ReleaseError("Implementation provenance changed after release planning")
    comments = as_list(
        cli.json(
            ["issue", "comment", "list", str(authorization["issue_id"]), "--full", "--output", "json"]
        ),
        "comments",
    )
    expected_lines = {
        f"APPROVE WORKFLOW RELEASE {plan['release_plan_digest']}",
        f"APPROVE WORKFLOW RELEASE {plan['release_plan_digest'][:12]}",
    }
    current_metadata = metadata_map(
        cli.json(
            [
                "issue",
                "metadata",
                "list",
                str(authorization["issue_id"]),
                "--output",
                "json",
            ]
        )
    )
    human_approver_id = str(
        current_metadata.get("human_approver_id")
        or authorization.get("human_approver_id")
        or ""
    )
    matches = []
    for item in comments:
        author_id, author_type = comment_author(item)
        lines = [
            line.strip()
            for line in str(item.get("content") or "").splitlines()
            if line.strip()
        ]
        if (
            author_type in {"member", "user"}
            and author_id == human_approver_id
            and lines in [[line] for line in expected_lines]
        ):
            matches.append(item)
    if len(matches) != 1:
        raise ReleaseError(
            "expected exactly one human APPROVE WORKFLOW RELEASE comment for this digest "
            f"on {authorization['issue_id']}; found {len(matches)}"
        )
    return {
        "comment_id": str(matches[0].get("id") or ""),
        "author_id": comment_author(matches[0])[0],
    }


def release_approver_record(root: Path) -> dict[str, Any]:
    path = root / "docs/bootstrap-v6.json"
    record = json.loads(path.read_text(encoding="utf-8"))
    if not record.get("repository") or not record.get("approver_github_login"):
        raise ReleaseError("durable GitHub release approver record is incomplete")
    return record


def verify_github_release_approval(
    root: Path,
    digest_value: str,
    pr_number: int,
    expected_comment_id: str | None = None,
    expected_provenance: dict[str, str] | None = None,
) -> dict[str, Any]:
    record = release_approver_record(root)
    value = gh_json(
        root,
        [
            "pr",
            "view",
            str(pr_number),
            "--repo",
            str(record["repository"]),
            "--json",
            "comments",
        ],
    )
    comments = as_list(value, "comments")
    expected_lines = {
        f"APPROVE WORKFLOW RELEASE {digest_value}",
        f"APPROVE WORKFLOW RELEASE {digest_value[:12]}",
    }
    required_provenance_lines = {
        f"{key}={value}" for key, value in (expected_provenance or {}).items()
    }
    matches = [
        item
        for item in comments
        if (not expected_comment_id or str(item.get("id") or "") == expected_comment_id)
        and str((item.get("author") or {}).get("login") or "").lower()
        == str(record["approver_github_login"]).lower()
        and any(
            line.strip() in expected_lines
            for line in str(item.get("body") or item.get("content") or "").splitlines()
        )
        and required_provenance_lines.issubset(
            {
                line.strip()
                for line in str(item.get("body") or item.get("content") or "").splitlines()
            }
        )
    ]
    if len(matches) != 1:
        raise ReleaseError(
            "expected exactly one durable GitHub APPROVE WORKFLOW RELEASE comment from "
            f"{record['approver_github_login']} on PR #{pr_number}; found {len(matches)}"
        )
    return {
        "comment_id": str(matches[0].get("id") or ""),
        "url": str(matches[0].get("url") or ""),
        "author_login": str((matches[0].get("author") or {}).get("login") or ""),
    }


def verify_bootstrap_release_approval(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    authorization = verify_bootstrap_authorization(root, plan)
    record = release_approver_record(root)
    if (
        str(authorization.get("approver_login") or "").lower()
        != str(record["approver_github_login"]).lower()
        or authorization.get("repository") != record.get("repository")
    ):
        raise ReleaseError("bootstrap release approver differs from the durable approval record")
    return verify_github_release_approval(
        root, plan["release_plan_digest"], int(authorization["pr_number"])
    )


def command_plan(args: argparse.Namespace, root: Path) -> int:
    cli = None
    if args.maintenance_issue:
        cli, _ = release_cli(args.multica_bin, args.profile, args.workspace)
    plan = build_plan(
        root,
        args.version,
        args.bootstrap_plan,
        args.maintenance_issue,
        cli,
        args.implementation_provenance,
    )
    path = save_plan(root, plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print(f"Release Plan file: {path}")
    if plan["draft"]:
        print("DRAFT: commit/review the exact source and generate a new release Plan")
    else:
        print(f"Approval: APPROVE WORKFLOW RELEASE {plan['release_plan_digest'][:12]}")
    return 0


def command_approval_block(args: argparse.Namespace, root: Path) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    expected = str(plan.get("release_plan_digest") or "")
    verify_plan_file(plan, expected)
    dispatcher = verify_dispatcher_installation(root)
    verify_current_state(root, plan, verify_merged_pr=False)
    authorization = plan.get("release_authorization") or {}
    if authorization.get("mode") == "maintenance":
        cli, _ = release_cli(
            args.multica_bin,
            args.profile if args.profile is not None else authorization.get("profile"),
            args.workspace if args.workspace is not None else authorization.get("workspace_id"),
        )
        if cli.workspace_id != str(authorization.get("workspace_id") or ""):
            raise ReleaseError("Multica Workspace differs from the release Plan")
        release_approval = verify_release_approval(root, cli, plan)
        boundary = verify_release_environment(
            root, dispatcher_installation=dispatcher
        )
        request = release_request(root, plan, release_approval, boundary)
    elif authorization.get("mode") != "bootstrap":
        raise ReleaseError("release Plan has no recognized authorization mode")
    else:
        raise ReleaseError(
            "bootstrap release authorization is historical and cannot create new releases"
        )
    print(
        json.dumps(
            {
                "release_plan_digest": expected,
                "release_request_digest": request["release_request_digest"],
                "tag": request["tag"],
                "source_commit": request["source_commit"],
                "maintenance_provenance": request["maintenance_provenance"],
                "protected_environment": boundary,
                "next_step": "dispatch the request; an isolated Environment reviewer must approve before publication",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


def dispatch_release_workflow(
    root: Path,
    boundary: dict[str, Any],
    request: dict[str, Any],
    *,
    recover_existing_tag: bool,
) -> None:
    encoded = base64.urlsafe_b64encode(canonical(request).encode("utf-8")).decode(
        "ascii"
    )
    run(
        [
            "gh",
            "workflow",
            "run",
            "release.yml",
            "--repo",
            str(boundary["repository"]),
            "--ref",
            str(boundary["deployment_branch"]),
            "--field",
            f"request_b64={encoded}",
            "--field",
            f"recover_existing_tag={'true' if recover_existing_tag else 'false'}",
        ],
        root,
    )


def command_apply(args: argparse.Namespace, root: Path) -> int:
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    expected = verify_plan_file(plan, args.approve)
    dispatcher = verify_dispatcher_installation(root)
    verify_current_state(root, plan, verify_merged_pr=False)
    authorization = plan.get("release_authorization") or {}
    if authorization.get("mode") != "maintenance":
        raise ReleaseError("new releases require Phase 1 development authorization")
    cli, _ = release_cli(
        args.multica_bin,
        args.profile if args.profile is not None else authorization.get("profile"),
        args.workspace if args.workspace is not None else authorization.get("workspace_id"),
    )
    if cli.workspace_id != str(authorization.get("workspace_id") or ""):
        raise ReleaseError("Multica Workspace differs from the release Plan")
    release_approval = verify_release_approval(root, cli, plan)
    boundary = verify_release_environment(
        root, dispatcher_installation=dispatcher
    )
    request = release_request(root, plan, release_approval, boundary)
    request_path = save_release_request(root, request)
    dispatch_release_workflow(
        root,
        boundary,
        request,
        recover_existing_tag=False,
    )
    print(
        json.dumps(
            {
                "action": "release_request_dispatched",
                "tag": request["tag"],
                "source_commit": request["source_commit"],
                "release_plan_digest": expected,
                "release_request_digest": request["release_request_digest"],
                "request_file": str(request_path),
                "protected_environment": boundary["environment"],
                "local_tag_mutation": False,
                "local_release_mutation": False,
            },
            indent=2,
        )
    )
    return 0


def command_recover(args: argparse.Namespace, root: Path) -> int:
    dispatcher = verify_dispatcher_installation(root)
    if args.request:
        request = load_release_request(args.request)
    else:
        tag = str(args.tag)
        if not tag.startswith("v"):
            raise ReleaseError("release tag must start with v")
        validate_release_version(tag[1:])
        if not run(["git", "tag", "--list", tag], root).stdout.strip():
            run(
                [
                    "git",
                    "fetch",
                    "--no-tags",
                    "origin",
                    f"refs/tags/{tag}:refs/tags/{tag}",
                ],
                root,
            )
        request = embedded_release_request(annotated_tag_contents(root, tag))
        if str(request.get("tag") or "") != tag:
            raise ReleaseError("embedded release Request belongs to a different tag")
    verify_release_request_payload(request)
    verify_existing_recovery_tag(root, request)
    boundary = verify_release_environment(
        root, dispatcher_installation=dispatcher
    )
    request_path = save_release_request(root, request)
    dispatch_release_workflow(
        root,
        boundary,
        request,
        recover_existing_tag=True,
    )
    print(
        json.dumps(
            {
                "action": "release_recovery_dispatched",
                "tag": request["tag"],
                "source_commit": request["source_commit"],
                "release_request_digest": request["release_request_digest"],
                "request_file": str(request_path),
                "protected_environment": boundary["environment"],
            },
            indent=2,
        )
    )
    return 0


def command_doctor(args: argparse.Namespace, root: Path) -> int:
    boundary = verify_release_environment(root)
    print(json.dumps({"status": "ready", **boundary}, indent=2))
    return 0


def command_verify_request(args: argparse.Namespace, root: Path) -> int:
    request = load_release_request(args.request)
    request_digest = verify_release_request(
        root, request, expected_source=args.expected_source
    )
    output = {
        "release_request_digest": request_digest,
        "release_plan_digest": request["release_plan_digest"],
        "source_commit": request["source_commit"],
        "tag": request["tag"],
        "version": request["version"],
    }
    if args.github_output:
        path = Path(args.github_output)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            for key, value in output.items():
                handle.write(f"{key}={value}\n")
    print(json.dumps(output, indent=2))
    return 0


def command_verify_request_payload(args: argparse.Namespace, root: Path) -> int:
    request = load_release_request(args.request)
    request_digest = verify_release_request_payload(request)
    output = {
        "release_request_digest": request_digest,
        "source_commit": request["source_commit"],
        "tag": request["tag"],
    }
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8", newline="\n") as handle:
            for key, value in output.items():
                handle.write(f"{key}={value}\n")
    print(json.dumps(output, indent=2))
    return 0


def command_verify_publish_gate(args: argparse.Namespace, root: Path) -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ReleaseError("publish gate verification may run only inside GitHub Actions")
    if str(os.environ.get("GITHUB_RUN_ID") or "") != str(args.workflow_run_id):
        raise ReleaseError("GitHub workflow run ID differs from the publish gate request")
    request = load_release_request(args.request)
    verify_release_request(root, request, expected_source=git_head(root))
    control = release_control(root)
    if str(args.environment) != str(control["environment"]):
        raise ReleaseError("publish gate Environment differs from release control")
    if str(os.environ.get("GITHUB_REPOSITORY") or "") != str(control["repository"]):
        raise ReleaseError("GitHub Actions repository differs from release control")
    boundary = verify_release_environment(root, verify_dispatcher_token=False)
    approval = verify_environment_approval(
        root,
        str(args.workflow_run_id),
        verify_configuration=True,
        verify_dispatcher_token=False,
    )
    gate = publish_gate_record(request, boundary, approval)
    output_path = Path(args.output).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(gate, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    output = {
        "publish_gate_digest": gate["publish_gate_digest"],
        "dispatcher_app_id": gate["dispatcher"]["app_id"],
        "dispatcher_app_slug": gate["dispatcher"]["app_slug"],
        "dispatcher_installation_id": gate["dispatcher"]["installation_id"],
        "publisher_app_id": gate["publisher"]["app_id"],
        "publisher_app_slug": gate["publisher"]["app_slug"],
        "publisher_installation_id": gate["publisher"]["installation_id"],
    }
    if args.github_output:
        with Path(args.github_output).open("a", encoding="utf-8", newline="\n") as handle:
            for key, value in output.items():
                handle.write(f"{key}={value}\n")
    print(json.dumps({**output, "gate": str(output_path)}, indent=2))
    return 0


def protected_tag_message(
    request: dict[str, Any], gate: dict[str, Any], publisher: dict[str, Any]
) -> str:
    expected_assets = normalized_expected_assets(request.get("expected_assets"))
    provenance = request.get("maintenance_provenance") or {}
    boundary = request.get("release_boundary") or {}
    approval = gate.get("environment_approval") or {}
    encoded_request = base64.urlsafe_b64encode(
        canonical(request).encode("utf-8")
    ).decode("ascii").rstrip("=")
    lines = [
        f"Multica Workflow {request['tag']}",
        "",
        f"release_request_digest={request['release_request_digest']}",
        f"release_request_b64={encoded_request}",
        f"release_plan_digest={request['release_plan_digest']}",
        f"source_commit={request['source_commit']}",
        f"merged_pr={request['merged_pr']['number']}",
        f"validation_run_id={request['validation']['databaseId']}",
        f"authorization_mode={PROTECTED_ENVIRONMENT_MODE}",
        f"release_workflow_run_id={approval['run_id']}",
        f"release_environment={approval['environment']}",
        f"environment_approval_actor={approval['actor_login']}",
        f"environment_approval_sha256={approval['approval_sha256']}",
        f"publish_gate_digest={gate['publish_gate_digest']}",
        f"release_operator_type={boundary['operator_type']}",
        f"dispatcher_app_id={boundary['dispatcher_app_id']}",
        f"dispatcher_app_slug={boundary['dispatcher_app_slug']}",
        f"dispatcher_actor_login={boundary['dispatcher_actor_login']}",
        f"dispatcher_installation_id={boundary['dispatcher_installation_id']}",
        f"dispatcher_installation_sha256={boundary['dispatcher_installation_sha256']}",
        f"publisher_app_id={publisher['app_id']}",
        f"publisher_app_slug={publisher['app_slug']}",
        f"publisher_installation_id={publisher['installation_id']}",
        f"publisher_installation_sha256={publisher['sha256']}",
        f"release_boundary_sha256={digest(boundary)}",
        f"repository_visibility={boundary['repository_visibility']}",
        f"release_environment_sha256={boundary['environment_sha256']}",
        f"release_ruleset_id={boundary['tag_ruleset_id']}",
        f"release_ruleset_sha256={boundary['tag_ruleset_sha256']}",
        f"implementation_provenance_sha256={request['implementation_provenance_sha256']}",
        f"expected_assets_sha256={expected_assets_sha256(expected_assets)}",
        *(f"expected_asset={name}" for name in expected_assets),
        *(f"{key}={value}" for key, value in provenance.items()),
        "",
    ]
    return "\n".join(lines)


def create_protected_tag(
    root: Path,
    request: dict[str, Any],
    gate: dict[str, Any],
    publisher: dict[str, Any],
) -> dict[str, str]:
    repository = str((request.get("release_boundary") or {})["repository"])
    tag = str(request["tag"])
    if run(["git", "tag", "--list", tag], root).stdout.strip():
        raise ReleaseError(f"tag already exists locally: {tag}")
    if run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"], root
    ).stdout.strip():
        raise ReleaseError(f"tag already exists on origin: {tag}")
    tag_object = gh_api_json(
        root,
        "POST",
        f"repos/{repository}/git/tags",
        {
            "tag": tag,
            "message": protected_tag_message(request, gate, publisher),
            "object": str(request["source_commit"]),
            "type": "commit",
        },
    )
    tag_sha = str((tag_object or {}).get("sha") or "")
    if not re.fullmatch(r"[a-f0-9]{40,64}", tag_sha):
        raise ReleaseError("GitHub did not return the annotated tag object SHA")
    ref = gh_api_json(
        root,
        "POST",
        f"repos/{repository}/git/refs",
        {"ref": f"refs/tags/{tag}", "sha": tag_sha},
    )
    if str((ref or {}).get("ref") or "") != f"refs/tags/{tag}":
        raise ReleaseError("GitHub did not create the expected protected tag ref")
    run(
        [
            "git",
            "fetch",
            "--no-tags",
            "origin",
            f"refs/tags/{tag}:refs/tags/{tag}",
        ],
        root,
    )
    return {
        "tag": tag,
        "source_commit": str(request["source_commit"]),
        "tag_object_sha": tag_sha,
    }


def verify_existing_recovery_tag(
    root: Path, request: dict[str, Any]
) -> dict[str, str]:
    tag = str(request["tag"])
    remote = run(
        ["git", "ls-remote", "--tags", "origin", f"refs/tags/{tag}"],
        root,
    ).stdout.strip()
    if not remote:
        raise ReleaseError(f"recovery tag does not exist on origin: {tag}")
    remote_tag_object = remote.split()[0]
    if not re.fullmatch(r"[a-f0-9]{40,64}", remote_tag_object):
        raise ReleaseError("origin returned an invalid recovery tag object")
    if not run(["git", "tag", "--list", tag], root).stdout.strip():
        run(
            [
                "git",
                "fetch",
                "--no-tags",
                "origin",
                f"refs/tags/{tag}:refs/tags/{tag}",
            ],
            root,
        )
    local_tag_object = run(
        ["git", "rev-parse", f"refs/tags/{tag}"], root
    ).stdout.strip()
    if local_tag_object != remote_tag_object:
        raise ReleaseError("local recovery tag differs from origin")
    annotation = annotated_tag_contents(root, tag)
    if annotation_value(annotation, "release_request_digest", r"[a-f0-9]{64}") != str(
        request["release_request_digest"]
    ):
        raise ReleaseError("recovery tag release Request digest does not match")
    commit = run(["git", "rev-list", "-n", "1", tag], root).stdout.strip()
    if commit != str(request["source_commit"]):
        raise ReleaseError("recovery tag source commit does not match")
    return {"tag": tag, "source_commit": commit}


def command_publish(args: argparse.Namespace, root: Path) -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ReleaseError("publish may run only inside GitHub Actions")
    if str(os.environ.get("GITHUB_RUN_ID") or "") != str(args.workflow_run_id):
        raise ReleaseError("GitHub workflow run ID differs from the publish request")
    request = load_release_request(args.request)
    verify_release_request(
        root, request, expected_source=git_head(root), verify_state=False
    )
    control = release_control(root)
    if str(args.environment) != str(control["environment"]):
        raise ReleaseError("publish Environment differs from release control")
    if str(os.environ.get("GITHUB_REPOSITORY") or "") != str(control["repository"]):
        raise ReleaseError("GitHub Actions repository differs from release control")
    gate = load_publish_gate(args.gate)
    verify_publish_gate(
        gate,
        request,
        args.approve,
        workflow_run_id=str(args.workflow_run_id),
        environment=str(args.environment),
    )
    publisher = verify_publisher_installation(root, control)

    tag = str(request["tag"])
    if args.recover_existing_tag:
        recovery = verify_existing_recovery_tag(root, request)
        action = "existing_tag_recovery_approved"
    else:
        recovery = create_protected_tag(root, request, gate, publisher)
        action = "protected_tag_created"
    print(
        json.dumps(
            {
                "action": action,
                **recovery,
                "release_request_digest": request["release_request_digest"],
                "publish_gate_digest": gate["publish_gate_digest"],
                "protected_environment_approval": gate["environment_approval"],
                "publisher": publisher,
            },
            indent=2,
        )
    )
    return 0


def command_publish_release(args: argparse.Namespace, root: Path) -> int:
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise ReleaseError("release publication may run only inside GitHub Actions")
    if str(os.environ.get("GITHUB_RUN_ID") or "") != str(args.workflow_run_id):
        raise ReleaseError("GitHub workflow run ID differs from the release publication")
    request = load_release_request(args.request)
    verify_release_request(
        root, request, expected_source=git_head(root), verify_state=False
    )
    control = release_control(root)
    if str(args.environment) != str(control["environment"]):
        raise ReleaseError("release publication Environment differs from release control")
    if str(os.environ.get("GITHUB_REPOSITORY") or "") != str(control["repository"]):
        raise ReleaseError("GitHub Actions repository differs from release control")
    gate = load_publish_gate(args.gate)
    verify_publish_gate(
        gate,
        request,
        args.approve,
        workflow_run_id=str(args.workflow_run_id),
        environment=str(args.environment),
    )
    publisher = verify_publisher_installation(root, control)
    tag = str(request["tag"])
    annotation = annotated_tag_contents(root, tag)
    tag_gate_digest = annotation_value(
        annotation, "publish_gate_digest", r"[a-f0-9]{64}"
    )
    if args.recover_existing_tag:
        if annotation_value(
            annotation, "release_request_digest", r"[a-f0-9]{64}"
        ) != str(request["release_request_digest"]):
            raise ReleaseError(
                "recovery tag does not bind the approved release Request"
            )
    elif tag_gate_digest != str(gate["publish_gate_digest"]):
        raise ReleaseError("protected tag does not bind the approved publish gate")
    assets = verify_asset_directory(annotation, Path(args.directory).resolve())
    existing = run(
        ["gh", "release", "view", tag, "--json", "id,assets"],
        root,
        check=False,
    )
    asset_paths = [str(Path(args.directory).resolve() / name) for name in assets]
    if existing.returncode == 0:
        if not args.recover_existing_tag:
            raise ReleaseError(f"GitHub Release already exists: {tag}")
        try:
            release_record = json.loads(existing.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise ReleaseError("existing GitHub Release response is invalid") from exc
        existing_names = {
            str(item.get("name") or "")
            for item in release_record.get("assets") or []
            if isinstance(item, dict)
        } - {""}
        unexpected = sorted(existing_names - set(assets))
        if unexpected:
            raise ReleaseError(
                f"existing GitHub Release contains unapproved assets: {unexpected}"
            )
        run(["gh", "release", "upload", tag, *asset_paths, "--clobber"], root)
        publish_action = "github_release_assets_recovered"
    else:
        command = [
            "gh",
            "release",
            "create",
            tag,
            *asset_paths,
            "--verify-tag",
            "--title",
            f"Multica Workflow {tag}",
            "--generate-notes",
        ]
        if "-" in tag:
            command.append("--prerelease")
        run(command, root)
        publish_action = "github_release_published"
    print(
        json.dumps(
            {
                "action": publish_action,
                "tag": tag,
                "assets": assets,
                "publish_gate_digest": gate["publish_gate_digest"],
                "tag_publish_gate_digest": tag_gate_digest,
                "recovery": bool(args.recover_existing_tag),
                "publisher": publisher,
            },
            indent=2,
        )
    )
    return 0


def command_verify_tag(args: argparse.Namespace, root: Path) -> int:
    tag = args.tag
    if not tag.startswith("v"):
        raise ReleaseError("release tag must start with v")
    version = tag[1:]
    commit = run(["git", "rev-list", "-n", "1", tag], root).stdout.strip()
    if not commit:
        raise ReleaseError(f"tag not found: {tag}")
    verify_versions_at_ref(root, tag, version)
    annotation = annotated_tag_contents(root, tag)
    if re.search(r"(?m)^release_request_b64=", annotation):
        embedded = embedded_release_request(annotation)
        if (
            str(embedded.get("tag") or "") != tag
            or str(embedded.get("source_commit") or "") != commit
        ):
            raise ReleaseError("annotated release Request does not bind this tag")
    expected_assets = expected_assets_from_annotation(annotation)
    match = re.search(r"(?m)^release_plan_digest=([a-f0-9]{64})$", annotation)
    if not match:
        raise ReleaseError("annotated tag has no release_plan_digest")
    validation_match = re.search(r"(?m)^validation_run_id=(\d+)$", annotation)
    if not validation_match:
        raise ReleaseError("annotated tag has no validation_run_id")
    source_match = re.search(r"(?m)^source_commit=([a-f0-9]{40,64})$", annotation)
    merged_pr_match = re.search(r"(?m)^merged_pr=(\d+)$", annotation)
    mode_match = re.search(
        rf"(?m)^authorization_mode=(bootstrap|maintenance|{PROTECTED_ENVIRONMENT_MODE})$",
        annotation,
    )
    github_approval_match = re.search(r"(?m)^github_approval_comment_id=(\S+)$", annotation)
    if not source_match or not merged_pr_match or not mode_match:
        raise ReleaseError("annotated tag is missing release authorization provenance")
    if source_match.group(1) != commit:
        raise ReleaseError("annotated source commit does not match the tag commit")
    verify_origin_main_reachability(root, commit)
    merged_pr = merged_pr_for_commit(root, commit)
    if int(merged_pr_match.group(1)) != int(merged_pr["number"]):
        raise ReleaseError("annotated merged PR does not match the tag commit PR")
    authorization_mode = mode_match.group(1)
    if authorization_mode == PROTECTED_ENVIRONMENT_MODE:
        required = [
            "release_request_digest",
            "release_boundary_sha256",
            "release_workflow_run_id",
            "release_environment",
            "release_environment_sha256",
            "release_ruleset_id",
            "release_ruleset_sha256",
            "repository_visibility",
            "environment_approval_actor",
            "environment_approval_sha256",
            "publish_gate_digest",
            "release_operator_type",
            "dispatcher_app_id",
            "dispatcher_app_slug",
            "dispatcher_actor_login",
            "dispatcher_installation_id",
            "dispatcher_installation_sha256",
            "publisher_app_id",
            "publisher_app_slug",
            "publisher_installation_id",
            "publisher_installation_sha256",
            "implementation_provenance_sha256",
            "maintenance_issue",
            "review_comment_id",
            "multica_approval_comment_id",
            "maintenance_evidence_sha256",
            "multica_approval_author_sha256",
        ]
        values = {}
        for key in required + ["review_issue_id", "batch_review_mappings_sha256"]:
            if key.endswith("sha256") or key in {
                "release_request_digest",
                "publish_gate_digest",
            }:
                pattern = r"[a-f0-9]{64}"
            elif key in {
                "dispatcher_app_id",
                "dispatcher_installation_id",
                "publisher_app_id",
                "publisher_installation_id",
            }:
                pattern = r"\d+"
            else:
                pattern = r"\S+"
            value_match = re.search(rf"(?m)^{key}=({pattern})$", annotation)
            if value_match:
                values[key] = value_match.group(1)
        missing = [key for key in required if key not in values]
        if missing:
            raise ReleaseError(
                f"protected Environment tag is missing provenance fields: {missing}"
            )
        control = release_control(root)
        if values["release_environment"] != str(control["environment"]):
            raise ReleaseError("annotated release Environment differs from release control")
        current_boundary = verify_release_environment(
            root, verify_dispatcher_token=False
        )
        current_snapshot = release_boundary_snapshot(current_boundary)
        if values["release_operator_type"] != str(current_snapshot["operator_type"]):
            raise ReleaseError("annotated release operator type differs from release control")
        if int(values["dispatcher_app_id"]) != int(current_snapshot["dispatcher_app_id"]):
            raise ReleaseError("annotated Dispatcher App ID differs from release control")
        if values["dispatcher_app_slug"] != str(current_snapshot["dispatcher_app_slug"]):
            raise ReleaseError("annotated Dispatcher App slug differs from release control")
        if values["dispatcher_actor_login"] != str(current_snapshot["dispatcher_actor_login"]):
            raise ReleaseError("annotated Dispatcher actor differs from release control")
        if int(values["dispatcher_installation_id"]) != int(
            current_snapshot["dispatcher_installation_id"]
        ):
            raise ReleaseError("annotated Dispatcher installation differs from release control")
        dispatcher_snapshot = {
            "app_id": int(current_snapshot["dispatcher_app_id"]),
            "app_slug": str(current_snapshot["dispatcher_app_slug"]),
            "installation_id": int(current_snapshot["dispatcher_installation_id"]),
            "account_login": str(current_snapshot["dispatcher_account_login"]),
            "repository_selection": str(current_snapshot["dispatcher_repository_selection"]),
            "repositories": sorted(current_snapshot["dispatcher_repositories"]),
            "permissions": dict(sorted(current_snapshot["dispatcher_permissions"].items())),
        }
        if values["dispatcher_installation_sha256"] != digest(dispatcher_snapshot):
            raise ReleaseError("annotated Dispatcher installation evidence changed")
        if int(values["publisher_app_id"]) != int(current_snapshot["publisher_app_id"]):
            raise ReleaseError("annotated Publisher App ID differs from release control")
        if values["publisher_app_slug"] != str(current_snapshot["publisher_app_slug"]):
            raise ReleaseError("annotated Publisher App slug differs from release control")
        if int(values["publisher_installation_id"]) != int(
            current_snapshot["publisher_installation_id"]
        ):
            raise ReleaseError("annotated Publisher installation differs from release control")
        publisher_snapshot = {
            "app_id": int(current_snapshot["publisher_app_id"]),
            "app_slug": str(current_snapshot["publisher_app_slug"]),
            "installation_id": int(current_snapshot["publisher_installation_id"]),
            "account_login": str(current_snapshot["publisher_account_login"]),
            "repository_selection": str(current_snapshot["publisher_repository_selection"]),
            "repositories": sorted(current_snapshot["publisher_repositories"]),
            "permissions": dict(sorted(current_snapshot["publisher_permissions"].items())),
        }
        if values["publisher_installation_sha256"] != digest(publisher_snapshot):
            raise ReleaseError("annotated Publisher installation evidence changed")
        if values["release_boundary_sha256"] != digest(current_snapshot):
            raise ReleaseError("annotated protected release boundary changed")
        if values["repository_visibility"] != current_snapshot["repository_visibility"]:
            raise ReleaseError("annotated repository visibility changed")
        if values["release_environment_sha256"] != current_snapshot["environment_sha256"]:
            raise ReleaseError("annotated release Environment configuration changed")
        if values["release_ruleset_id"] != current_snapshot["tag_ruleset_id"]:
            raise ReleaseError("annotated release Ruleset ID changed")
        if values["release_ruleset_sha256"] != current_snapshot["tag_ruleset_sha256"]:
            raise ReleaseError("annotated release Ruleset configuration changed")
        github_approval = verify_environment_approval(
            root,
            values["release_workflow_run_id"],
            expected_actor=values["environment_approval_actor"],
            verify_configuration=True,
            verify_dispatcher_token=False,
        )
        if github_approval["approval_sha256"] != values["environment_approval_sha256"]:
            raise ReleaseError("annotated Environment approval evidence changed")
    elif authorization_mode == "maintenance":
        if not github_approval_match:
            raise ReleaseError("legacy maintenance tag is missing GitHub approval comment")
        required = [
            "maintenance_issue",
            "review_comment_id",
            "multica_approval_comment_id",
            "maintenance_evidence_sha256",
            "multica_approval_author_sha256",
        ]
        if version == RC2_RECOVERY_VERSION:
            required.extend(
                [
                    "recovery_mode",
                    "pending_incident_source",
                    "recovery_decision_comment_id",
                    "control_identity_sha256",
                    "pending_incident_evidence_sha256",
                    "recovery_decision_sha256",
                ]
            )
        optional = ["review_issue_id", "batch_review_mappings_sha256"]
        values = {}
        for key in required + optional:
            pattern = rf"(?m)^{key}=([a-f0-9]{{64}})$" if key.endswith("sha256") else rf"(?m)^{key}=(\S+)$"
            value_match = re.search(pattern, annotation)
            if value_match:
                values[key] = value_match.group(1)
        missing = [key for key in required if key not in values]
        if missing:
            raise ReleaseError(f"maintenance tag is missing provenance fields: {missing}")
        github_approval = verify_github_release_approval(
            root,
            match.group(1),
            int(merged_pr_match.group(1)),
            github_approval_match.group(1),
            values,
        )
    else:
        if not github_approval_match:
            raise ReleaseError("bootstrap tag is missing GitHub approval comment")
        bootstrap = bootstrap_evidence(
            root, version, "v6", {"number": int(merged_pr_match.group(1))}
        )
        plan_approval_match = re.search(r"(?m)^plan_approval_comment_id=(\S+)$", annotation)
        if (
            not plan_approval_match
            or plan_approval_match.group(1) != bootstrap["plan_approval_comment_id"]
        ):
            raise ReleaseError("bootstrap tag does not bind the approved v6 Plan comment")
        github_approval = verify_github_release_approval(
            root,
            match.group(1),
            int(merged_pr_match.group(1)),
            github_approval_match.group(1),
        )
    validation = gh_json(
        root,
        [
            "run",
            "view",
            validation_match.group(1),
            "--json",
            "databaseId,status,conclusion,url,event,headSha,createdAt",
        ],
    )
    if (
        validation.get("status") != "completed"
        or validation.get("conclusion") != "success"
        or str(validation.get("headSha") or "") != commit
    ):
        raise ReleaseError("annotated validation run is not a successful run for the tag commit")
    print(
        json.dumps(
            {
                "tag": tag,
                "version": version,
                "commit": commit,
                "release_plan_digest": match.group(1),
                "authorization_mode": authorization_mode,
                "expected_assets": expected_assets,
                "github_release_approval": github_approval,
                "validation_run": validation,
            },
            indent=2,
        )
    )
    return 0


def command_verify_assets(args: argparse.Namespace, root: Path) -> int:
    annotation = annotated_tag_contents(root, args.tag)
    assets = verify_asset_directory(annotation, Path(args.directory).resolve())
    print(json.dumps({"tag": args.tag, "assets": assets}, indent=2))
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=command_doctor)
    plan = sub.add_parser("plan")
    plan.add_argument("--version", required=True)
    plan.add_argument("--bootstrap-plan")
    plan.add_argument(
        "--development-issue",
        "--maintenance-issue",
        dest="maintenance_issue",
        help="Phase 1 Requirement or Maintenance Case authorizing the release",
    )
    plan.add_argument("--implementation-provenance", action="append", default=[])
    plan.add_argument("--multica-bin")
    plan.add_argument("--profile")
    plan.add_argument("--workspace")
    plan.set_defaults(func=command_plan)
    approval_block = sub.add_parser("approval-block")
    approval_block.add_argument("--plan", required=True)
    approval_block.add_argument("--multica-bin")
    approval_block.add_argument("--profile")
    approval_block.add_argument("--workspace")
    approval_block.set_defaults(func=command_approval_block)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--approve", required=True)
    apply.add_argument("--multica-bin")
    apply.add_argument("--profile")
    apply.add_argument("--workspace")
    apply.set_defaults(func=command_apply)
    recover = sub.add_parser("recover")
    recovery_source = recover.add_mutually_exclusive_group(required=True)
    recovery_source.add_argument("--tag")
    recovery_source.add_argument("--request")
    recover.set_defaults(func=command_recover)
    verify_request = sub.add_parser("verify-request")
    verify_request.add_argument("--request", required=True)
    verify_request.add_argument("--expected-source")
    verify_request.add_argument("--github-output")
    verify_request.set_defaults(func=command_verify_request)
    verify_request_payload = sub.add_parser("verify-request-payload")
    verify_request_payload.add_argument("--request", required=True)
    verify_request_payload.add_argument("--github-output")
    verify_request_payload.set_defaults(func=command_verify_request_payload)
    verify_publish_gate = sub.add_parser("verify-publish-gate")
    verify_publish_gate.add_argument("--request", required=True)
    verify_publish_gate.add_argument("--workflow-run-id", required=True)
    verify_publish_gate.add_argument("--environment", required=True)
    verify_publish_gate.add_argument("--output", required=True)
    verify_publish_gate.add_argument("--github-output")
    verify_publish_gate.set_defaults(func=command_verify_publish_gate)
    publish = sub.add_parser("publish")
    publish.add_argument("--request", required=True)
    publish.add_argument("--gate", required=True)
    publish.add_argument("--approve", required=True)
    publish.add_argument("--workflow-run-id", required=True)
    publish.add_argument("--environment", required=True)
    publish.add_argument("--recover-existing-tag", action="store_true")
    publish.set_defaults(func=command_publish)
    publish_release = sub.add_parser("publish-release")
    publish_release.add_argument("--request", required=True)
    publish_release.add_argument("--gate", required=True)
    publish_release.add_argument("--approve", required=True)
    publish_release.add_argument("--workflow-run-id", required=True)
    publish_release.add_argument("--environment", required=True)
    publish_release.add_argument("--directory", required=True)
    publish_release.add_argument("--recover-existing-tag", action="store_true")
    publish_release.set_defaults(func=command_publish_release)
    verify_tag = sub.add_parser("verify-tag")
    verify_tag.add_argument("--tag", required=True)
    verify_tag.set_defaults(func=command_verify_tag)
    verify_assets = sub.add_parser("verify-assets")
    verify_assets.add_argument("--tag", required=True)
    verify_assets.add_argument("--directory", required=True)
    verify_assets.set_defaults(func=command_verify_assets)
    return root


def main() -> int:
    args = parser().parse_args()
    root = Path(__file__).resolve().parents[1]
    try:
        return int(args.func(args, root))
    except (ReleaseError, WorkflowError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
