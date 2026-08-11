from __future__ import annotations

import argparse
import base64
import binascii
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
RESOLVER_ID = "multica-delivery-policy"
SNAPSHOT_SCHEMA_VERSION = 2
POLICY_DIGEST_SCHEMA_VERSION = 2
LEGACY_POLICY_DIGEST_SCHEMA_VERSION = 1
WORKSPACE_MODES = ("branch_only", "lightweight", "isolated")
PR_CONSTRAINTS = ("optional", "required", "forbidden")
PROVIDERS = ("auto", "github", "none")
FINAL_ACTIONS = ("open", "approve", "delivery", "handoff", "converge")
DELIVERY_MODES = ("requirement_pr", "direct_push", "local_only")
HANDOFF_OUTCOMES = ("queued", "coalesced", "deferred")
METADATA_RECORD_PREFIX = "v1."
MAX_ISSUE_METADATA_KEYS = 50
REMOTE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")
FULL_SHA_RE = re.compile(r"^[0-9a-fA-F]{40,64}$")
DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")

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


def encode_metadata_record(value: dict[str, Any]) -> str:
    payload = base64.urlsafe_b64encode(canonical_json(value).encode("utf-8"))
    return METADATA_RECORD_PREFIX + payload.decode("ascii").rstrip("=")


def decode_metadata_record(value: Any, name: str = "metadata record") -> dict[str, Any]:
    if not isinstance(value, str) or not value.startswith(METADATA_RECORD_PREFIX):
        raise DeliveryPolicyError(f"{name} is not a versioned metadata record")
    encoded = value[len(METADATA_RECORD_PREFIX) :]
    if not encoded or re.fullmatch(r"[A-Za-z0-9_-]+", encoded) is None:
        raise DeliveryPolicyError(f"{name} is invalid")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        decoded = base64.b64decode(
            padded.encode("ascii"), altchars=b"-_", validate=True
        ).decode("utf-8")
        record = json.loads(decoded)
    except (UnicodeError, ValueError, binascii.Error, json.JSONDecodeError) as exc:
        raise DeliveryPolicyError(f"{name} is invalid") from exc
    if not isinstance(record, dict) or record.get("schema_version") != 1:
        raise DeliveryPolicyError(f"{name} has an unsupported schema")
    if encode_metadata_record(record) != value:
        raise DeliveryPolicyError(f"{name} is not canonical")
    return record


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def resolver_package_version() -> str:
    skill_file = Path(__file__).resolve().parents[1] / "SKILL.md"
    try:
        content = skill_file.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeliveryPolicyError("cannot read Resolver package metadata") from exc
    matched = re.search(r"(?m)^\s*version:\s*([^\s]+)\s*$", content)
    if matched is None:
        raise DeliveryPolicyError("Resolver package version is missing")
    return matched.group(1)


def resolver_provenance() -> dict[str, Any]:
    implementation = Path(__file__).resolve()
    try:
        implementation_digest = hashlib.sha256(implementation.read_bytes()).hexdigest()
    except OSError as exc:
        raise DeliveryPolicyError("cannot hash Resolver implementation") from exc
    return {
        "resolver_id": RESOLVER_ID,
        "package_version": resolver_package_version(),
        "implementation_digest": implementation_digest,
        "snapshot_schema_version": SNAPSHOT_SCHEMA_VERSION,
        "policy_digest_schema_version": POLICY_DIGEST_SCHEMA_VERSION,
    }


def _digest_schema_version(snapshot: dict[str, Any]) -> int:
    value = snapshot.get("policy_digest_schema_version")
    if value is None and snapshot.get("schema_version") == 1:
        return LEGACY_POLICY_DIGEST_SCHEMA_VERSION
    if value not in {
        LEGACY_POLICY_DIGEST_SCHEMA_VERSION,
        POLICY_DIGEST_SCHEMA_VERSION,
    }:
        raise DeliveryPolicyError("snapshot policy digest schema is unsupported")
    return int(value)


def _semantic_project_policy(snapshot: dict[str, Any]) -> dict[str, Any]:
    raw = _require_object(snapshot.get("project_policy"), "snapshot.project_policy")
    normalized = normalize_policy(raw)
    if canonical_json(raw) != canonical_json(normalized):
        raise DeliveryPolicyError("snapshot.project_policy is not normalized")
    allowed = normalized["workspace_modes"]["allowed"]
    normalized["workspace_modes"]["allowed"] = [
        mode for mode in WORKSPACE_MODES if mode in allowed
    ]
    return normalized


def _semantic_capabilities(snapshot: dict[str, Any]) -> dict[str, Any]:
    capabilities = _require_object(
        snapshot.get("capabilities"), "snapshot.capabilities"
    )
    if capabilities.get("git_repository") is not True:
        raise DeliveryPolicyError("snapshot.capabilities.git_repository must be true")
    for name in ("remote_configured", "pull_request_capable"):
        if not isinstance(capabilities.get(name), bool):
            raise DeliveryPolicyError(f"snapshot.capabilities.{name} must be boolean")
    direct_target = capabilities.get("direct_target_push")
    direct_default = capabilities.get("direct_default_push")
    if direct_target is None:
        direct_target = direct_default
    if not isinstance(direct_target, bool):
        raise DeliveryPolicyError(
            "snapshot capabilities require a direct target-push boolean"
        )
    if direct_default is not None and direct_default != direct_target:
        raise DeliveryPolicyError(
            "snapshot direct_target_push and direct_default_push conflict"
        )
    remote_configured = capabilities["remote_configured"]
    remote_name = capabilities.get("remote_name")
    remote_fingerprint = capabilities.get("remote_fingerprint")
    if remote_configured:
        if not isinstance(remote_name, str) or not remote_name:
            raise DeliveryPolicyError("snapshot selected remote name is missing")
        if not _is_digest(remote_fingerprint):
            raise DeliveryPolicyError("snapshot remote fingerprint is invalid")
    elif remote_name is not None or remote_fingerprint is not None:
        raise DeliveryPolicyError("snapshot records remote identity without a remote")
    provider = capabilities.get("remote_provider")
    if provider not in {"github", "none"}:
        raise DeliveryPolicyError("snapshot remote provider is invalid")
    return {
        "remote_configured": remote_configured,
        "remote_name": remote_name,
        "remote_fingerprint": remote_fingerprint,
        "remote_provider": provider,
        "pull_request_capable": capabilities["pull_request_capable"],
        "direct_target_push": direct_target,
    }


def semantic_policy_projection(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("workflow_id") != WORKFLOW_ID:
        raise DeliveryPolicyError("snapshot workflow identity is invalid")
    policy_source = snapshot.get("policy_source")
    if policy_source not in {"implicit_default", "repository"}:
        raise DeliveryPolicyError("snapshot.policy_source is invalid")
    policy_file = snapshot.get("policy_file")
    if policy_source == "implicit_default" and policy_file is not None:
        raise DeliveryPolicyError("implicit policy snapshot must not name a policy file")
    if policy_source == "repository" and (
        not isinstance(policy_file, str) or not policy_file
    ):
        raise DeliveryPolicyError("repository policy snapshot must name its policy file")

    effective = _require_object(snapshot.get("effective"), "snapshot.effective")
    if effective.get("workspace_mode") not in WORKSPACE_MODES:
        raise DeliveryPolicyError("snapshot.effective.workspace_mode is invalid")
    for name in ("task_pr", "requirement_pr", "parallel_tasks"):
        if not isinstance(effective.get(name), bool):
            raise DeliveryPolicyError(f"snapshot.effective.{name} must be boolean")
    lease_scope = effective.get("workspace_lease_scope")
    expected_lease = {
        "branch_only": "repository",
        "lightweight": "requirement",
        "isolated": "task",
    }[effective["workspace_mode"]]
    if lease_scope != expected_lease:
        raise DeliveryPolicyError("snapshot workspace lease scope is inconsistent")
    if effective["parallel_tasks"] != (effective["workspace_mode"] == "isolated"):
        raise DeliveryPolicyError("snapshot parallel_tasks is inconsistent")

    selections = _require_object(
        snapshot.get("selection_source"), "snapshot.selection_source"
    )
    normalized_selections: dict[str, str] = {}
    for name in ("workspace_mode", "task_pr", "requirement_pr"):
        source = selections.get(name)
        if source not in {
            "plan_selection",
            "project_default",
            "capability_default",
        }:
            raise DeliveryPolicyError(f"snapshot.selection_source.{name} is invalid")
        normalized_selections[name] = source

    return {
        "policy_digest_schema_version": POLICY_DIGEST_SCHEMA_VERSION,
        "workflow_id": WORKFLOW_ID,
        "policy_source": policy_source,
        "policy_file": policy_file,
        "project_policy": _semantic_project_policy(snapshot),
        "capabilities": _semantic_capabilities(snapshot),
        "effective": {
            "workspace_mode": effective["workspace_mode"],
            "task_pr": effective["task_pr"],
            "requirement_pr": effective["requirement_pr"],
            "parallel_tasks": effective["parallel_tasks"],
            "workspace_lease_scope": lease_scope,
        },
        "selection_source": normalized_selections,
    }


def legacy_policy_digest(snapshot: dict[str, Any]) -> str:
    return digest(
        {
            "schema_version": 1,
            **{
                key: snapshot[key]
                for key in (
                    "workflow_id",
                    "policy_source",
                    "policy_file",
                    "project_policy",
                    "capabilities",
                    "effective",
                    "selection_source",
                )
            },
        }
    )


def semantic_policy_digest(snapshot: dict[str, Any]) -> str:
    return digest(semantic_policy_projection(snapshot))


def snapshot_digest(snapshot: dict[str, Any]) -> str:
    if _digest_schema_version(snapshot) == LEGACY_POLICY_DIGEST_SCHEMA_VERSION:
        return legacy_policy_digest(snapshot)
    return semantic_policy_digest(snapshot)


def snapshot_record_digest(snapshot: dict[str, Any]) -> str:
    return digest(
        {key: value for key, value in snapshot.items() if key != "snapshot_record_digest"}
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
        raise DeliveryPolicyError("direct target-branch push cannot be enabled without a remote")
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
        "direct_target_push": bool(selected and direct_push),
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
        if not capabilities["direct_target_push"]:
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
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "workflow_id": WORKFLOW_ID,
        "resolver_provenance": resolver_provenance(),
        "policy_digest_schema_version": POLICY_DIGEST_SCHEMA_VERSION,
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
    snapshot["snapshot_record_digest"] = snapshot_record_digest(snapshot)
    return snapshot


def _validate_resolver_provenance(value: Any) -> dict[str, Any]:
    provenance = _require_object(value, "snapshot.resolver_provenance")
    if provenance.get("resolver_id") != RESOLVER_ID:
        raise DeliveryPolicyError("snapshot Resolver identity is invalid")
    if not isinstance(provenance.get("package_version"), str) or not provenance.get(
        "package_version"
    ):
        raise DeliveryPolicyError("snapshot Resolver package version is missing")
    if not _is_digest(provenance.get("implementation_digest")):
        raise DeliveryPolicyError("snapshot Resolver implementation digest is invalid")
    if provenance.get("snapshot_schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise DeliveryPolicyError("snapshot Resolver snapshot schema is invalid")
    if (
        provenance.get("policy_digest_schema_version")
        != POLICY_DIGEST_SCHEMA_VERSION
    ):
        raise DeliveryPolicyError("snapshot Resolver digest schema is invalid")
    return provenance


def validate_policy_snapshot(snapshot: Any) -> dict[str, Any]:
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
    schema_version = expected.get("schema_version")
    if schema_version not in {1, SNAPSHOT_SCHEMA_VERSION}:
        raise DeliveryPolicyError("snapshot protocol identity is invalid")
    if expected.get("workflow_id") != WORKFLOW_ID:
        raise DeliveryPolicyError("snapshot protocol identity is invalid")
    digest_schema_version = _digest_schema_version(expected)
    if schema_version == 1 and (
        digest_schema_version != LEGACY_POLICY_DIGEST_SCHEMA_VERSION
    ):
        raise DeliveryPolicyError("legacy snapshot digest schema is invalid")
    if schema_version == SNAPSHOT_SCHEMA_VERSION:
        versioned_required = {
            "resolver_provenance",
            "policy_digest_schema_version",
            "snapshot_record_digest",
        }
        versioned_missing = sorted(versioned_required - set(expected))
        if versioned_missing:
            raise DeliveryPolicyError(
                "snapshot is missing fields: " + ", ".join(versioned_missing)
            )
        if digest_schema_version != POLICY_DIGEST_SCHEMA_VERSION:
            raise DeliveryPolicyError("snapshot policy digest schema is invalid")
        _validate_resolver_provenance(expected.get("resolver_provenance"))
    semantic_policy_projection(expected)
    expected_digest = expected.get("policy_digest")
    if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest):
        raise DeliveryPolicyError("snapshot.policy_digest must be a SHA-256 digest")
    if snapshot_digest(expected) != expected_digest:
        raise DeliveryPolicyError("snapshot content does not match policy_digest")
    if schema_version == SNAPSHOT_SCHEMA_VERSION:
        expected_record_digest = expected.get("snapshot_record_digest")
        if not _is_digest(expected_record_digest):
            raise DeliveryPolicyError(
                "snapshot.snapshot_record_digest must be a SHA-256 digest"
            )
        if snapshot_record_digest(expected) != expected_record_digest:
            raise DeliveryPolicyError(
                "snapshot content does not match snapshot_record_digest"
            )
    return expected


def _snapshot_identity_digest(snapshot: dict[str, Any]) -> str:
    if snapshot.get("schema_version") == SNAPSHOT_SCHEMA_VERSION:
        return str(snapshot["snapshot_record_digest"])
    return digest(snapshot)


def _snapshot_provenance(snapshot: dict[str, Any]) -> dict[str, Any]:
    if snapshot.get("schema_version") == SNAPSHOT_SCHEMA_VERSION:
        return dict(snapshot["resolver_provenance"])
    return {
        "resolver_id": RESOLVER_ID,
        "provenance_status": "legacy_undeclared",
        "snapshot_schema_version": 1,
        "policy_digest_schema_version": LEGACY_POLICY_DIGEST_SCHEMA_VERSION,
    }


def _policy_digest_recovery_record(
    expected: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    expected_semantic_digest = semantic_policy_digest(expected)
    current_semantic_digest = semantic_policy_digest(current)
    if expected_semantic_digest != current_semantic_digest:
        raise DeliveryPolicyError(
            "a policy digest recovery record requires semantic equivalence"
        )
    superseded: list[dict[str, Any]] = []
    legacy_candidates = [
        (
            legacy_policy_digest(current),
            "legacy full-snapshot projection changed without semantic drift",
        )
    ]
    current_capabilities = _require_object(
        current.get("capabilities"), "current snapshot capabilities"
    )
    if (
        "direct_target_push" in current_capabilities
        and current_capabilities.get("direct_target_push")
        == current_capabilities.get("direct_default_push")
    ):
        predecessor = json.loads(canonical_json(current))
        predecessor["capabilities"].pop("direct_target_push", None)
        legacy_candidates.append(
            (
                legacy_policy_digest(predecessor),
                "legacy direct_default_push-only projection was superseded by canonical target-push semantics",
            )
        )
    authoritative = {
        str(expected["policy_digest"]),
        str(current["policy_digest"]),
    }
    seen = set(authoritative)
    for legacy_digest, reason in legacy_candidates:
        if legacy_digest in seen:
            continue
        superseded.append(
            {
                "policy_digest": legacy_digest,
                "policy_digest_schema_version": LEGACY_POLICY_DIGEST_SCHEMA_VERSION,
                "reason": reason,
            }
        )
        seen.add(legacy_digest)
    record: dict[str, Any] = {
        "schema_version": 1,
        "workflow_id": WORKFLOW_ID,
        "recovery_kind": "resolver_digest_schema_supersession",
        "frozen_snapshot_identity_digest": _snapshot_identity_digest(expected),
        "pinned_policy_digest": expected["policy_digest"],
        "policy_digest_to_propagate": expected["policy_digest"],
        "pinned_policy_digest_schema_version": _digest_schema_version(expected),
        "resolved_policy_digest": current["policy_digest"],
        "resolved_policy_digest_schema_version": _digest_schema_version(current),
        "semantic_policy_digest": current_semantic_digest,
        "superseded_policy_digests": superseded,
        "frozen_resolver_provenance": _snapshot_provenance(expected),
        "resolved_resolver_provenance": _snapshot_provenance(current),
        "requires_plan_revision": False,
    }
    record["record_digest"] = digest(record)
    return record


def _decode_policy_digest_recovery_record(value: Any) -> dict[str, Any]:
    if isinstance(value, dict) and "policy_digest_recovery_record" in value:
        value = value["policy_digest_recovery_record"]
    if isinstance(value, dict):
        value = encode_metadata_record(value)
    record = decode_metadata_record(value, "policy_digest_recovery_record")
    expected_record_digest = record.get("record_digest")
    if not _is_digest(expected_record_digest):
        raise DeliveryPolicyError("policy_digest_recovery_record digest is invalid")
    payload = {key: item for key, item in record.items() if key != "record_digest"}
    if digest(payload) != expected_record_digest:
        raise DeliveryPolicyError("policy_digest_recovery_record was modified")
    return record


def _validate_policy_digest_recovery_record(
    value: Any,
    expected: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    record = _decode_policy_digest_recovery_record(value)
    required_values = {
        "workflow_id": WORKFLOW_ID,
        "recovery_kind": "resolver_digest_schema_supersession",
        "frozen_snapshot_identity_digest": _snapshot_identity_digest(expected),
        "pinned_policy_digest": expected["policy_digest"],
        "policy_digest_to_propagate": expected["policy_digest"],
        "pinned_policy_digest_schema_version": _digest_schema_version(expected),
        "resolved_policy_digest": current["policy_digest"],
        "resolved_policy_digest_schema_version": _digest_schema_version(current),
        "semantic_policy_digest": semantic_policy_digest(current),
        "frozen_resolver_provenance": _snapshot_provenance(expected),
        "requires_plan_revision": False,
    }
    mismatched = [
        key for key, expected_value in required_values.items()
        if record.get(key) != expected_value
    ]
    if mismatched:
        raise DeliveryPolicyError(
            "policy_digest_recovery_record does not match the frozen/current policy: "
            + ", ".join(sorted(mismatched))
        )
    resolved_provenance = _require_object(
        record.get("resolved_resolver_provenance"),
        "policy_digest_recovery_record.resolved_resolver_provenance",
    )
    if resolved_provenance.get("resolver_id") != RESOLVER_ID:
        raise DeliveryPolicyError(
            "policy_digest_recovery_record Resolver identity is invalid"
        )
    if resolved_provenance.get("provenance_status") == "legacy_undeclared":
        if (
            resolved_provenance.get("snapshot_schema_version") != 1
            or resolved_provenance.get("policy_digest_schema_version")
            != LEGACY_POLICY_DIGEST_SCHEMA_VERSION
        ):
            raise DeliveryPolicyError(
                "policy_digest_recovery_record legacy Resolver provenance is invalid"
            )
    elif (
        not isinstance(resolved_provenance.get("package_version"), str)
        or not resolved_provenance.get("package_version")
        or not _is_digest(resolved_provenance.get("implementation_digest"))
        or resolved_provenance.get("snapshot_schema_version")
        != SNAPSHOT_SCHEMA_VERSION
        or resolved_provenance.get("policy_digest_schema_version")
        != POLICY_DIGEST_SCHEMA_VERSION
    ):
        raise DeliveryPolicyError(
            "policy_digest_recovery_record Resolver provenance is invalid"
        )

    superseded = record.get("superseded_policy_digests")
    if not isinstance(superseded, list):
        raise DeliveryPolicyError(
            "policy_digest_recovery_record superseded digest evidence is invalid"
        )
    seen: set[str] = set()
    authoritative = {str(expected["policy_digest"]), str(current["policy_digest"])}
    for item in superseded:
        if not isinstance(item, dict):
            raise DeliveryPolicyError(
                "policy_digest_recovery_record superseded digest evidence is invalid"
            )
        item_digest = item.get("policy_digest")
        if (
            not _is_digest(item_digest)
            or item_digest in authoritative
            or item_digest in seen
            or item.get("policy_digest_schema_version")
            != LEGACY_POLICY_DIGEST_SCHEMA_VERSION
            or not isinstance(item.get("reason"), str)
            or not item.get("reason")
        ):
            raise DeliveryPolicyError(
                "policy_digest_recovery_record superseded digest evidence is invalid"
            )
        seen.add(item_digest)
    return record


def verify_resolved_snapshots(
    expected_snapshot: Any,
    current_snapshot: Any,
    recovery_record: Any = None,
) -> dict[str, Any]:
    expected = validate_policy_snapshot(expected_snapshot)
    current = validate_policy_snapshot(current_snapshot)
    expected_digest = str(expected["policy_digest"])
    current_digest = str(current["policy_digest"])
    expected_digest_schema = _digest_schema_version(expected)
    current_digest_schema = _digest_schema_version(current)
    expected_semantic_digest = semantic_policy_digest(expected)
    current_semantic_digest = semantic_policy_digest(current)
    semantically_equivalent = expected_semantic_digest == current_semantic_digest
    common = {
        "expected_policy_digest": expected_digest,
        "current_policy_digest": current_digest,
        "expected_policy_digest_schema_version": expected_digest_schema,
        "current_policy_digest_schema_version": current_digest_schema,
        "expected_semantic_policy_digest": expected_semantic_digest,
        "current_semantic_policy_digest": current_semantic_digest,
        "semantically_equivalent": semantically_equivalent,
        "expected_resolver_provenance": _snapshot_provenance(expected),
        "current_resolver_provenance": _snapshot_provenance(current),
        "current": current,
    }
    if not semantically_equivalent:
        return {
            "valid": False,
            "verification_outcome": "semantic_drift",
            "recovery_required": False,
            "requires_plan_revision": True,
            "policy_digest_to_propagate": expected_digest,
            "policy_digest_recovery_record": None,
            "superseded_policy_digests": [],
            **common,
        }
    if (
        expected_digest == current_digest
        and expected_digest_schema == current_digest_schema
    ):
        return {
            "valid": True,
            "verification_outcome": "exact_match",
            "recovery_required": False,
            "requires_plan_revision": False,
            "policy_digest_to_propagate": expected_digest,
            "policy_digest_recovery_record": None,
            "superseded_policy_digests": [],
            **common,
        }

    proposed = _policy_digest_recovery_record(expected, current)
    encoded_proposed = encode_metadata_record(proposed)
    if recovery_record is None:
        return {
            "valid": False,
            "verification_outcome": "recovery_required",
            "recovery_required": True,
            "requires_plan_revision": False,
            "policy_digest_to_propagate": expected_digest,
            "policy_digest_recovery_record": encoded_proposed,
            "superseded_policy_digests": proposed["superseded_policy_digests"],
            **common,
        }
    accepted = _validate_policy_digest_recovery_record(
        recovery_record, expected, current
    )
    return {
        "valid": True,
        "verification_outcome": "pinned_equivalent",
        "recovery_required": False,
        "requires_plan_revision": False,
        "policy_digest_to_propagate": expected_digest,
        "policy_digest_recovery_record": encode_metadata_record(accepted),
        "superseded_policy_digests": accepted["superseded_policy_digests"],
        **common,
    }


def verify_snapshot(
    repo: Path,
    snapshot: Any,
    config: str | None = None,
    recovery_record: Any = None,
) -> dict[str, Any]:
    expected = validate_policy_snapshot(snapshot)
    effective = _require_object(expected.get("effective"), "snapshot.effective")
    selections = _require_object(
        expected.get("selection_source"), "snapshot.selection_source"
    )
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
    return verify_resolved_snapshots(expected, current, recovery_record)


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and FULL_SHA_RE.fullmatch(value) is not None


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and DIGEST_RE.fullmatch(value) is not None


def _add(reasons: list[str], condition: bool, message: str) -> None:
    if not condition:
        reasons.append(message)


def _root_reasons(root: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    issue_id = root.get("issue_id")
    _add(reasons, isinstance(issue_id, str) and bool(issue_id), "root issue_id is missing")
    _add(
        reasons,
        root.get("workflow_object_type") == "requirement",
        "final approval is only valid for a top-level Requirement",
    )
    _add(
        reasons,
        root.get("root_requirement_id") == issue_id,
        "root_requirement_id must equal the target Requirement issue_id",
    )
    _add(reasons, root.get("plan_status") == "done", "Plan must be done")
    _add(
        reasons,
        root.get("implementation_status") == "done",
        "Implementation must be done before final approval",
    )
    _add(
        reasons,
        root.get("integration_validation_status") == "done",
        "integration validation must be done",
    )
    _add(
        reasons,
        root.get("integration_review_status") == "approved",
        "integration Review is not approved",
    )
    _add(reasons, root.get("tests_passed") is True, "required tests are not complete")
    _add(
        reasons,
        root.get("acceptance_complete") is True,
        "Requirement acceptance evidence is incomplete",
    )
    _add(reasons, root.get("policy_valid") is True, "delivery policy is not current")
    _add(reasons, root.get("blockers_clear") is True, "Requirement blockers are not clear")
    _add(
        reasons,
        root.get("dependencies_satisfied") is True,
        "dependency contract is not satisfied",
    )
    revision = root.get("plan_revision")
    _add(
        reasons,
        isinstance(revision, int) and not isinstance(revision, bool) and revision > 0,
        "plan_revision must be a positive integer",
    )
    _add(
        reasons,
        _is_digest(root.get("delivery_policy_digest")),
        "delivery_policy_digest is invalid",
    )
    _add(
        reasons,
        _is_sha(root.get("reviewed_commit_sha")),
        "reviewed_commit_sha is invalid",
    )
    _add(
        reasons,
        isinstance(root.get("human_approver_id"), str)
        and bool(root.get("human_approver_id")),
        "human_approver_id is missing",
    )
    _add(
        reasons,
        isinstance(root.get("target_branch"), str) and bool(root.get("target_branch")),
        "target_branch is missing",
    )
    _add(reasons, _is_sha(root.get("target_base_sha")), "target_base_sha is invalid")
    _add(
        reasons,
        isinstance(root.get("default_branch"), str)
        and bool(root.get("default_branch")),
        "default_branch is missing",
    )
    _add(
        reasons,
        _is_sha(root.get("default_base_sha")),
        "default_base_sha is invalid",
    )
    metadata_keys = root.get("metadata_keys")
    _add(
        reasons,
        isinstance(metadata_keys, list)
        and all(isinstance(key, str) and bool(key) for key in metadata_keys)
        and len(metadata_keys) == len(set(metadata_keys)),
        "root metadata key inventory is invalid",
    )
    return reasons


def _metadata_capacity_reasons(
    root: dict[str, Any], update_keys: Any
) -> list[str]:
    metadata_keys = root.get("metadata_keys")
    if not isinstance(metadata_keys, list):
        return ["root metadata key inventory is invalid"]
    projected = set(metadata_keys)
    projected.update(str(key) for key in update_keys)
    if len(projected) > MAX_ISSUE_METADATA_KEYS:
        return [
            "metadata updates exceed the platform 50-key limit "
            f"({len(metadata_keys)} current, {len(projected)} projected)"
        ]
    return []


def _gate_matches(root: dict[str, Any], state: str) -> bool:
    return (
        root.get("final_approval_gate_state") == state
        and root.get("final_approval_gate_revision") == root.get("plan_revision")
        and root.get("final_approval_gate_reviewed_commit_sha")
        == root.get("reviewed_commit_sha")
        and root.get("final_approval_gate_policy_digest")
        == root.get("delivery_policy_digest")
    )


def _approval_matches(root: dict[str, Any]) -> bool:
    return (
        root.get("approval_author_type") == "member"
        and root.get("approval_author_id") == root.get("human_approver_id")
        and isinstance(root.get("approval_comment_id"), str)
        and bool(root.get("approval_comment_id"))
        and root.get("approval_revision") == root.get("plan_revision")
        and root.get("approved_requirement_head_sha")
        == root.get("reviewed_commit_sha")
        and root.get("approved_delivery_policy_digest")
        == root.get("delivery_policy_digest")
    )


def _delivery_reasons(
    root: dict[str, Any], delivery_value: Any
) -> list[str]:
    reasons: list[str] = []
    if not isinstance(delivery_value, dict):
        return ["delivery evidence is missing"]
    delivery = delivery_value
    mode = delivery.get("mode")
    _add(reasons, mode in DELIVERY_MODES, "delivery mode is invalid")
    _add(reasons, delivery.get("state") == "complete", "delivery state is not complete")
    _add(
        reasons,
        delivery.get("plan_revision") == root.get("plan_revision"),
        "delivery Plan revision drifted",
    )
    _add(
        reasons,
        delivery.get("delivery_policy_digest") == root.get("delivery_policy_digest"),
        "delivery policy digest drifted",
    )
    reviewed = root.get("reviewed_commit_sha")
    target_base = root.get("target_base_sha")
    default_base = root.get("default_base_sha")
    merged = delivery.get("merged_commit_sha")
    _add(
        reasons,
        delivery.get("reviewed_commit_sha") == reviewed,
        "delivered Requirement head does not match reviewed head",
    )
    _add(
        reasons,
        delivery.get("current_requirement_head_sha") == reviewed,
        "current Requirement head drifted after Review",
    )
    _add(
        reasons,
        delivery.get("target_branch") == root.get("target_branch"),
        "delivery target branch changed",
    )
    _add(
        reasons,
        delivery.get("verified_target_base_sha") == target_base,
        "target branch baseline drifted",
    )
    _add(
        reasons,
        delivery.get("verified_default_base_sha") == default_base,
        "default branch baseline drifted",
    )
    _add(reasons, _is_sha(merged), "merged_commit_sha is invalid")
    _add(
        reasons,
        isinstance(delivery.get("merge_method"), str)
        and bool(delivery.get("merge_method")),
        "merge_method is missing",
    )
    _add(
        reasons,
        delivery.get("merge_parent_target_sha") == target_base,
        "merge target parent does not match target_base_sha",
    )
    _add(
        reasons,
        delivery.get("merge_parent_requirement_sha") == reviewed,
        "merge Requirement parent does not match reviewed head",
    )
    _add(
        reasons,
        _is_sha(delivery.get("reviewed_tree_sha")),
        "reviewed_tree_sha is invalid",
    )
    _add(
        reasons,
        delivery.get("merged_tree_sha") == delivery.get("reviewed_tree_sha"),
        "merged tree does not match the reviewed Requirement tree",
    )
    _add(
        reasons,
        delivery.get("local_target_sha") == merged,
        "local target branch does not contain the recorded merge commit",
    )

    requirement_pr_enabled = root.get("requirement_pr_enabled") is True
    remote_configured = root.get("remote_configured") is True
    if mode == "requirement_pr":
        _add(reasons, requirement_pr_enabled, "Requirement PR delivery is not enabled")
        _add(reasons, remote_configured, "Requirement PR delivery requires a remote")
        _add(
            reasons,
            isinstance(delivery.get("pr_url"), str) and bool(delivery.get("pr_url")),
            "Requirement PR URL is missing",
        )
        _add(
            reasons,
            isinstance(delivery.get("pr_number"), int)
            and not isinstance(delivery.get("pr_number"), bool)
            and delivery.get("pr_number") > 0,
            "Requirement PR number is invalid",
        )
        _add(
            reasons,
            delivery.get("required_checks_passed") is True,
            "Requirement PR required checks are not complete",
        )
        _add(reasons, delivery.get("pr_merged") is True, "Requirement PR is not merged")
        _add(
            reasons,
            delivery.get("pr_base_branch") == root.get("target_branch"),
            "Requirement PR base is not the Plan target branch",
        )
        _add(
            reasons,
            delivery.get("pr_head_sha") == reviewed,
            "Requirement PR head does not match reviewed head",
        )
        _add(
            reasons,
            delivery.get("pr_merge_commit_sha") == merged,
            "Requirement PR merge commit does not match delivery evidence",
        )
        _add(
            reasons,
            delivery.get("remote_target_sha") == merged,
            "remote target branch does not contain the PR merge commit",
        )
    elif mode == "direct_push":
        _add(reasons, not requirement_pr_enabled, "direct push conflicts with Requirement PR")
        _add(reasons, remote_configured, "direct push requires a remote")
        _add(
            reasons,
            isinstance(delivery.get("remote_name"), str)
            and bool(delivery.get("remote_name")),
            "direct push remote name is missing",
        )
        _add(
            reasons,
            delivery.get("remote_verified") is True,
            "direct push remote/auth state is not verified",
        )
        _add(
            reasons,
            root.get("direct_target_push") is True
            or root.get("direct_default_push") is True,
            "project policy does not allow direct target-branch push",
        )
        _add(reasons, delivery.get("push_completed") is True, "target push is incomplete")
        _add(
            reasons,
            delivery.get("remote_target_sha") == merged,
            "remote target branch does not contain the merge commit",
        )
    elif mode == "local_only":
        _add(reasons, not requirement_pr_enabled, "local-only delivery conflicts with Requirement PR")
        _add(reasons, not remote_configured, "local-only delivery requires no remote")
        _add(
            reasons,
            delivery.get("push_completed") in (None, False),
            "local-only delivery must not record a push",
        )
        _add(
            reasons,
            delivery.get("remote_target_sha") in (None, ""),
            "local-only delivery must not record a remote target SHA",
        )
    return reasons


def _delivery_record(delivery: dict[str, Any]) -> dict[str, Any]:
    keys = [
        "state",
        "mode",
        "plan_revision",
        "delivery_policy_digest",
        "reviewed_commit_sha",
        "current_requirement_head_sha",
        "target_branch",
        "verified_target_base_sha",
        "verified_default_base_sha",
        "merge_method",
        "merged_commit_sha",
        "merge_parent_target_sha",
        "merge_parent_requirement_sha",
        "reviewed_tree_sha",
        "merged_tree_sha",
        "local_target_sha",
    ]
    if delivery.get("mode") == "requirement_pr":
        keys.extend(
            [
                "pr_merged",
                "pr_url",
                "pr_number",
                "required_checks_passed",
                "pr_base_branch",
                "pr_head_sha",
                "pr_merge_commit_sha",
                "remote_target_sha",
            ]
        )
    elif delivery.get("mode") == "direct_push":
        keys.extend(
            [
                "push_completed",
                "remote_name",
                "remote_verified",
                "remote_target_sha",
            ]
        )
    else:
        keys.extend(["push_completed", "remote_target_sha"])
    return {
        "schema_version": 1,
        **{key: delivery.get(key) for key in keys if key in delivery},
    }


def _recorded_delivery_reasons(
    root: dict[str, Any], current_delivery: Any
) -> list[str]:
    reasons: list[str] = []
    try:
        recorded = decode_metadata_record(
            root.get("delivery_evidence_record"), "delivery_evidence_record"
        )
    except DeliveryPolicyError as exc:
        return [str(exc)]
    reasons.extend(_delivery_reasons(root, recorded))
    current_reasons = _delivery_reasons(root, current_delivery)
    reasons.extend(current_reasons)
    if not current_reasons and isinstance(current_delivery, dict):
        _add(
            reasons,
            recorded == _delivery_record(current_delivery),
            "recorded delivery evidence does not match current delivery evidence",
        )
    return reasons


def _handoff_record(
    root: dict[str, Any], handoff: dict[str, Any], role: str, outcome: str
) -> dict[str, Any]:
    delivery_record = str(root["delivery_evidence_record"])
    return {
        "schema_version": 1,
        "issue_id": root["issue_id"],
        "comment_id": handoff["comment_id"],
        "target": role,
        "trigger_outcome": outcome,
        "plan_revision": root["plan_revision"],
        "reviewed_commit_sha": root["reviewed_commit_sha"],
        "delivery_policy_digest": root["delivery_policy_digest"],
        "delivery_record_digest": hashlib.sha256(
            delivery_record.encode("utf-8")
        ).hexdigest(),
    }


def _recorded_handoff_reasons(root: dict[str, Any]) -> list[str]:
    try:
        handoff = decode_metadata_record(
            root.get("delivery_handoff_record"), "delivery_handoff_record"
        )
    except DeliveryPolicyError as exc:
        return [str(exc)]
    reasons: list[str] = []
    _add(
        reasons,
        handoff.get("issue_id") == root.get("issue_id"),
        "recorded handoff does not target the Requirement",
    )
    _add(
        reasons,
        isinstance(handoff.get("comment_id"), str)
        and bool(handoff.get("comment_id")),
        "recorded handoff comment_id is missing",
    )
    _add(
        reasons,
        handoff.get("target") in {"leader", "squad"},
        "recorded handoff target is invalid",
    )
    _add(
        reasons,
        handoff.get("trigger_outcome") in HANDOFF_OUTCOMES,
        "recorded handoff trigger outcome is invalid",
    )
    _add(
        reasons,
        handoff.get("plan_revision") == root.get("plan_revision"),
        "recorded handoff revision is stale",
    )
    _add(
        reasons,
        handoff.get("reviewed_commit_sha") == root.get("reviewed_commit_sha"),
        "recorded handoff reviewed head is stale",
    )
    _add(
        reasons,
        handoff.get("delivery_policy_digest")
        == root.get("delivery_policy_digest"),
        "recorded handoff policy digest is stale",
    )
    delivery_record = root.get("delivery_evidence_record")
    current_record_digest = (
        hashlib.sha256(delivery_record.encode("utf-8")).hexdigest()
        if isinstance(delivery_record, str)
        else None
    )
    _add(
        reasons,
        handoff.get("delivery_record_digest") == current_record_digest,
        "recorded handoff does not match current delivery evidence",
    )
    return reasons


def _rejected(action: str, reasons: list[str], *, retry_required: bool = False) -> dict[str, Any]:
    return {
        "allowed": False,
        "action": action,
        "outcome": "rejected",
        "reasons": reasons,
        "metadata_updates": {},
        "status_write": None,
        "merge_required": False,
        "resume_delivery": False,
        "wake_leader": False,
        "retry_required": retry_required,
    }


def final_gate_transition(snapshot: Any, action: str) -> dict[str, Any]:
    data = _require_object(snapshot, "final gate snapshot")
    root = _require_object(data.get("root"), "final gate snapshot.root")
    if action not in FINAL_ACTIONS:
        raise DeliveryPolicyError(f"unsupported final gate action: {action}")
    if root.get("status") == "done":
        return {
            "allowed": True,
            "action": action,
            "outcome": "already_done",
            "reasons": [],
            "metadata_updates": {},
            "status_write": None,
            "merge_required": False,
            "resume_delivery": False,
            "wake_leader": False,
            "retry_required": False,
        }

    reasons = _root_reasons(root)
    actor_role = data.get("actor_role")
    if action == "open":
        _add(reasons, actor_role == "leader", "only Leader may open the final approval gate")
        _add(
            reasons,
            root.get("status") in {"in_progress", "in_review"},
            "Requirement must be active before opening final approval",
        )
        if reasons:
            return _rejected(action, reasons)
        if _gate_matches(root, "accepted") and _approval_matches(root):
            return {
                "allowed": True,
                "action": action,
                "outcome": "already_accepted",
                "reasons": [],
                "metadata_updates": {},
                "status_write": None,
                "merge_required": False,
                "resume_delivery": False,
                "wake_leader": False,
                "retry_required": False,
            }
        if _gate_matches(root, "open"):
            outcome = "already_open"
            updates: dict[str, Any] = {}
        else:
            outcome = "gate_opened"
            updates = {
                "final_approval_gate_state": "open",
                "final_approval_gate_revision": root["plan_revision"],
                "final_approval_gate_reviewed_commit_sha": root["reviewed_commit_sha"],
                "final_approval_gate_policy_digest": root["delivery_policy_digest"],
            }
        capacity_reasons = _metadata_capacity_reasons(root, updates)
        if capacity_reasons:
            return _rejected(action, capacity_reasons)
        return {
            "allowed": True,
            "action": action,
            "outcome": outcome,
            "reasons": [],
            "metadata_updates": updates,
            "status_write": "in_review" if root.get("status") != "in_review" else None,
            "merge_required": False,
            "resume_delivery": False,
            "wake_leader": False,
            "retry_required": False,
        }

    if action == "approve":
        event = data.get("event")
        if not isinstance(event, dict):
            reasons.append("approval event is missing")
            event = {}
        _add(reasons, actor_role == "integrator", "only Integrator records final approval")
        _add(reasons, root.get("status") == "in_review", "Requirement must be in_review")
        _add(
            reasons,
            event.get("issue_id") == root.get("issue_id"),
            "approval comment must be posted on the top-level Requirement",
        )
        _add(reasons, event.get("author_type") == "member", "approval author_type must be member")
        _add(
            reasons,
            event.get("author_id") == root.get("human_approver_id"),
            "approval author does not match human_approver_id",
        )
        _add(
            reasons,
            event.get("revision") == root.get("plan_revision"),
            "approval revision is stale",
        )
        _add(
            reasons,
            event.get("command")
            == f"APPROVE REQUIREMENT v{root.get('plan_revision')}",
            "approval command does not match the current Requirement revision",
        )
        _add(
            reasons,
            isinstance(event.get("comment_id"), str) and bool(event.get("comment_id")),
            "approval comment_id is missing",
        )
        if reasons:
            return _rejected(action, reasons)
        if _gate_matches(root, "accepted") and _approval_matches(root):
            delivery_ready = not _recorded_delivery_reasons(
                root, data.get("delivery")
            )
            return {
                "allowed": True,
                "action": action,
                "outcome": "duplicate_approval",
                "reasons": [],
                "metadata_updates": {},
                "status_write": None,
                "merge_required": False,
                "resume_delivery": not delivery_ready,
                "wake_leader": delivery_ready,
                "retry_required": False,
            }
        if not _gate_matches(root, "open"):
            return _rejected(action, ["the current final approval gate is not open"])
        updates = {
            "approval_author_type": "member",
            "approval_author_id": event["author_id"],
            "approval_comment_id": event["comment_id"],
            "approval_revision": root["plan_revision"],
            "approved_requirement_head_sha": root["reviewed_commit_sha"],
            "approved_delivery_policy_digest": root["delivery_policy_digest"],
            "final_approval_gate_state": "accepted",
        }
        capacity_reasons = _metadata_capacity_reasons(root, updates)
        if capacity_reasons:
            return _rejected(action, capacity_reasons)
        return {
            "allowed": True,
            "action": action,
            "outcome": "approval_accepted",
            "reasons": [],
            "metadata_updates": updates,
            "status_write": None,
            "merge_required": True,
            "resume_delivery": False,
            "wake_leader": False,
            "retry_required": False,
        }

    if action == "delivery":
        _add(reasons, actor_role == "integrator", "only Integrator records delivery")
        _add(reasons, root.get("status") == "in_review", "Requirement must remain in_review during delivery")
        _add(reasons, _gate_matches(root, "accepted"), "final approval gate is not accepted")
        _add(reasons, _approval_matches(root), "current final approval evidence is invalid")
        reasons.extend(_delivery_reasons(root, data.get("delivery")))
        if reasons:
            return _rejected(action, reasons)
        delivery = data["delivery"]
        updates = {
            "delivery_evidence_record": encode_metadata_record(
                _delivery_record(delivery)
            )
        }
        capacity_reasons = _metadata_capacity_reasons(root, updates)
        if capacity_reasons:
            return _rejected(action, capacity_reasons)
        return {
            "allowed": True,
            "action": action,
            "outcome": "delivery_complete",
            "reasons": [],
            "metadata_updates": updates,
            "status_write": None,
            "merge_required": False,
            "resume_delivery": False,
            "wake_leader": True,
            "retry_required": False,
        }

    if action == "handoff":
        handoff = data.get("handoff")
        if not isinstance(handoff, dict):
            reasons.append("delivery handoff evidence is missing")
            handoff = {}
        _add(reasons, actor_role == "integrator", "only Integrator records delivery handoff")
        _add(reasons, root.get("status") == "in_review", "Requirement must remain in_review before Leader convergence")
        _add(reasons, _gate_matches(root, "accepted"), "final approval gate is not accepted")
        _add(reasons, _approval_matches(root), "current final approval evidence is invalid")
        reasons.extend(_recorded_delivery_reasons(root, data.get("delivery")))
        _add(
            reasons,
            handoff.get("issue_id") == root.get("issue_id"),
            "delivery completion comment must be posted on the top-level Requirement",
        )
        role = handoff.get("mentioned_role")
        _add(reasons, role in {"leader", "squad"}, "delivery handoff must mention Leader or Squad")
        _add(
            reasons,
            isinstance(handoff.get("comment_id"), str) and bool(handoff.get("comment_id")),
            "delivery handoff comment_id is missing",
        )
        outcomes = handoff.get("trigger_outcomes")
        matched_outcome: str | None = None
        if isinstance(outcomes, list):
            acceptable_roles = {"leader"} if role == "leader" else {"leader", "squad"}
            for item in outcomes:
                if not isinstance(item, dict):
                    continue
                outcome = item.get("status") or item.get("outcome")
                recipient = item.get("recipient_role") or item.get("role")
                if recipient in acceptable_roles and outcome in HANDOFF_OUTCOMES:
                    matched_outcome = outcome
                    break
        _add(
            reasons,
            matched_outcome is not None,
            "trigger_outcomes did not confirm queued, coalesced, or deferred Leader delivery",
        )
        if reasons:
            return _rejected(action, reasons, retry_required=True)
        updates = {
            "delivery_handoff_record": encode_metadata_record(
                _handoff_record(root, handoff, role, matched_outcome)
            )
        }
        capacity_reasons = _metadata_capacity_reasons(root, updates)
        if capacity_reasons:
            return _rejected(action, capacity_reasons)
        return {
            "allowed": True,
            "action": action,
            "outcome": "leader_handoff_confirmed",
            "reasons": [],
            "metadata_updates": updates,
            "status_write": None,
            "merge_required": False,
            "resume_delivery": False,
            "wake_leader": True,
            "retry_required": False,
        }

    _add(reasons, actor_role == "leader", "only Leader may complete the Requirement")
    _add(reasons, root.get("status") == "in_review", "Requirement must be in_review before completion")
    _add(reasons, _gate_matches(root, "accepted"), "final approval gate is not accepted")
    _add(reasons, _approval_matches(root), "current final approval evidence is invalid")
    reasons.extend(_recorded_delivery_reasons(root, data.get("delivery")))
    reasons.extend(_recorded_handoff_reasons(root))
    reasons.extend(_metadata_capacity_reasons(root, ["final_approval_gate_state"]))
    if reasons:
        return _rejected(action, reasons)
    return {
        "allowed": True,
        "action": action,
        "outcome": "requirement_done",
        "reasons": [],
        "metadata_updates": {"final_approval_gate_state": "closed"},
        "status_write": "done",
        "merge_required": False,
        "resume_delivery": False,
        "wake_leader": False,
        "retry_required": False,
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


def load_recovery_record(path: str | None) -> Any:
    if path is None:
        return None
    content = Path(path).read_text(encoding="utf-8").strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        return content


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Resolve delivery policy and validate protocol-v4 final delivery"
    )
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
    verify.add_argument("--recovery-record")

    guard = subparsers.add_parser("guard-workspace")
    guard.add_argument("--repo", default=".")
    guard.add_argument("--workspace-mode", required=True, choices=WORKSPACE_MODES)
    guard.add_argument("--expected-branch")
    guard.add_argument("--expected-head")

    final_gate = subparsers.add_parser("final-gate")
    final_gate.add_argument("--action", required=True, choices=FINAL_ACTIONS)
    final_gate.add_argument("--snapshot", required=True)
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
            result = verify_snapshot(
                Path(args.repo),
                snapshot,
                args.config,
                load_recovery_record(args.recovery_record),
            )
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
        if args.command == "final-gate":
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            result = final_gate_transition(snapshot, args.action)
            print_json(result)
            return 0 if result["allowed"] else 1
    except (DeliveryPolicyError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
