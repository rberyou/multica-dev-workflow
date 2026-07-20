#!/usr/bin/env python3
"""Deterministic Maintainer/Reviewer handoff loop for one Maintenance Issue."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


class LoopError(RuntimeError):
    pass


def discover_multica(explicit: str | None) -> str:
    if explicit:
        return explicit
    found = shutil.which("multica")
    if found:
        return found
    if os.name == "nt":
        candidate = (
            Path(os.environ.get("LOCALAPPDATA", ""))
            / "Programs/@multicadesktop/resources/app.asar.unpacked/resources/bin/multica.exe"
        )
        if candidate.is_file():
            return str(candidate)
    raise LoopError("multica CLI was not found")


class Multica:
    def __init__(self, binary: str, profile: str | None, workspace: str | None):
        self.binary = binary
        self.profile = profile
        self.workspace = workspace

    def command(self, args: list[str]) -> list[str]:
        command = [self.binary]
        if self.profile:
            command.extend(["--profile", self.profile])
        if self.workspace:
            command.extend(["--workspace-id", self.workspace])
        command.extend(args)
        return command

    def json(self, args: list[str], stdin: str | None = None):
        result = subprocess.run(
            self.command(args),
            input=stdin,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
        )
        if result.returncode != 0:
            raise LoopError(result.stderr.strip() or result.stdout.strip() or "multica command failed")
        return json.loads(result.stdout) if result.stdout.strip() else None


def metadata_map(value) -> dict:
    if isinstance(value, dict):
        if isinstance(value.get("metadata"), dict):
            return value["metadata"]
        return value
    result = {}
    for item in value or []:
        if isinstance(item, dict) and (item.get("key") or item.get("name")):
            result[str(item.get("key") or item.get("name"))] = item.get("value")
    return result


def caller_agent_id() -> str:
    value = os.environ.get("MULTICA_AGENT_ID", "").strip()
    if not value:
        raise LoopError("maintenance loop must run inside a Multica Agent task")
    return value


def require_sha(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r"[a-f0-9]{40,64}", value):
        raise LoopError("review commit must be a full hexadecimal commit SHA")
    return value


def tests_digest(path: str | None) -> str:
    if not path:
        return hashlib.sha256(b"none").hexdigest()
    data = Path(path).read_bytes()
    return hashlib.sha256(data).hexdigest()


def set_metadata(cli: Multica, issue: str, key: str, value: str) -> None:
    cli.json(
        [
            "issue", "metadata", "set", issue,
            "--key", key,
            "--value", value,
            "--type", "string",
            "--output", "json",
        ]
    )


def add_comment(cli: Multica, issue: str, content: str, parent: str | None) -> str:
    args = ["issue", "comment", "add", issue, "--content-stdin", "--output", "json"]
    if parent:
        args.extend(["--parent", parent])
    value = cli.json(args, stdin=content)
    comment_id = str((value or {}).get("id") or "")
    if not comment_id:
        raise LoopError("Multica did not return the handoff comment ID")
    return comment_id


def assign(cli: Multica, issue: str, actor_id: str) -> None:
    cli.json(["issue", "assign", issue, "--to-id", actor_id, "--output", "json"])


def status(cli: Multica, issue: str, value: str) -> None:
    cli.json(["issue", "status", issue, value, "--output", "json"])


def issue_context(cli: Multica, issue: str) -> tuple[dict, dict]:
    detail = cli.json(["issue", "get", issue, "--output", "json"])
    metadata = metadata_map(cli.json(["issue", "metadata", "list", issue, "--output", "json"]))
    if not isinstance(detail, dict):
        raise LoopError("Maintenance Issue is unreadable")
    return detail, metadata


def request_review(args, cli: Multica) -> dict:
    _, metadata = issue_context(cli, args.issue)
    caller = caller_agent_id()
    maintainer = str(metadata.get("maintainer_id") or "")
    reviewer = str(metadata.get("maintenance_reviewer_id") or "")
    if caller != maintainer or not reviewer or reviewer == maintainer:
        raise LoopError("review request identity does not match the durable Maintainer/Reviewer pair")
    revision = str(metadata.get("plan_revision") or "")
    if revision != args.plan_revision:
        raise LoopError("requested plan_revision differs from the Issue metadata")
    commit = require_sha(args.commit)
    content = "\n".join(
        [
            "REVIEW_REQUEST",
            f"review_kind={args.kind}",
            f"plan_revision={revision}",
            f"review_commit_sha={commit}",
            f"tests_sha256={tests_digest(args.tests_file)}",
            f"requested_by={caller}",
        ]
    )
    comment_id = add_comment(cli, args.issue, content, args.parent_comment)
    for key, value in {
        "review_request_comment_id": comment_id,
        "review_kind": args.kind,
        "review_status": "pending",
        "review_commit_sha": commit,
        "waiting_on": "maintenance_reviewer",
    }.items():
        set_metadata(cli, args.issue, key, value)
    status(cli, args.issue, "todo")
    assign(cli, args.issue, reviewer)
    return {"action": "review_requested", "comment_id": comment_id, "reviewer_id": reviewer}


def review(args, cli: Multica) -> dict:
    _, metadata = issue_context(cli, args.issue)
    caller = caller_agent_id()
    reviewer = str(metadata.get("maintenance_reviewer_id") or "")
    maintainer = str(metadata.get("maintainer_id") or "")
    human = str(metadata.get("human_approver_id") or "")
    if caller != reviewer or not maintainer or reviewer == maintainer:
        raise LoopError("review verdict identity does not match the durable Reviewer")
    revision = str(metadata.get("plan_revision") or "")
    commit = require_sha(args.commit)
    if revision != args.plan_revision or commit != str(metadata.get("review_commit_sha") or ""):
        raise LoopError("review verdict is stale relative to the pending request")
    verdict = args.verdict.upper()
    if verdict not in {"APPROVED", "CHANGES_REQUESTED", "DECISION_REQUIRED"}:
        raise LoopError("unsupported review verdict")
    lines = [
        verdict,
        f"review_kind={str(metadata.get('review_kind') or '')}",
        f"plan_revision={revision}",
        f"reviewed_commit_sha={commit}",
        f"tests_sha256={tests_digest(args.tests_file)}",
        f"reviewer_id={reviewer}",
    ]
    if args.findings_file:
        lines.extend(["", Path(args.findings_file).read_text(encoding="utf-8").strip()])
    comment_id = add_comment(cli, args.issue, "\n".join(lines), args.parent_comment)
    if verdict == "APPROVED":
        for key, value in {
            "review_comment_id": comment_id,
            "review_status": "approved",
            "reviewed_commit_sha": commit,
            "waiting_on": "human_plan_approval" if metadata.get("review_kind") == "plan" else "maintainer_post_review",
        }.items():
            set_metadata(cli, args.issue, key, value)
        if metadata.get("review_kind") == "plan":
            if not human:
                raise LoopError("human_approver_id is required for the Plan gate")
            status(cli, args.issue, "in_review")
            assign(cli, args.issue, human)
        else:
            status(cli, args.issue, "in_progress")
            assign(cli, args.issue, maintainer)
    elif verdict == "CHANGES_REQUESTED":
        set_metadata(cli, args.issue, "review_status", "changes_requested")
        set_metadata(cli, args.issue, "waiting_on", "maintainer_revision")
        status(cli, args.issue, "todo")
        assign(cli, args.issue, maintainer)
    else:
        if not human:
            raise LoopError("human_approver_id is required for a decision block")
        set_metadata(cli, args.issue, "review_status", "decision_required")
        set_metadata(cli, args.issue, "waiting_on", "human_decision")
        status(cli, args.issue, "blocked")
        assign(cli, args.issue, human)
    return {"action": verdict.lower(), "comment_id": comment_id}


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    root.add_argument("--multica-bin")
    root.add_argument("--profile")
    root.add_argument("--workspace")
    sub = root.add_subparsers(dest="command", required=True)
    request = sub.add_parser("request-review")
    request.add_argument("--issue", required=True)
    request.add_argument("--kind", choices=["plan", "implementation", "rollback", "release"], required=True)
    request.add_argument("--plan-revision", required=True)
    request.add_argument("--commit", required=True)
    request.add_argument("--tests-file")
    request.add_argument("--parent-comment")
    request.set_defaults(func=request_review)
    verdict = sub.add_parser("review")
    verdict.add_argument("--issue", required=True)
    verdict.add_argument("--verdict", choices=["approved", "changes_requested", "decision_required"], required=True)
    verdict.add_argument("--plan-revision", required=True)
    verdict.add_argument("--commit", required=True)
    verdict.add_argument("--tests-file")
    verdict.add_argument("--findings-file")
    verdict.add_argument("--parent-comment")
    verdict.set_defaults(func=review)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        cli = Multica(discover_multica(args.multica_bin), args.profile, args.workspace)
        print(json.dumps(args.func(args, cli), ensure_ascii=False, indent=2))
        return 0
    except (LoopError, OSError, json.JSONDecodeError) as error:
        print(f"maintenance-loop: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
