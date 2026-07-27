from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse


WORKFLOW_ID = "development-delivery"
CONFIG_NAME = "multica.delivery.json"
WORKSPACE_MODES = ("branch_only", "lightweight", "isolated")
PR_CONSTRAINTS = ("optional", "required", "forbidden")
PROVIDERS = ("auto", "github", "none")
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")

DEFAULT_POLICY: dict[str, Any] = {
    "schema_version": 1,
    "workflow_id": WORKFLOW_ID,
    "workspace_modes": {
        "allowed": list(WORKSPACE_MODES),
        "default": "lightweight",
    },
    "task_pr": {"constraint": "optional", "default": False},
    "requirement_pr": {"constraint": "optional", "default": True},
    "remote": {
        "provider": "auto",
        "allow_direct_default_push": False,
    },
}


class DeliveryPolicyError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    return digest(
        {
            key: snapshot[key]
            for key in (
                "schema_version",
                "workflow_id",
                "policy_source",
                "policy_file",
                "project_policy",
                "capabilities",
                "effective",
                "selection_source",
            )
        }
    )


def git(repo: Path, *args: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "git command failed"
        raise DeliveryPolicyError(detail)
    return completed.stdout.strip()


def repository_root(repo: Path) -> Path:
    candidate = repo.expanduser().resolve()
    root = git(candidate, "rev-parse", "--show-toplevel")
    if not root:
        raise DeliveryPolicyError(f"not a Git repository: {candidate}")
    return Path(root).resolve()


def _require_object(value: Any, location: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise DeliveryPolicyError(f"{location} must be an object")
    return value


def _reject_unknown(value: dict[str, Any], allowed: set[str], location: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DeliveryPolicyError(f"{location} has unknown fields: {', '.join(unknown)}")


def _normalize_pr_policy(value: Any, location: str) -> dict[str, Any]:
    item = _require_object(value, location)
    _reject_unknown(item, {"constraint", "default"}, location)
    if set(item) != {"constraint", "default"}:
        raise DeliveryPolicyError(f"{location} requires constraint and default")
    constraint = item["constraint"]
    default = item["default"]
    if constraint not in PR_CONSTRAINTS:
        raise DeliveryPolicyError(f"{location}.constraint must be optional, required, or forbidden")
    if not isinstance(default, bool):
        raise DeliveryPolicyError(f"{location}.default must be boolean")
    if constraint == "required" and not default:
        raise DeliveryPolicyError(f"{location}.default must be true when required")
    if constraint == "forbidden" and default:
        raise DeliveryPolicyError(f"{location}.default must be false when forbidden")
    return {"constraint": constraint, "default": default}


def normalize_policy(document: Any) -> dict[str, Any]:
    root = _require_object(document, "project policy")
    _reject_unknown(
        root,
        {
            "$schema",
            "schema_version",
            "workflow_id",
            "workspace_modes",
            "task_pr",
            "requirement_pr",
            "remote",
        },
        "project policy",
    )
    if root.get("schema_version") != 1:
        raise DeliveryPolicyError("project policy schema_version must be 1")
    if root.get("workflow_id") != WORKFLOW_ID:
        raise DeliveryPolicyError(f"project policy workflow_id must be {WORKFLOW_ID}")

    policy = json.loads(canonical_json(DEFAULT_POLICY))
    if "workspace_modes" in root:
        modes = _require_object(root["workspace_modes"], "workspace_modes")
        _reject_unknown(modes, {"allowed", "default"}, "workspace_modes")
        if set(modes) != {"allowed", "default"}:
            raise DeliveryPolicyError("workspace_modes requires allowed and default")
        allowed = modes["allowed"]
        default = modes["default"]
        if not isinstance(allowed, list) or not allowed:
            raise DeliveryPolicyError("workspace_modes.allowed must be a non-empty array")
        if any(item not in WORKSPACE_MODES for item in allowed):
            raise DeliveryPolicyError("workspace_modes.allowed contains an unsupported mode")
        if len(set(allowed)) != len(allowed):
            raise DeliveryPolicyError("workspace_modes.allowed must contain unique modes")
        if default not in allowed:
            raise DeliveryPolicyError("workspace_modes.default must be allowed")
        policy["workspace_modes"] = {"allowed": allowed, "default": default}

    for name in ("task_pr", "requirement_pr"):
        if name in root:
            policy[name] = _normalize_pr_policy(root[name], name)

    if "remote" in root:
        remote = _require_object(root["remote"], "remote")
        _reject_unknown(
            remote,
            {"name", "provider", "allow_direct_default_push"},
            "remote",
        )
        normalized = dict(policy["remote"])
        if "name" in remote:
            name = remote["name"]
            if not isinstance(name, str) or not REMOTE_NAME_RE.fullmatch(name):
                raise DeliveryPolicyError("remote.name is invalid")
            normalized["name"] = name
        if "provider" in remote:
            provider = remote["provider"]
            if provider not in PROVIDERS:
                raise DeliveryPolicyError("remote.provider must be auto, github, or none")
            normalized["provider"] = provider
        if "allow_direct_default_push" in remote:
            direct = remote["allow_direct_default_push"]
            if not isinstance(direct, bool):
                raise DeliveryPolicyError("remote.allow_direct_default_push must be boolean")
            normalized["allow_direct_default_push"] = direct
        policy["remote"] = normalized
    return policy


def load_policy(root: Path, config: str | None = None) -> tuple[dict[str, Any], str, str | None]:
    explicit = config is not None
    path = Path(config) if explicit else root / CONFIG_NAME
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as exc:
        raise DeliveryPolicyError("project policy must be inside the repository") from exc
    if not path.exists():
        if explicit:
            raise DeliveryPolicyError(f"project policy does not exist: {relative}")
        return json.loads(canonical_json(DEFAULT_POLICY)), "implicit_default", None
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeliveryPolicyError(f"cannot read project policy {relative}: {exc}") from exc
    return normalize_policy(document), "repository", relative


def remote_host(url: str) -> str | None:
    value = url.strip()
    scp = re.match(r"^[^@/]+@([^:/]+):.+$", value)
    if scp:
        return scp.group(1).lower()
    parsed = urlparse(value)
    return parsed.hostname.lower() if parsed.hostname else None


def repository_capabilities(root: Path, policy: dict[str, Any]) -> dict[str, Any]:
    remotes = [item for item in git(root, "remote").splitlines() if item]
    configured_name = policy["remote"].get("name")
    if configured_name:
        if configured_name not in remotes:
            raise DeliveryPolicyError(f"configured remote does not exist: {configured_name}")
        selected = configured_name
    elif "origin" in remotes:
        selected = "origin"
    elif len(remotes) == 1:
        selected = remotes[0]
    elif not remotes:
        selected = None
    else:
        raise DeliveryPolicyError("multiple remotes exist without origin or remote.name")

    provider_request = policy["remote"]["provider"]
    direct_push = policy["remote"]["allow_direct_default_push"]
    fetch_urls: list[str] = []
    push_urls: list[str] = []
    detected_provider = "none"
    if selected:
        fetch_urls = [
            item
            for item in git(root, "remote", "get-url", "--all", selected).splitlines()
            if item
        ]
        push_urls = [
            item
            for item in git(
                root, "remote", "get-url", "--push", "--all", selected
            ).splitlines()
            if item
        ]
        all_urls = fetch_urls + push_urls
        if all_urls and all(remote_host(item) == "github.com" for item in all_urls):
            detected_provider = "github"
    if provider_request == "github" and detected_provider != "github":
        raise DeliveryPolicyError("remote.provider=github requires a GitHub remote URL")
    provider = detected_provider if provider_request == "auto" else provider_request
    if not selected and provider != "none":
        raise DeliveryPolicyError("a PR provider requires a selected remote")
    if not selected and direct_push:
        raise DeliveryPolicyError("direct default-branch push cannot be enabled without a remote")
    fingerprint = (
        digest(
            {
                "name": selected,
                "fetch_urls": sorted(fetch_urls),
                "push_urls": sorted(push_urls),
            }
        )
        if selected
        else None
    )
    return {
        "git_repository": True,
        "remote_configured": selected is not None,
        "remote_name": selected,
        "remote_fingerprint": fingerprint,
        "remote_provider": provider,
        "pull_request_capable": bool(selected and provider == "github"),
        "direct_default_push": bool(selected and direct_push),
    }


def parse_selection(value: str | None) -> bool | None:
    if value is None:
        return None
    return value == "enabled"


def resolve_pr(
    name: str,
    requested: bool | None,
    policy: dict[str, Any],
    capable: bool,
) -> tuple[bool, str]:
    constraint = policy["constraint"]
    value = policy["default"] if requested is None else requested
    source = "project_default" if requested is None else "plan_selection"
    if not capable and requested is None and constraint == "optional":
        return False, "capability_default"
    if constraint == "required" and not value:
        raise DeliveryPolicyError(f"{name} is required by project policy")
    if constraint == "forbidden" and value:
        raise DeliveryPolicyError(f"{name} is forbidden by project policy")
    if value and not capable:
        raise DeliveryPolicyError(f"{name} requires a supported PR remote")
    return value, source


def resolve_policy(
    repo: Path,
    *,
    config: str | None = None,
    workspace_mode: str | None = None,
    task_pr: bool | None = None,
    requirement_pr: bool | None = None,
) -> dict[str, Any]:
    root = repository_root(repo)
    policy, policy_source, policy_file = load_policy(root, config)
    capabilities = repository_capabilities(root, policy)
    selected_mode = workspace_mode or policy["workspace_modes"]["default"]
    if selected_mode not in policy["workspace_modes"]["allowed"]:
        raise DeliveryPolicyError(f"workspace_mode is not allowed: {selected_mode}")
    task_value, task_source = resolve_pr(
        "task_pr", task_pr, policy["task_pr"], capabilities["pull_request_capable"]
    )
    requirement_value, requirement_source = resolve_pr(
        "requirement_pr",
        requirement_pr,
        policy["requirement_pr"],
        capabilities["pull_request_capable"],
    )
    if capabilities["remote_configured"] and not requirement_value:
        if not capabilities["direct_default_push"]:
            raise DeliveryPolicyError(
                "requirement_pr=disabled with a remote requires "
                "remote.allow_direct_default_push=true"
            )
    effective = {
        "workspace_mode": selected_mode,
        "task_pr": task_value,
        "requirement_pr": requirement_value,
        "parallel_tasks": selected_mode == "isolated",
        "workspace_lease_scope": {
            "branch_only": "repository",
            "lightweight": "requirement",
            "isolated": "task",
        }[selected_mode],
    }
    snapshot: dict[str, Any] = {
        "schema_version": 1,
        "workflow_id": WORKFLOW_ID,
        "policy_source": policy_source,
        "policy_file": policy_file,
        "project_policy": policy,
        "capabilities": capabilities,
        "effective": effective,
        "selection_source": {
            "workspace_mode": "plan_selection" if workspace_mode else "project_default",
            "task_pr": task_source,
            "requirement_pr": requirement_source,
        },
    }
    snapshot["policy_digest"] = snapshot_digest(snapshot)
    return snapshot


def verify_snapshot(repo: Path, snapshot: Any, config: str | None = None) -> dict[str, Any]:
    expected = _require_object(snapshot, "snapshot")
    required = {
        "schema_version",
        "workflow_id",
        "policy_source",
        "policy_file",
        "project_policy",
        "capabilities",
        "effective",
        "selection_source",
        "policy_digest",
    }
    missing = sorted(required - set(expected))
    if missing:
        raise DeliveryPolicyError(f"snapshot is missing fields: {', '.join(missing)}")
    if expected.get("schema_version") != 1 or expected.get("workflow_id") != WORKFLOW_ID:
        raise DeliveryPolicyError("snapshot protocol identity is invalid")
    effective = _require_object(expected.get("effective"), "snapshot.effective")
    selections = _require_object(
        expected.get("selection_source"), "snapshot.selection_source"
    )
    if effective.get("workspace_mode") not in WORKSPACE_MODES:
        raise DeliveryPolicyError("snapshot.effective.workspace_mode is invalid")
    if not isinstance(effective.get("task_pr"), bool) or not isinstance(
        effective.get("requirement_pr"), bool
    ):
        raise DeliveryPolicyError("snapshot PR selections must be boolean")
    for name in ("workspace_mode", "task_pr", "requirement_pr"):
        if selections.get(name) not in {
            "plan_selection",
            "project_default",
            "capability_default",
        }:
            raise DeliveryPolicyError(f"snapshot.selection_source.{name} is invalid")
    expected_digest = expected.get("policy_digest")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise DeliveryPolicyError("snapshot.policy_digest must be a SHA-256 digest")
    if snapshot_digest(expected) != expected_digest:
        raise DeliveryPolicyError("snapshot content does not match policy_digest")
    current = resolve_policy(
        repo,
        config=config or expected.get("policy_file"),
        workspace_mode=(
            effective["workspace_mode"]
            if selections["workspace_mode"] == "plan_selection"
            else None
        ),
        task_pr=(
            effective["task_pr"] if selections["task_pr"] == "plan_selection" else None
        ),
        requirement_pr=(
            effective["requirement_pr"]
            if selections["requirement_pr"] == "plan_selection"
            else None
        ),
    )
    valid = current["policy_digest"] == expected_digest
    return {
        "valid": valid,
        "expected_policy_digest": expected_digest,
        "current_policy_digest": current["policy_digest"],
        "current": current,
    }


def git_operation_markers(root: Path) -> list[str]:
    markers = [
        "MERGE_HEAD",
        "CHERRY_PICK_HEAD",
        "REVERT_HEAD",
        "BISECT_LOG",
        "rebase-apply",
        "rebase-merge",
        "sequencer",
    ]
    active: list[str] = []
    for marker in markers:
        path = git(root, "rev-parse", "--git-path", marker)
        marker_path = Path(path)
        if not marker_path.is_absolute():
            marker_path = root / marker_path
        if path and marker_path.exists():
            active.append(marker)
    return active


def guard_workspace(
    repo: Path,
    *,
    workspace_mode: str,
    expected_branch: str | None = None,
    expected_head: str | None = None,
) -> dict[str, Any]:
    if workspace_mode not in WORKSPACE_MODES:
        raise DeliveryPolicyError(f"unsupported workspace mode: {workspace_mode}")
    root = repository_root(repo)
    branch = git(root, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if not branch:
        raise DeliveryPolicyError("workspace has detached HEAD")
    head = git(root, "rev-parse", "HEAD")
    status = git(root, "status", "--porcelain=v1", "--untracked-files=all")
    if status:
        raise DeliveryPolicyError("workspace is dirty or contains untracked files")
    operations = git_operation_markers(root)
    if operations:
        raise DeliveryPolicyError(f"workspace has unfinished Git operations: {', '.join(operations)}")
    if expected_branch and branch != expected_branch:
        raise DeliveryPolicyError(f"unexpected branch: expected {expected_branch}, found {branch}")
    if expected_head:
        if not FULL_SHA_RE.fullmatch(expected_head):
            raise DeliveryPolicyError("expected head must be a full Git SHA")
        if head.lower() != expected_head.lower():
            raise DeliveryPolicyError(f"unexpected head: expected {expected_head}, found {head}")
    return {
        "valid": True,
        "workspace_mode": workspace_mode,
        "branch": branch,
        "head": head,
        "clean": True,
        "unfinished_operations": [],
    }


def print_json(value: Any) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Resolve Multica project delivery policy")
    subparsers = result.add_subparsers(dest="command", required=True)

    resolve = subparsers.add_parser("resolve")
    resolve.add_argument("--repo", default=".")
    resolve.add_argument("--config")
    resolve.add_argument("--workspace-mode", choices=WORKSPACE_MODES)
    resolve.add_argument("--task-pr", choices=("enabled", "disabled"))
    resolve.add_argument("--requirement-pr", choices=("enabled", "disabled"))

    verify = subparsers.add_parser("verify")
    verify.add_argument("--repo", default=".")
    verify.add_argument("--config")
    verify.add_argument("--snapshot", required=True)

    guard = subparsers.add_parser("guard-workspace")
    guard.add_argument("--repo", default=".")
    guard.add_argument("--workspace-mode", required=True, choices=WORKSPACE_MODES)
    guard.add_argument("--expected-branch")
    guard.add_argument("--expected-head")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "resolve":
            print_json(
                resolve_policy(
                    Path(args.repo),
                    config=args.config,
                    workspace_mode=args.workspace_mode,
                    task_pr=parse_selection(args.task_pr),
                    requirement_pr=parse_selection(args.requirement_pr),
                )
            )
            return 0
        if args.command == "verify":
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            result = verify_snapshot(Path(args.repo), snapshot, args.config)
            print_json(result)
            return 0 if result["valid"] else 1
        if args.command == "guard-workspace":
            print_json(
                guard_workspace(
                    Path(args.repo),
                    workspace_mode=args.workspace_mode,
                    expected_branch=args.expected_branch,
                    expected_head=args.expected_head,
                )
            )
            return 0
    except (DeliveryPolicyError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
