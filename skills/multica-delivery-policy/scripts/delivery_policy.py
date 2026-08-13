from __future__ import annotations

import argparse
import base64
import binascii
from datetime import datetime
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
TERMINAL_NORMALIZATION_ACTIONS = ("transition", "attest")
DELIVERY_MODES = ("requirement_pr", "direct_push", "local_only")
HANDOFF_OUTCOMES = ("queued", "coalesced", "deferred")
LEASE_STATES = ("held", "released")
TERMINAL_NORMALIZATION_RECORD_KEY = "workspace_lease_terminal_normalization_record"
TERMINAL_NORMALIZATION_ENDPOINT_KEYS = (
    "workspace_lease_state",
    "workspace_lease_owner_issue_id",
    "workspace_lease_owner_agent_id",
    TERMINAL_NORMALIZATION_RECORD_KEY,
)
SUPERSEDED_TASK_RELEASE_RECORD_KEY = "workspace_lease_superseded_release_record"
SUPERSEDED_TASK_RELEASE_KEYS = (
    "workspace_lease_state",
    "workspace_lease_owner_issue_id",
    "workspace_lease_owner_agent_id",
    SUPERSEDED_TASK_RELEASE_RECORD_KEY,
)
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


def _parse_timestamp(value: Any, name: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise DeliveryPolicyError(f"{name} is missing")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DeliveryPolicyError(f"{name} is invalid") from exc
    if parsed.tzinfo is None:
        raise DeliveryPolicyError(f"{name} must include a timezone")
    return parsed


def _integration_review_binding_digest(record: dict[str, Any]) -> str:
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
    return digest({key: record.get(key) for key in keys})


def _integration_review_epoch_id(record: dict[str, Any]) -> str:
    return digest(
        {
            "review_binding_digest": record.get("review_binding_digest"),
            "handoff_comment_id": record.get("handoff_comment_id"),
            "trigger_run_id": record.get("trigger_run_id"),
            "trigger_outcome": record.get("trigger_outcome"),
            "handoff_created_at": record.get("handoff_created_at"),
        }
    )


def _active_integration_roster(
    root: dict[str, Any]
) -> tuple[str, str, str, list[str]]:
    reasons: list[str] = []
    roster = root.get("integration_roster")
    if not isinstance(roster, list):
        raise DeliveryPolicyError("integration roster must be a list")
    if root.get("integration_roster_complete") is not True:
        raise DeliveryPolicyError("integration roster must be declared complete")
    normalized = []
    integrators = []
    reviewers = []
    for item in roster:
        if not isinstance(item, dict):
            raise DeliveryPolicyError("integration roster contains an invalid member")
        member = {
            "agent_id": item.get("agent_id"),
            "member_type": item.get("member_type"),
            "role_key": item.get("role_key"),
            "active": item.get("active") is True,
            "archived": item.get("archived") is True,
        }
        if not isinstance(member["agent_id"], str) or not member["agent_id"]:
            raise DeliveryPolicyError("integration roster member agent_id is missing")
        normalized.append(member)
        if member["member_type"] == "agent" and member["active"] and not member["archived"]:
            if member["role_key"] == "integrator":
                integrators.append(member["agent_id"])
            if member["role_key"] == "code_reviewer":
                reviewers.append(member["agent_id"])
    if len(integrators) != 1:
        reasons.append(
            f"expected one active Integrator in the current roster, found {len(integrators)}"
        )
    if len(reviewers) != 1:
        reasons.append(
            f"expected one active Code Reviewer in the current roster, found {len(reviewers)}"
        )
    owner_id = integrators[0] if len(integrators) == 1 else ""
    reviewer_id = reviewers[0] if len(reviewers) == 1 else ""
    if owner_id and reviewer_id and owner_id == reviewer_id:
        reasons.append("integration owner and reviewer must be independent")
    normalized.sort(key=canonical_json)
    roster_digest = digest(
        {
            "workspace_id": root.get("workflow_instance_id"),
            "squad_id": root.get("integration_squad_id"),
            "roster": normalized,
        }
    )
    return owner_id, reviewer_id, roster_digest, reasons


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
    reasons.extend(_integration_review_reasons(root))
    return reasons


def _integration_review_reasons(root: dict[str, Any]) -> list[str]:
    reasons: list[str] = []
    try:
        roster_owner_id, roster_reviewer_id, roster_digest, roster_reasons = (
            _active_integration_roster(root)
        )
    except DeliveryPolicyError as exc:
        return [str(exc)]
    reasons.extend(roster_reasons)
    try:
        record = decode_metadata_record(
            root.get("integration_review_role_record"),
            "integration_review_role_record",
        )
    except DeliveryPolicyError as exc:
        return [str(exc)]
    _add(
        reasons,
        record.get("record_type") == "integration_review_role",
        "integration review role record type is invalid",
    )
    _add(
        reasons,
        record.get("state") == "approved",
        "integration review role record is not approved",
    )
    _add(
        reasons,
        record.get("workspace_id") == root.get("workflow_instance_id"),
        "integration review workspace binding drifted",
    )
    _add(
        reasons,
        record.get("squad_id") == root.get("integration_squad_id"),
        "integration review Squad binding drifted",
    )
    _add(
        reasons,
        root.get("integration_roster_digest") == roster_digest
        and record.get("roster_digest") == roster_digest,
        "integration review roster drifted",
    )
    owner_id = root.get("integration_original_owner_id")
    reviewer_id = root.get("integration_reviewer_id")
    _add(
        reasons,
        isinstance(owner_id, str) and bool(owner_id),
        "integration original owner is missing",
    )
    _add(
        reasons,
        isinstance(reviewer_id, str) and bool(reviewer_id),
        "integration reviewer is missing",
    )
    _add(reasons, owner_id != reviewer_id, "integration owner and reviewer must be independent")
    _add(
        reasons,
        owner_id == roster_owner_id,
        "integration original owner is not the current unique Integrator",
    )
    _add(
        reasons,
        reviewer_id == roster_reviewer_id,
        "integration reviewer is not the current unique Code Reviewer",
    )
    _add(
        reasons,
        root.get("integration_validation_assignee_id") == owner_id,
        "integration validation assignee no longer matches the original owner",
    )
    _add(reasons, record.get("owner_id") == owner_id, "recorded integration owner is stale")
    _add(
        reasons,
        record.get("reviewer_id") == reviewer_id,
        "recorded integration reviewer is stale",
    )
    _add(
        reasons,
        record.get("issue_id") == root.get("integration_validation_issue_id"),
        "integration review record targets a different validation Issue",
    )
    _add(
        reasons,
        record.get("plan_revision") == root.get("plan_revision"),
        "integration review Plan revision is stale",
    )
    _add(
        reasons,
        record.get("delivery_policy_digest") == root.get("delivery_policy_digest"),
        "integration review policy digest is stale",
    )
    _add(
        reasons,
        record.get("base_commit_sha") == root.get("integration_base_commit_sha"),
        "integration review base commit is stale",
    )
    _add(
        reasons,
        record.get("reviewed_commit_sha") == root.get("reviewed_commit_sha"),
        "integration review head is stale",
    )
    _add(
        reasons,
        record.get("dependency_digest") == root.get("integration_dependency_digest"),
        "integration review dependency evidence is stale",
    )
    _add(
        reasons,
        _is_digest(record.get("dependency_digest")),
        "integration review dependency digest is invalid",
    )
    _add(
        reasons,
        record.get("lease_digest") == root.get("integration_lease_digest"),
        "integration review lease evidence is stale",
    )
    _add(
        reasons,
        _is_digest(record.get("lease_digest")),
        "integration review lease digest is invalid",
    )
    for key, label in (
        ("review_comment_id", "Review comment"),
        ("handoff_comment_id", "Review handoff comment"),
        ("trigger_run_id", "Review trigger run"),
    ):
        _add(
            reasons,
            isinstance(record.get(key), str) and bool(record.get(key)),
            f"integration {label} ID is missing",
        )
    _add(
        reasons,
        record.get("review_comment_id") == root.get("integration_review_comment_id"),
        "integration Review comment is not current",
    )
    _add(
        reasons,
        record.get("review_author_id") == reviewer_id
        and root.get("integration_review_comment_author_id") == reviewer_id,
        "integration Review author is not the current Code Reviewer",
    )
    _add(
        reasons,
        record.get("review_epoch_id") == root.get("integration_review_epoch_id"),
        "integration Review epoch is stale",
    )
    _add(
        reasons,
        record.get("handoff_comment_id")
        == root.get("integration_review_handoff_comment_id"),
        "integration Review is not bound to the current handoff comment",
    )
    _add(
        reasons,
        record.get("trigger_run_id") == root.get("integration_review_trigger_run_id"),
        "integration Review is not bound to the current trigger run",
    )
    _add(
        reasons,
        record.get("trigger_outcome") in HANDOFF_OUTCOMES,
        "integration Review handoff trigger was not confirmed",
    )
    _add(
        reasons,
        record.get("review_binding_digest")
        == _integration_review_binding_digest(record),
        "integration Review binding digest is invalid",
    )
    _add(
        reasons,
        record.get("review_epoch_id") == _integration_review_epoch_id(record),
        "integration Review epoch digest is invalid",
    )
    try:
        handoff_time = _parse_timestamp(
            record.get("handoff_created_at"), "integration Review handoff_created_at"
        )
        review_time = _parse_timestamp(
            record.get("review_created_at"), "integration Review review_created_at"
        )
        _add(
            reasons,
            review_time > handoff_time,
            "integration Review comment predates or coincides with its handoff",
        )
    except DeliveryPolicyError as exc:
        reasons.append(str(exc))
    recovery_digest = record.get("recovery_record_digest")
    recovery_value = root.get("integration_review_recovery_record")
    if recovery_digest is None:
        _add(
            reasons,
            recovery_value in {None, ""},
            "unexpected integration recovery evidence is present",
        )
    else:
        current_digest = (
            hashlib.sha256(recovery_value.encode("utf-8")).hexdigest()
            if isinstance(recovery_value, str)
            else None
        )
        _add(
            reasons,
            recovery_digest == current_digest,
            "integration recovery record is stale",
        )
        try:
            recovery = decode_metadata_record(
                recovery_value, "integration_review_recovery_record"
            )
        except DeliveryPolicyError as exc:
            reasons.append(str(exc))
        else:
            _add(
                reasons,
                recovery.get("record_type") == "integration_review_recovery",
                "integration recovery record type is invalid",
            )
            for key in (
                "incident_id",
                "recovery_id",
                "from_assignee_id",
                "from_original_owner_id",
                "from_reviewer_id",
            ):
                _add(
                    reasons,
                    isinstance(recovery.get(key), str) and bool(recovery.get(key)),
                    f"integration recovery {key} is missing",
                )
            for key in ("lease_transition_digest", "blocker_digest"):
                _add(
                    reasons,
                    _is_digest(recovery.get(key)),
                    f"integration recovery {key} is invalid",
                )
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
                _add(
                    reasons,
                    recovery.get(key) == record.get(key),
                    f"integration recovery {key} binding is stale",
                )
    return reasons


def _metadata_capacity_reasons(
    root: dict[str, Any], update_keys: Any
) -> list[str]:
    metadata_keys = root.get("metadata_keys")
    if (
        not isinstance(metadata_keys, list)
        or not all(isinstance(key, str) and bool(key) for key in metadata_keys)
        or len(metadata_keys) != len(set(metadata_keys))
    ):
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


LEASE_ENDPOINT_FIELDS = (
    "workspace_lease_state",
    "workspace_lease_owner_issue_id",
    "workspace_lease_owner_agent_id",
    "workspace_lease_transition_record",
)
LEASE_BLOCKER_FIELDS = (
    "status",
    "waiting_on",
    "blocked_reason",
    "workflow_blocked_by_incident_id",
    "workflow_blocked_previous_status",
)


def _lease_endpoint_projection(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {key: endpoint.get(key, "") for key in LEASE_ENDPOINT_FIELDS}


def _lease_projection(
    authority: dict[str, Any], mirror: dict[str, Any]
) -> dict[str, Any]:
    result = {}
    for prefix, endpoint in (("authority", authority), ("mirror", mirror)):
        for key, value in _lease_endpoint_projection(endpoint).items():
            result[f"{prefix}.{key}"] = value
    for key in LEASE_BLOCKER_FIELDS:
        result[f"mirror.{key}"] = mirror.get(key, "")
    return result


def _lease_apply_write(
    projection: dict[str, Any], write: dict[str, Any]
) -> dict[str, Any]:
    result = dict(projection)
    result[f"{write['endpoint']}.{write['key']}"] = write.get("value", "")
    return result


def _lease_record_value(plan: dict[str, Any], completed: int) -> str:
    return encode_metadata_record({**plan, "completed": completed})


def _lease_data_writes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    desired = plan["desired"]
    direction = plan["direction"]
    if direction == "release":
        endpoint_order = ("mirror", "authority")
        field_order = (
            "workspace_lease_owner_agent_id",
            "workspace_lease_owner_issue_id",
            "workspace_lease_state",
        )
    else:
        endpoint_order = ("authority", "mirror")
        field_order = (
            "workspace_lease_owner_issue_id",
            "workspace_lease_owner_agent_id",
            "workspace_lease_state",
        )
    return [
        {
            "kind": "metadata",
            "endpoint": endpoint,
            "issue_id": plan[f"{endpoint}_issue_id"],
            "key": key,
            "value": desired[key],
        }
        for endpoint in endpoint_order
        for key in field_order
    ]


def _lease_full_writes(plan: dict[str, Any]) -> list[dict[str, Any]]:
    writes = [
        {
            "kind": "metadata",
            "endpoint": "authority",
            "issue_id": plan["authority_issue_id"],
            "key": "workspace_lease_transition_record",
            "value": _lease_record_value(plan, 0),
        },
        {
            "kind": "metadata",
            "endpoint": "mirror",
            "issue_id": plan["mirror_issue_id"],
            "key": "workspace_lease_transition_record",
            "value": _lease_record_value(plan, 0),
        },
    ]
    for completed, write in enumerate(_lease_data_writes(plan), start=1):
        writes.append(write)
        for endpoint in ("authority", "mirror"):
            writes.append(
                {
                    "kind": "metadata",
                    "endpoint": endpoint,
                    "issue_id": plan[f"{endpoint}_issue_id"],
                    "key": "workspace_lease_transition_record",
                    "value": _lease_record_value(plan, completed),
                }
            )
    return writes


def _lease_plan_from_record(value: Any) -> tuple[dict[str, Any], int]:
    record = decode_metadata_record(value, "workspace_lease_transition_record")
    if record.get("record_type") != "workspace_lease_transition":
        raise DeliveryPolicyError("workspace_lease_transition_record has the wrong record type")
    completed = record.get("completed")
    if not isinstance(completed, int) or isinstance(completed, bool) or completed < 0:
        raise DeliveryPolicyError("workspace_lease_transition_record completed is invalid")
    plan = {key: item for key, item in record.items() if key != "completed"}
    _validate_lease_plan(plan)
    if completed > len(_lease_data_writes(plan)):
        raise DeliveryPolicyError("workspace_lease_transition_record completed is out of range")
    return plan, completed


def _validate_lease_plan(plan: dict[str, Any]) -> None:
    if plan.get("schema_version") != 1 or plan.get("record_type") != "workspace_lease_transition":
        raise DeliveryPolicyError("workspace lease transition record schema is invalid")
    for key in (
        "workspace_id",
        "squad_id",
        "roster_digest",
        "authority_issue_id",
        "mirror_issue_id",
        "guard_digest",
    ):
        if not isinstance(plan.get(key), str) or not plan.get(key):
            raise DeliveryPolicyError(f"workspace lease transition {key} is missing")
    if not _is_digest(plan.get("roster_digest")):
        raise DeliveryPolicyError("workspace lease transition roster_digest is invalid")
    if not _is_digest(plan.get("delivery_policy_digest")):
        raise DeliveryPolicyError("workspace lease transition policy digest is invalid")
    if not _is_digest(plan.get("guard_digest")):
        raise DeliveryPolicyError("workspace lease transition guard digest is invalid")
    revision = plan.get("plan_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise DeliveryPolicyError("workspace lease transition Plan revision is invalid")
    if plan.get("direction") not in {"release", "acquire"}:
        raise DeliveryPolicyError("workspace lease transition direction is invalid")
    if plan.get("authority_issue_id") == plan.get("mirror_issue_id"):
        raise DeliveryPolicyError("workspace lease authority and mirror must be different Issues")
    initial_authority = _require_object(
        plan.get("initial_authority"), "workspace lease initial authority"
    )
    initial_mirror = _require_object(
        plan.get("initial_mirror"), "workspace lease initial mirror"
    )
    desired = _require_object(plan.get("desired"), "workspace lease desired tuple")
    for endpoint in (initial_authority, initial_mirror, desired):
        if endpoint.get("workspace_lease_state") not in LEASE_STATES:
            raise DeliveryPolicyError("workspace lease state is invalid")
    if initial_authority.get("workspace_lease_transition_record") not in {None, ""}:
        raise DeliveryPolicyError("workspace lease initial authority record must be empty")
    if initial_mirror.get("workspace_lease_transition_record") not in {None, ""}:
        raise DeliveryPolicyError("workspace lease initial mirror record must be empty")
    tuple_keys = (
        "workspace_lease_state",
        "workspace_lease_owner_issue_id",
        "workspace_lease_owner_agent_id",
    )
    authority_tuple = {key: initial_authority.get(key, "") for key in tuple_keys}
    mirror_tuple = {key: initial_mirror.get(key, "") for key in tuple_keys}
    desired_tuple = {key: desired.get(key, "") for key in tuple_keys}
    if authority_tuple != mirror_tuple:
        raise DeliveryPolicyError("workspace lease initial authority and mirror differ")
    if plan["direction"] == "release":
        if (
            authority_tuple["workspace_lease_state"] != "held"
            or not authority_tuple["workspace_lease_owner_issue_id"]
            or not authority_tuple["workspace_lease_owner_agent_id"]
            or desired_tuple
            != {
                "workspace_lease_state": "released",
                "workspace_lease_owner_issue_id": "",
                "workspace_lease_owner_agent_id": "",
            }
        ):
            raise DeliveryPolicyError("workspace lease release tuple is invalid")
    else:
        if (
            authority_tuple
            != {
                "workspace_lease_state": "released",
                "workspace_lease_owner_issue_id": "",
                "workspace_lease_owner_agent_id": "",
            }
            or desired_tuple["workspace_lease_state"] != "held"
            or not desired_tuple["workspace_lease_owner_issue_id"]
            or not desired_tuple["workspace_lease_owner_agent_id"]
        ):
            raise DeliveryPolicyError("workspace lease acquire tuple is invalid")
    blocker = _require_object(plan.get("mirror_blocker"), "workspace lease mirror blocker")
    if digest(blocker) != plan.get("mirror_blocker_digest"):
        raise DeliveryPolicyError("workspace lease mirror blocker digest is invalid")
    other_leases = plan.get("other_leases")
    if not isinstance(other_leases, list):
        raise DeliveryPolicyError("workspace lease other lease inventory is invalid")
    if digest(other_leases) != plan.get("other_leases_digest"):
        raise DeliveryPolicyError("workspace lease other lease inventory digest is invalid")
    if plan["direction"] == "release" and other_leases:
        raise DeliveryPolicyError("workspace lease release record must not bind other leases")
    if plan["direction"] == "acquire" and not _other_leases_released(other_leases):
        raise DeliveryPolicyError("workspace lease acquire inventory is not fully released")


def _released_endpoint(endpoint: Any) -> bool:
    return (
        isinstance(endpoint, dict)
        and endpoint.get("workspace_lease_state") == "released"
        and endpoint.get("workspace_lease_owner_issue_id") in {None, ""}
        and endpoint.get("workspace_lease_owner_agent_id") in {None, ""}
    )


def _normalize_other_leases(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("other_leases")
    if raw is None and data.get("other_lease") is not None:
        raw = [data.get("other_lease")]
    if raw is None:
        raw = []
    if not isinstance(raw, list):
        raise DeliveryPolicyError("other Requirement lease inventory must be a list")
    normalized = []
    requirement_ids = set()
    endpoint_ids = set()
    for item in raw:
        lease = _require_object(item, "other Requirement lease")
        requirement_id = lease.get("requirement_id")
        if not isinstance(requirement_id, str) or not requirement_id:
            raise DeliveryPolicyError("other Requirement lease requirement_id is missing")
        if requirement_id in requirement_ids:
            raise DeliveryPolicyError("other Requirement lease inventory has duplicates")
        requirement_ids.add(requirement_id)
        normalized_item = {"requirement_id": requirement_id}
        for endpoint_name in ("authority", "mirror"):
            endpoint = _require_object(
                lease.get(endpoint_name), f"other Requirement {endpoint_name} lease"
            )
            issue_id = endpoint.get("issue_id")
            if not isinstance(issue_id, str) or not issue_id:
                raise DeliveryPolicyError(
                    f"other Requirement {endpoint_name} lease issue_id is missing"
                )
            if issue_id in endpoint_ids:
                raise DeliveryPolicyError(
                    "other Requirement lease inventory reuses an endpoint"
                )
            endpoint_ids.add(issue_id)
            normalized_item[endpoint_name] = {
                "issue_id": issue_id,
                "workspace_lease_state": endpoint.get("workspace_lease_state"),
                "workspace_lease_owner_issue_id": endpoint.get(
                    "workspace_lease_owner_issue_id", ""
                ),
                "workspace_lease_owner_agent_id": endpoint.get(
                    "workspace_lease_owner_agent_id", ""
                ),
            }
        normalized.append(normalized_item)
    normalized.sort(key=canonical_json)
    return normalized


def _other_leases_released(other_leases: list[dict[str, Any]]) -> bool:
    return all(
        _released_endpoint(item.get("authority"))
        and _released_endpoint(item.get("mirror"))
        for item in other_leases
    )


def lease_transition_preflight(snapshot: Any) -> dict[str, Any]:
    data = _require_object(snapshot, "lease transition snapshot")
    context = _require_object(data.get("context"), "lease transition context")
    plan_binding = _require_object(data.get("plan"), "lease transition Plan binding")
    guard = _require_object(data.get("guard"), "lease transition guard")
    authority = _require_object(data.get("authority"), "lease authority")
    mirror = _require_object(data.get("mirror"), "lease mirror")
    reasons: list[str] = []
    for key in ("workspace_id", "squad_id", "roster_digest"):
        _add(
            reasons,
            isinstance(context.get(key), str) and bool(context.get(key)),
            f"lease context {key} is missing",
        )
    _add(
        reasons,
        isinstance(plan_binding.get("plan_revision"), int)
        and not isinstance(plan_binding.get("plan_revision"), bool)
        and plan_binding.get("plan_revision") > 0,
        "lease plan_revision must be a positive integer",
    )
    _add(
        reasons,
        _is_digest(plan_binding.get("delivery_policy_digest")),
        "lease delivery_policy_digest is invalid",
    )
    _add(reasons, guard.get("valid") is True, "workspace guard is not valid")
    _add(reasons, guard.get("clean") is True, "workspace guard is not clean")
    _add(
        reasons,
        guard.get("branch") == guard.get("expected_branch")
        and isinstance(guard.get("branch"), str)
        and bool(guard.get("branch")),
        "workspace guard branch drifted",
    )
    _add(
        reasons,
        guard.get("head") == guard.get("expected_head")
        and _is_sha(guard.get("head")),
        "workspace guard head drifted",
    )
    _add(
        reasons,
        guard.get("unfinished_operations") in (None, []),
        "workspace has an unfinished Git operation",
    )
    for label, endpoint in (("authority", authority), ("mirror", mirror)):
        _add(
            reasons,
            isinstance(endpoint.get("issue_id"), str) and bool(endpoint.get("issue_id")),
            f"lease {label} issue_id is missing",
        )
        _add(
            reasons,
            endpoint.get("workspace_lease_scope") == "requirement",
            f"lease {label} scope must be requirement",
        )
    if reasons:
        return {
            "allowed": False,
            "outcome": "rejected",
            "reasons": reasons,
            "writes": [],
            "complete": False,
            "blocker_writes": [],
            "status_writes": [],
        }

    authority_record = authority.get("workspace_lease_transition_record")
    mirror_record = mirror.get("workspace_lease_transition_record")
    records = [
        value
        for value in (authority_record, mirror_record)
        if value not in {None, ""}
    ]
    superseded_complete_records = False
    if records:
        decoded_records = [_lease_plan_from_record(value) for value in records]
        plan = decoded_records[0][0]
        if any(item[0] != plan for item in decoded_records[1:]):
            raise DeliveryPolicyError("authority and mirror lease transition records conflict")
        all_terminal = (
            len(records) == 2
            and all(
                completed == len(_lease_data_writes(decoded_plan))
                for decoded_plan, completed in decoded_records
            )
        )
        requested_desired = data.get("desired")
        normalized_desired = None
        if requested_desired is not None:
            desired = _require_object(requested_desired, "desired lease tuple")
            normalized_desired = {
                "workspace_lease_state": desired.get("workspace_lease_state"),
                "workspace_lease_owner_issue_id": desired.get(
                    "workspace_lease_owner_issue_id", ""
                ),
                "workspace_lease_owner_agent_id": desired.get(
                    "workspace_lease_owner_agent_id", ""
                ),
            }
            if not all_terminal and normalized_desired != plan.get("desired"):
                raise DeliveryPolicyError("desired lease tuple conflicts with the transition record")
        current_authority_tuple = {
            key: authority.get(key, "")
            for key in (
                "workspace_lease_state",
                "workspace_lease_owner_issue_id",
                "workspace_lease_owner_agent_id",
            )
        }
        current_mirror_tuple = {
            key: mirror.get(key, "")
            for key in (
                "workspace_lease_state",
                "workspace_lease_owner_issue_id",
                "workspace_lease_owner_agent_id",
            )
        }
        if (
            all_terminal
            and current_authority_tuple == plan.get("desired")
            and current_mirror_tuple == plan.get("desired")
            and (
                normalized_desired is None
                or normalized_desired == plan.get("desired")
            )
        ):
            return {
                "allowed": True,
                "outcome": "complete",
                "reasons": [],
                "record": authority_record,
                "progress": len(_lease_full_writes(plan)),
                "total_writes": len(_lease_full_writes(plan)),
                "writes": [],
                "complete": True,
                "direction": plan["direction"],
                "blocker_writes": [],
                "status_writes": [],
                "next_requirement_acquire_allowed": plan["direction"] == "release",
            }
        if all_terminal:
            superseded_complete_records = True
    if not records or superseded_complete_records:
        desired = _require_object(data.get("desired"), "desired lease tuple")
        initial_authority = _lease_endpoint_projection(authority)
        initial_mirror = _lease_endpoint_projection(mirror)
        initial_authority["workspace_lease_transition_record"] = ""
        initial_mirror["workspace_lease_transition_record"] = ""
        current_tuple = {
            key: initial_authority[key]
            for key in (
                "workspace_lease_state",
                "workspace_lease_owner_issue_id",
                "workspace_lease_owner_agent_id",
            )
        }
        mirror_tuple = {
            key: initial_mirror[key]
            for key in (
                "workspace_lease_state",
                "workspace_lease_owner_issue_id",
                "workspace_lease_owner_agent_id",
            )
        }
        if current_tuple != mirror_tuple:
            raise DeliveryPolicyError("lease authority and mirror are inconsistent before transition")
        desired_tuple = {
            "workspace_lease_state": desired.get("workspace_lease_state"),
            "workspace_lease_owner_issue_id": desired.get(
                "workspace_lease_owner_issue_id", ""
            ),
            "workspace_lease_owner_agent_id": desired.get(
                "workspace_lease_owner_agent_id", ""
            ),
        }
        if current_tuple["workspace_lease_state"] == "held" and desired_tuple["workspace_lease_state"] == "released":
            direction = "release"
        elif current_tuple["workspace_lease_state"] == "released" and desired_tuple["workspace_lease_state"] == "held":
            direction = "acquire"
        else:
            raise DeliveryPolicyError("lease transition must be held-to-released or released-to-held")
        if direction == "release":
            if (
                desired_tuple["workspace_lease_owner_issue_id"] not in {None, ""}
                or desired_tuple["workspace_lease_owner_agent_id"] not in {None, ""}
            ):
                raise DeliveryPolicyError("released lease tuple must clear both owner IDs")
        else:
            if (
                not isinstance(desired_tuple["workspace_lease_owner_issue_id"], str)
                or not desired_tuple["workspace_lease_owner_issue_id"]
                or not isinstance(desired_tuple["workspace_lease_owner_agent_id"], str)
                or not desired_tuple["workspace_lease_owner_agent_id"]
            ):
                raise DeliveryPolicyError("held lease tuple requires both owner IDs")
            if context.get("lease_inventory_complete") is not True:
                raise DeliveryPolicyError(
                    "acquisition requires a complete other Requirement lease inventory"
                )
            other_leases = _normalize_other_leases(data)
            if not _other_leases_released(other_leases):
                raise DeliveryPolicyError(
                    "another Requirement is not fully released; acquisition would create double ownership"
                )
        if direction == "release":
            other_leases = []
        blocker = {key: mirror.get(key, "") for key in LEASE_BLOCKER_FIELDS}
        plan = {
            "schema_version": 1,
            "record_type": "workspace_lease_transition",
            "workspace_id": context["workspace_id"],
            "squad_id": context["squad_id"],
            "roster_digest": context["roster_digest"],
            "plan_revision": plan_binding["plan_revision"],
            "delivery_policy_digest": plan_binding["delivery_policy_digest"],
            "guard_digest": digest(guard),
            "authority_issue_id": authority["issue_id"],
            "mirror_issue_id": mirror["issue_id"],
            "direction": direction,
            "initial_authority": initial_authority,
            "initial_mirror": initial_mirror,
            "desired": desired_tuple,
            "mirror_blocker": blocker,
            "mirror_blocker_digest": digest(blocker),
            "other_leases": other_leases,
            "other_leases_digest": digest(other_leases),
        }

    _validate_lease_plan(plan)
    if plan.get("workspace_id") != context.get("workspace_id"):
        raise DeliveryPolicyError("lease transition workspace binding drifted")
    if plan.get("squad_id") != context.get("squad_id"):
        raise DeliveryPolicyError("lease transition Squad binding drifted")
    if plan.get("roster_digest") != context.get("roster_digest"):
        raise DeliveryPolicyError("lease transition roster binding drifted")
    if plan.get("plan_revision") != plan_binding.get("plan_revision"):
        raise DeliveryPolicyError("lease transition Plan revision drifted")
    if plan.get("delivery_policy_digest") != plan_binding.get("delivery_policy_digest"):
        raise DeliveryPolicyError("lease transition policy digest drifted")
    if plan["direction"] == "acquire" and context.get("lease_inventory_complete") is not True:
        raise DeliveryPolicyError(
            "acquisition requires a complete other Requirement lease inventory"
        )
    if plan.get("guard_digest") != digest(guard):
        raise DeliveryPolicyError("lease transition workspace guard drifted")
    if plan.get("authority_issue_id") != authority.get("issue_id") or plan.get(
        "mirror_issue_id"
    ) != mirror.get("issue_id"):
        raise DeliveryPolicyError("lease transition endpoint binding drifted")
    current_blocker = {key: mirror.get(key, "") for key in LEASE_BLOCKER_FIELDS}
    if digest(current_blocker) != plan.get("mirror_blocker_digest"):
        raise DeliveryPolicyError("lease transition must not change the mirror Incident blocker")
    current_other_leases = _normalize_other_leases(data) if plan["direction"] == "acquire" else []
    if current_other_leases != plan.get("other_leases"):
        raise DeliveryPolicyError("other Requirement lease inventory drifted")
    if plan["direction"] == "acquire" and not _other_leases_released(
        current_other_leases
    ):
        raise DeliveryPolicyError(
            "another Requirement acquired during lease transition"
        )
    current_endpoint_ids = {authority.get("issue_id"), mirror.get("issue_id")}
    if any(
        endpoint.get("issue_id") in current_endpoint_ids
        for item in current_other_leases
        for endpoint in (item["authority"], item["mirror"])
    ):
        raise DeliveryPolicyError(
            "other Requirement lease inventory includes the current endpoints"
        )

    update_keys = [*LEASE_ENDPOINT_FIELDS]
    capacity_reasons = []
    capacity_reasons.extend(
        _metadata_capacity_reasons(authority, update_keys)
    )
    capacity_reasons.extend(
        _metadata_capacity_reasons(mirror, update_keys)
    )
    if capacity_reasons:
        return {
            "allowed": False,
            "outcome": "rejected",
            "reasons": capacity_reasons,
            "writes": [],
            "complete": False,
            "blocker_writes": [],
            "status_writes": [],
        }

    full_writes = _lease_full_writes(plan)
    expected = {}
    for prefix, initial_key in (
        ("authority", "initial_authority"),
        ("mirror", "initial_mirror"),
    ):
        for key, value in plan[initial_key].items():
            expected[f"{prefix}.{key}"] = value
    for key, value in plan["mirror_blocker"].items():
        expected[f"mirror.{key}"] = value
    current = _lease_projection(authority, mirror)
    if superseded_complete_records:
        current["authority.workspace_lease_transition_record"] = ""
        current["mirror.workspace_lease_transition_record"] = ""
    matching_prefixes = []
    if expected == current:
        matching_prefixes.append(0)
    for index, write in enumerate(full_writes, start=1):
        expected = _lease_apply_write(expected, write)
        if expected == current:
            matching_prefixes.append(index)
    if not matching_prefixes:
        raise DeliveryPolicyError("lease transition state is not a valid retry prefix")
    progress = max(matching_prefixes)
    remaining = full_writes[progress:]
    return {
        "allowed": True,
        "outcome": "complete" if not remaining else "resume_required",
        "reasons": [],
        "record": _lease_record_value(plan, len(_lease_data_writes(plan))),
        "progress": progress,
        "total_writes": len(full_writes),
        "writes": remaining,
        "complete": not remaining,
        "direction": plan["direction"],
        "blocker_writes": [],
        "status_writes": [],
        "next_requirement_acquire_allowed": (
            plan["direction"] == "release" and not remaining
        ),
    }


def _terminal_manifest_identity(manifest: dict[str, Any]) -> str:
    return f"v2.sha256:{digest(manifest)}"


def _terminal_tuple(value: Any, name: str) -> dict[str, Any]:
    item = _require_object(value, name)
    normalized = {
        "status": item.get("status"),
        "scope": item.get("scope"),
        "state": item.get("state"),
        "owner_issue_id": item.get("owner_issue_id", ""),
        "owner_agent_id": item.get("owner_agent_id", ""),
    }
    if normalized["status"] != "done" or normalized["scope"] != "requirement":
        raise DeliveryPolicyError(f"{name} must bind a done requirement lease")
    if not isinstance(normalized["state"], str) or not normalized["state"]:
        raise DeliveryPolicyError(f"{name} state is missing")
    for key in ("owner_issue_id", "owner_agent_id"):
        if not isinstance(normalized[key], str):
            raise DeliveryPolicyError(f"{name} {key} is invalid")
    return normalized


def _validate_terminal_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("schema_version") != 2:
        raise DeliveryPolicyError("terminal normalization manifest schema is unsupported")
    if manifest.get("canonicalization") != (
        "json-recursive-key-sort-arrays-preserved-utf8-no-bom-no-trailing-newline"
    ):
        raise DeliveryPolicyError("terminal normalization canonicalization is invalid")
    plan = _require_object(manifest.get("plan"), "terminal normalization manifest Plan")
    if not isinstance(plan.get("issue_id"), str) or not plan.get("issue_id"):
        raise DeliveryPolicyError("terminal normalization manifest Plan issue is missing")
    revision = plan.get("plan_revision")
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise DeliveryPolicyError("terminal normalization manifest Plan revision is invalid")
    if not _is_digest(plan.get("delivery_policy_digest")):
        raise DeliveryPolicyError("terminal normalization manifest policy digest is invalid")
    if not isinstance(manifest.get("workflow_instance_id"), str) or not manifest.get(
        "workflow_instance_id"
    ):
        raise DeliveryPolicyError("terminal normalization workflow instance is missing")
    root_authority = _require_object(
        manifest.get("root_authority"), "terminal normalization root authority"
    )
    if (
        root_authority.get("workflow_object_type") != "requirement"
        or root_authority.get("parent_issue_id") is not None
        or plan.get("parent_issue_id") != root_authority.get("issue_id")
        or plan.get("root_requirement_id") != root_authority.get("issue_id")
    ):
        raise DeliveryPolicyError("terminal normalization approved root authority is invalid")
    immutable = _require_object(
        manifest.get("immutable_snapshot"), "terminal normalization immutable snapshot"
    )
    if (
        immutable.get("digest_algorithm") != "sha256"
        or immutable.get("entry_schema_version") != 1
        or not _is_digest(immutable.get("aggregate_digest"))
        or not isinstance(immutable.get("requirements"), list)
        or len(immutable["requirements"]) != 2
    ):
        raise DeliveryPolicyError("terminal normalization immutable snapshot authority is invalid")
    issues = manifest.get("issues")
    endpoints = manifest.get("endpoints")
    order = manifest.get("endpoint_order")
    if not isinstance(issues, list) or len(issues) != 6:
        raise DeliveryPolicyError("terminal normalization manifest requires exactly six Issues")
    if not isinstance(endpoints, list) or len(endpoints) != 4:
        raise DeliveryPolicyError("terminal normalization manifest requires exactly four endpoints")
    if not isinstance(order, list) or len(order) != 4 or len(set(order)) != 4:
        raise DeliveryPolicyError("terminal normalization endpoint order is invalid")
    issue_map: dict[str, dict[str, Any]] = {}
    identifiers = set()
    for issue in issues:
        item = _require_object(issue, "terminal normalization manifest Issue")
        issue_id = item.get("issue_id")
        identifier = item.get("identifier")
        if (
            not isinstance(issue_id, str)
            or not issue_id
            or issue_id in issue_map
            or not isinstance(identifier, str)
            or not identifier
            or identifier in identifiers
        ):
            raise DeliveryPolicyError("terminal normalization manifest Issue identity is invalid")
        issue_map[issue_id] = item
        identifiers.add(identifier)
    normalized = []
    endpoint_ids = set()
    for endpoint in endpoints:
        item = _require_object(endpoint, "terminal normalization manifest endpoint")
        issue_id = item.get("issue_id")
        if issue_id in endpoint_ids or issue_id not in issue_map:
            raise DeliveryPolicyError("terminal normalization endpoint identity is invalid")
        endpoint_ids.add(issue_id)
        issue = issue_map[issue_id]
        for key in (
            "identifier",
            "parent_issue_id",
            "workflow_object_type",
            "object_role",
        ):
            if item.get(key) != issue.get(key):
                raise DeliveryPolicyError("terminal normalization endpoint Issue binding differs")
        role = item.get("object_role")
        object_type = item.get("workflow_object_type")
        if role == "authority":
            if object_type != "implementation" or item.get("parent_issue_id") is None:
                raise DeliveryPolicyError("terminal normalization authority topology is invalid")
        elif role == "final_mirror":
            parent = issue_map.get(item.get("parent_issue_id"))
            if object_type != "integration_validation" or not parent or parent.get(
                "object_role"
            ) != "authority":
                raise DeliveryPolicyError("terminal normalization mirror topology is invalid")
        else:
            raise DeliveryPolicyError("terminal normalization endpoint role is invalid")
        initial = _terminal_tuple(item.get("initial_tuple"), "endpoint initial tuple")
        target = _terminal_tuple(item.get("target_tuple"), "endpoint target tuple")
        if target != {
            "status": "done",
            "scope": "requirement",
            "state": "released",
            "owner_issue_id": "",
            "owner_agent_id": "",
        }:
            raise DeliveryPolicyError("terminal normalization target tuple is invalid")
        if not initial["owner_issue_id"] or not initial["owner_agent_id"]:
            raise DeliveryPolicyError("terminal normalization initial owners are missing")
        normalized.append({**item, "initial_tuple": initial, "target_tuple": target})
    if order != [item["issue_id"] for item in normalized]:
        raise DeliveryPolicyError("terminal normalization endpoint order differs from manifest")
    authorities = [item for item in normalized if item["object_role"] == "authority"]
    mirrors = [item for item in normalized if item["object_role"] == "final_mirror"]
    roots = [item for item in issues if item.get("object_role") == "root_requirement"]
    if len(authorities) != 2 or len(mirrors) != 2 or len(roots) != 2:
        raise DeliveryPolicyError("terminal normalization manifest topology is incomplete")
    for authority in authorities:
        root = issue_map.get(authority.get("parent_issue_id"))
        linked = [item for item in mirrors if item.get("parent_issue_id") == authority["issue_id"]]
        if (
            not root
            or root.get("object_role") != "root_requirement"
            or root.get("workflow_object_type") != "requirement"
            or len(linked) != 1
            or linked[0]["initial_tuple"] != authority["initial_tuple"]
        ):
            raise DeliveryPolicyError("terminal normalization Requirement pair is invalid")
    approved_roots = {
        (item.get("issue_id"), item.get("identifier")) for item in roots
    }
    immutable_roots = []
    for item in immutable["requirements"]:
        entry = _require_object(item, "terminal normalization immutable root")
        if not _is_digest(entry.get("snapshot_digest")):
            raise DeliveryPolicyError("terminal normalization immutable root digest is invalid")
        immutable_roots.append((entry.get("issue_id"), entry.get("identifier")))
    if set(immutable_roots) != approved_roots or len(set(immutable_roots)) != 2:
        raise DeliveryPolicyError("terminal normalization immutable roots differ from manifest")
    return normalized


TERMINAL_AUTHORITY_FIELDS = {
    "schema_version",
    "plan_issue_id",
    "plan_revision",
    "delivery_policy_digest",
    "workflow_instance_id",
    "fixed_operation_manifest_identity",
    "approved_root_requirement_id",
    "approved_immutable_snapshot_digest",
}


def _terminal_authority(value: Any) -> dict[str, Any]:
    authority = _require_object(value, "approved terminal authority")
    if set(authority) != TERMINAL_AUTHORITY_FIELDS or authority.get("schema_version") != 1:
        raise DeliveryPolicyError("approved terminal authority fields are invalid")
    if (
        not isinstance(authority.get("plan_issue_id"), str)
        or not authority["plan_issue_id"]
        or not isinstance(authority.get("approved_root_requirement_id"), str)
        or not authority["approved_root_requirement_id"]
        or authority.get("plan_revision") != 4
        or not _is_digest(authority.get("delivery_policy_digest"))
        or not isinstance(authority.get("workflow_instance_id"), str)
        or not authority["workflow_instance_id"]
        or not isinstance(authority.get("fixed_operation_manifest_identity"), str)
        or not authority["fixed_operation_manifest_identity"].startswith("v2.sha256:")
        or not isinstance(authority.get("approved_immutable_snapshot_digest"), str)
        or not authority["approved_immutable_snapshot_digest"].startswith("sha256:")
    ):
        raise DeliveryPolicyError("approved terminal authority is invalid")
    return authority


def _terminal_plan_binding(
    snapshot: dict[str, Any], approved_authority: Any
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    manifest = _require_object(snapshot.get("manifest"), "terminal normalization manifest")
    endpoints = _validate_terminal_manifest(manifest)
    plan_evidence = _require_object(snapshot.get("plan"), "terminal normalization Plan evidence")
    manifest_plan = manifest["plan"]
    identity = _terminal_manifest_identity(manifest)
    authority = _terminal_authority(approved_authority)
    if plan_evidence.get("workflow_object_type") != "plan":
        raise DeliveryPolicyError("terminal normalization Plan evidence object type is invalid")
    if _metadata_capacity_reasons(plan_evidence, []):
        raise DeliveryPolicyError(
            "terminal normalization Plan metadata key inventory is invalid"
        )
    approved_root = manifest["root_authority"]
    root_requirement_id = plan_evidence.get("root_requirement_id")
    if (
        not isinstance(root_requirement_id, str)
        or not root_requirement_id
        or plan_evidence.get("parent_issue_id") != root_requirement_id
        or root_requirement_id != approved_root.get("issue_id")
        or plan_evidence.get("approved_root_requirement_id") != root_requirement_id
    ):
        raise DeliveryPolicyError(
            "terminal normalization Plan root authority is invalid"
        )
    immutable_evidence_digest = _terminal_immutable_evidence_digest(
        snapshot.get("immutable_evidence"), manifest
    )
    bindings = {
        "issue_id": manifest_plan["issue_id"],
        "plan_revision": manifest_plan["plan_revision"],
        "delivery_policy_digest": manifest_plan["delivery_policy_digest"],
        "workflow_instance_id": manifest["workflow_instance_id"],
        "fixed_operation_manifest_identity": identity,
        "root_requirement_id": root_requirement_id,
        "approved_immutable_snapshot_digest": "sha256:"
        + manifest["immutable_snapshot"]["aggregate_digest"],
    }
    if any(plan_evidence.get(key) != value for key, value in bindings.items()):
        raise DeliveryPolicyError("terminal normalization Plan or manifest identity drifted")
    authority_bindings = {
        "plan_issue_id": manifest_plan["issue_id"],
        "plan_revision": manifest_plan["plan_revision"],
        "delivery_policy_digest": manifest_plan["delivery_policy_digest"],
        "workflow_instance_id": manifest["workflow_instance_id"],
        "fixed_operation_manifest_identity": identity,
        "approved_root_requirement_id": root_requirement_id,
        "approved_immutable_snapshot_digest": bindings[
            "approved_immutable_snapshot_digest"
        ],
    }
    if any(authority.get(key) != value for key, value in authority_bindings.items()):
        raise DeliveryPolicyError("approved terminal authority does not match the operation")
    return {
        "schema_version": 1,
        "record_type": "workspace_lease_terminal_normalization",
        **bindings,
        "pre_snapshot_digest": snapshot.get("pre_snapshot_digest"),
        "immutable_evidence_digest": immutable_evidence_digest,
        "endpoint_order": manifest["endpoint_order"],
    }, endpoints


TERMINAL_IMMUTABLE_FIELDS = (
    "issue_id",
    "identifier",
    "parent_issue_id",
    "workflow_object_type",
    "status",
    "approval_revision",
    "approved_requirement_head_sha",
    "reviewed_commit_sha",
    "current_requirement_head_sha",
    "plan_revision",
    "delivery_policy_digest",
    "requirement_branch",
    "delivery_evidence_record",
    "delivery_handoff_record",
)


def _terminal_immutable_entry(
    value: Any, manifest_root: dict[str, Any]
) -> dict[str, Any]:
    entry = _require_object(value, "terminal immutable Requirement evidence")
    if set(entry) != set(TERMINAL_IMMUTABLE_FIELDS):
        raise DeliveryPolicyError(
            "terminal immutable Requirement evidence fields are invalid"
        )
    normalized = {key: entry.get(key) for key in TERMINAL_IMMUTABLE_FIELDS}
    for key in ("issue_id", "identifier", "parent_issue_id", "workflow_object_type"):
        if normalized[key] != manifest_root.get(key):
            raise DeliveryPolicyError(
                "terminal immutable Requirement identity differs from manifest"
            )
    if normalized["status"] != "done":
        raise DeliveryPolicyError("terminal immutable Requirement must remain done")
    revision = normalized["approval_revision"]
    if not isinstance(revision, int) or isinstance(revision, bool) or revision <= 0:
        raise DeliveryPolicyError(
            "terminal immutable Requirement approval revision is invalid"
        )
    plan_revision = normalized["plan_revision"]
    if (
        not isinstance(plan_revision, int)
        or isinstance(plan_revision, bool)
        or plan_revision <= 0
    ):
        raise DeliveryPolicyError(
            "terminal immutable Requirement Plan revision is invalid"
        )
    approved = normalized["approved_requirement_head_sha"]
    if (
        not _is_sha(approved)
        or normalized["reviewed_commit_sha"] != approved
        or normalized["current_requirement_head_sha"] != approved
    ):
        raise DeliveryPolicyError(
            "terminal immutable Requirement approved head binding is invalid"
        )
    if not _is_digest(normalized["delivery_policy_digest"]):
        raise DeliveryPolicyError(
            "terminal immutable Requirement policy digest is invalid"
        )
    if (
        not isinstance(normalized["requirement_branch"], str)
        or not normalized["requirement_branch"]
    ):
        raise DeliveryPolicyError(
            "terminal immutable Requirement branch is missing"
        )
    delivery_scalar = normalized["delivery_evidence_record"]
    delivery = decode_metadata_record(
        delivery_scalar, "terminal immutable delivery_evidence_record"
    )
    for key, expected in (
        ("plan_revision", plan_revision),
        ("delivery_policy_digest", normalized["delivery_policy_digest"]),
        ("reviewed_commit_sha", approved),
        ("current_requirement_head_sha", approved),
    ):
        if delivery.get(key) != expected:
            raise DeliveryPolicyError(
                f"terminal immutable delivery evidence {key} binding is invalid"
            )
    handoff = decode_metadata_record(
        normalized["delivery_handoff_record"],
        "terminal immutable delivery_handoff_record",
    )
    for key, expected in (
        ("issue_id", normalized["issue_id"]),
        ("plan_revision", plan_revision),
        ("reviewed_commit_sha", approved),
        ("delivery_policy_digest", normalized["delivery_policy_digest"]),
    ):
        if handoff.get(key) != expected:
            raise DeliveryPolicyError(
                f"terminal immutable handoff {key} binding is invalid"
            )
    expected_delivery_digest = hashlib.sha256(
        delivery_scalar.encode("utf-8")
    ).hexdigest()
    if handoff.get("delivery_record_digest") != expected_delivery_digest:
        raise DeliveryPolicyError(
            "terminal immutable handoff delivery binding is invalid"
        )
    return normalized


def _terminal_immutable_evidence_digest(value: Any, manifest: dict[str, Any]) -> str:
    evidence = _require_object(value, "terminal immutable evidence")
    if set(evidence) != {
        "schema_version",
        "pre",
        "post",
        "pre_digest",
        "post_digest",
    }:
        raise DeliveryPolicyError("terminal immutable evidence fields are invalid")
    if evidence.get("schema_version") != 1:
        raise DeliveryPolicyError("terminal immutable evidence schema is unsupported")
    roots = [
        item
        for item in manifest["issues"]
        if item.get("object_role") == "root_requirement"
    ]
    root_map = {item["issue_id"]: item for item in roots}
    pre = evidence.get("pre")
    post = evidence.get("post")
    if not isinstance(pre, list) or not isinstance(post, list):
        raise DeliveryPolicyError("terminal immutable pre/post evidence is missing")

    def normalize(items: list[Any], label: str) -> list[dict[str, Any]]:
        if len(items) != 2:
            raise DeliveryPolicyError(
                f"terminal immutable {label} evidence requires exactly two Requirements"
            )
        item_map = {
            item.get("issue_id"): item
            for item in items
            if isinstance(item, dict) and isinstance(item.get("issue_id"), str)
        }
        if len(item_map) != 2 or set(item_map) != set(root_map):
            raise DeliveryPolicyError(
                f"terminal immutable {label} Requirement identities are invalid"
            )
        return [
            _terminal_immutable_entry(item_map[root["issue_id"]], root)
            for root in roots
        ]

    normalized_pre = normalize(pre, "pre")
    normalized_post = normalize(post, "post")
    pre_digest = digest(normalized_pre)
    post_digest = digest(normalized_post)
    if evidence.get("pre_digest") != pre_digest:
        raise DeliveryPolicyError("terminal immutable pre digest is not canonical")
    if evidence.get("post_digest") != post_digest:
        raise DeliveryPolicyError("terminal immutable post digest is not canonical")
    if normalized_pre != normalized_post or pre_digest != post_digest:
        raise DeliveryPolicyError("terminal immutable Requirement evidence drifted")
    approved = manifest["immutable_snapshot"]
    approved_entries = {
        item["issue_id"]: item for item in approved["requirements"]
    }
    for entry in normalized_pre:
        if approved_entries[entry["issue_id"]]["snapshot_digest"] != digest(entry):
            raise DeliveryPolicyError(
                "terminal immutable Requirement differs from approved snapshot"
            )
    if approved["aggregate_digest"] != pre_digest:
        raise DeliveryPolicyError("terminal immutable aggregate digest drifted")
    return digest(
        {
            "schema_version": 1,
            "requirements": normalized_pre,
            "evidence_digest": pre_digest,
        }
    )


def _superseded_release_record(plan: dict[str, Any], checkpoint: int) -> str:
    return encode_metadata_record({**plan, "checkpoint": checkpoint})


SUPERSEDED_AUTHORITY_FIELDS = {
    "schema_version", "workspace_id", "root_requirement_id", "plan_issue_id",
    "superseded_implementation_id", "target_issue_id", "original_owner_id",
    "plan_revision", "delivery_policy_digest", "fixed_operation_manifest_identity",
    "old_branch", "new_branch", "expected_worktree_path", "expected_pr_number",
    "expected_pr_head_sha", "expected_blocker_digest", "base_commit_sha",
    "reviewed_commit_sha", "merged_commit_sha", "review_comment_id", "reviewer_id",
    "merge_method",
}


def superseded_task_release(snapshot: Any, approved_authority: Any) -> dict[str, Any]:
    data = _require_object(snapshot, "superseded task release snapshot")
    context = _require_object(data.get("context"), "superseded release context")
    target = _require_object(data.get("target"), "superseded release target")
    guard = _require_object(data.get("guard"), "superseded release guard")
    pr = _require_object(data.get("pull_request"), "superseded release pull request")
    source = _require_object(data.get("source"), "superseded release source")
    authority = _require_object(approved_authority, "approved superseded release authority")
    if set(authority) != SUPERSEDED_AUTHORITY_FIELDS or authority.get("schema_version") != 1:
        raise DeliveryPolicyError("approved superseded release authority fields are invalid")
    reasons: list[str] = []
    for key in (
        "workspace_id", "root_requirement_id", "plan_issue_id",
        "superseded_implementation_id", "target_issue_id", "original_owner_id",
        "old_branch", "new_branch", "expected_worktree_path",
    ):
        _add(reasons, isinstance(context.get(key), str) and bool(context.get(key)), f"{key} is missing")
    _add(reasons, context.get("plan_revision") == 4, "superseded release Plan revision is invalid")
    _add(reasons, _is_digest(context.get("delivery_policy_digest")), "superseded release policy digest is invalid")
    _add(reasons, isinstance(context.get("fixed_operation_manifest_identity"), str) and context["fixed_operation_manifest_identity"].startswith("v2.sha256:"), "superseded release manifest identity is invalid")
    _add(reasons, target.get("issue_id") == context.get("target_issue_id"), "superseded release target identity drifted")
    _add(reasons, target.get("workspace_lease_scope") == "task", "superseded release scope must be task")
    existing_record = target.get(SUPERSEDED_TASK_RELEASE_RECORD_KEY)
    if existing_record in {None, ""}:
        _add(reasons, target.get("workspace_lease_state") == "held", "superseded release initial state must be held")
        _add(reasons, target.get("workspace_lease_owner_issue_id") == context.get("target_issue_id"), "superseded release owner Issue is invalid")
        _add(reasons, target.get("workspace_lease_owner_agent_id") == context.get("original_owner_id"), "superseded release original owner is invalid")
    _add(reasons, target.get("status") == "blocked", "superseded task must remain blocked")
    _add(reasons, pr.get("number") == 26 and pr.get("state") == "closed", "superseded PR must be closed PR 26")
    _add(reasons, pr.get("head_branch") == context.get("old_branch"), "superseded PR branch drifted")
    _add(reasons, guard.get("valid") is True and guard.get("clean") is True and guard.get("registered") is True, "superseded worktree guard is invalid")
    _add(reasons, guard.get("resolved_path") == context.get("expected_worktree_path"), "superseded worktree path drifted")
    _add(reasons, guard.get("branch") == context.get("old_branch") and guard.get("head") == pr.get("head_sha"), "superseded worktree branch or head drifted")
    _add(reasons, guard.get("unfinished_operations") in (None, []), "superseded worktree has an unfinished operation")
    _add(reasons, source.get("branch") == context.get("new_branch"), "reviewed source branch drifted")
    _add(reasons, _is_sha(source.get("reviewed_commit_sha")) and source.get("reviewed_commit_sha") == source.get("merged_commit_sha"), "reviewed merged source is invalid")
    _add(reasons, source.get("review_status") == "APPROVED", "reviewed source is not approved")
    blocker = {key: target.get(key, "") for key in LEASE_BLOCKER_FIELDS}
    context_binding = {
        key: context.get(key)
        for key in (
            "workspace_id", "root_requirement_id", "plan_issue_id",
            "superseded_implementation_id", "target_issue_id", "original_owner_id",
            "plan_revision", "delivery_policy_digest", "fixed_operation_manifest_identity",
            "old_branch", "new_branch", "expected_worktree_path",
        )
    }
    if any(authority.get(key) != value for key, value in context_binding.items()):
        reasons.append("approved superseded release identity drifted")
    _add(reasons, pr.get("number") == authority.get("expected_pr_number"), "approved superseded PR number drifted")
    _add(reasons, pr.get("head_sha") == authority.get("expected_pr_head_sha"), "approved superseded PR head drifted")
    _add(reasons, digest(blocker) == authority.get("expected_blocker_digest"), "approved superseded blocker bytes drifted")
    for key in (
        "base_commit_sha", "reviewed_commit_sha", "merged_commit_sha",
        "review_comment_id", "reviewer_id", "merge_method",
    ):
        _add(reasons, source.get(key) == authority.get(key), f"approved source {key} drifted")
    _add(reasons, _is_sha(authority.get("base_commit_sha")), "approved source base SHA is invalid")
    _add(reasons, _is_sha(authority.get("reviewed_commit_sha")), "approved source reviewed SHA is invalid")
    _add(reasons, _is_sha(authority.get("merged_commit_sha")), "approved source merged SHA is invalid")
    _add(reasons, isinstance(authority.get("review_comment_id"), str) and bool(authority["review_comment_id"]), "approved Review comment is missing")
    _add(reasons, isinstance(authority.get("reviewer_id"), str) and bool(authority["reviewer_id"]), "approved Reviewer is missing")
    _add(reasons, authority.get("merge_method") == "--no-ff", "approved merge method is invalid")
    if reasons:
        return _terminal_rejected(reasons)
    capacity = _metadata_capacity_reasons(target, SUPERSEDED_TASK_RELEASE_KEYS)
    if capacity:
        return _terminal_rejected(capacity)
    initial = {
        "workspace_lease_state": "held",
        "workspace_lease_owner_issue_id": context["target_issue_id"],
        "workspace_lease_owner_agent_id": context["original_owner_id"],
    }
    desired = {
        "workspace_lease_state": "released",
        "workspace_lease_owner_issue_id": "",
        "workspace_lease_owner_agent_id": "",
    }
    plan = {
        "schema_version": 1,
        "record_type": "workspace_lease_superseded_release",
        "action": "superseded_task_release",
        **context,
        "initial": initial,
        "desired": desired,
        "guard_digest": digest(guard),
        "pull_request_digest": digest(pr),
        "blocker_digest": digest(blocker),
        "reviewed_source_digest": digest(source),
    }
    writes = [
        {"kind": "metadata", "issue_id": target["issue_id"], "key": SUPERSEDED_TASK_RELEASE_RECORD_KEY, "value": _superseded_release_record(plan, 0)},
        {"kind": "metadata", "issue_id": target["issue_id"], "key": "workspace_lease_state", "value": "released"},
        {"kind": "metadata", "issue_id": target["issue_id"], "key": SUPERSEDED_TASK_RELEASE_RECORD_KEY, "value": _superseded_release_record(plan, 1)},
        {"kind": "metadata", "issue_id": target["issue_id"], "key": "workspace_lease_owner_issue_id", "value": ""},
        {"kind": "metadata", "issue_id": target["issue_id"], "key": SUPERSEDED_TASK_RELEASE_RECORD_KEY, "value": _superseded_release_record(plan, 2)},
        {"kind": "metadata", "issue_id": target["issue_id"], "key": "workspace_lease_owner_agent_id", "value": ""},
        {"kind": "metadata", "issue_id": target["issue_id"], "key": SUPERSEDED_TASK_RELEASE_RECORD_KEY, "value": _superseded_release_record(plan, 3)},
    ]
    expected = {**initial, SUPERSEDED_TASK_RELEASE_RECORD_KEY: ""}
    actual = {key: target.get(key, "") for key in SUPERSEDED_TASK_RELEASE_KEYS}
    prefixes = [0] if actual == expected else []
    for index, write in enumerate(writes, start=1):
        expected[write["key"]] = write["value"]
        if actual == expected:
            prefixes.append(index)
    if not prefixes:
        return _terminal_rejected(["superseded release state is not a canonical prefix"])
    progress = max(prefixes)
    if progress == len(writes):
        return {"allowed": True, "outcome": "already_complete", "reasons": [], "writes": [], "complete": True, "no_action": True, "status_writes": [], "blocker_writes": []}
    return {"allowed": True, "outcome": "next_write", "reasons": [], "writes": [writes[progress]], "complete": False, "no_action": False, "progress": progress, "total_writes": len(writes), "status_writes": [], "blocker_writes": []}


def _terminal_record(plan: dict[str, Any], endpoint: dict[str, Any], checkpoint: int) -> str:
    return encode_metadata_record(
        {
            **plan,
            "endpoint_issue_id": endpoint["issue_id"],
            "initial_tuple": endpoint["initial_tuple"],
            "target_tuple": endpoint["target_tuple"],
            "checkpoint": checkpoint,
        }
    )


def _terminal_full_writes(plan: dict[str, Any], endpoints: list[dict[str, Any]]) -> list[dict[str, Any]]:
    writes = []
    fields = (
        ("workspace_lease_state", "state"),
        ("workspace_lease_owner_issue_id", "owner_issue_id"),
        ("workspace_lease_owner_agent_id", "owner_agent_id"),
    )
    for endpoint in endpoints:
        writes.append(
            {
                "kind": "metadata",
                "issue_id": endpoint["issue_id"],
                "key": TERMINAL_NORMALIZATION_RECORD_KEY,
                "value": _terminal_record(plan, endpoint, 0),
            }
        )
        for checkpoint, (key, tuple_key) in enumerate(fields, start=1):
            writes.append(
                {
                    "kind": "metadata",
                    "issue_id": endpoint["issue_id"],
                    "key": key,
                    "value": endpoint["target_tuple"][tuple_key],
                }
            )
            writes.append(
                {
                    "kind": "metadata",
                    "issue_id": endpoint["issue_id"],
                    "key": TERMINAL_NORMALIZATION_RECORD_KEY,
                    "value": _terminal_record(plan, endpoint, checkpoint),
                }
            )
    return writes


def _terminal_endpoint_projection(endpoint: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace_lease_state": endpoint.get("workspace_lease_state"),
        "workspace_lease_owner_issue_id": endpoint.get("workspace_lease_owner_issue_id", ""),
        "workspace_lease_owner_agent_id": endpoint.get("workspace_lease_owner_agent_id", ""),
        TERMINAL_NORMALIZATION_RECORD_KEY: endpoint.get(TERMINAL_NORMALIZATION_RECORD_KEY, ""),
    }


def _terminal_rejected(reasons: list[str]) -> dict[str, Any]:
    return {
        "allowed": False,
        "outcome": "rejected",
        "reasons": reasons,
        "writes": [],
        "status_writes": [],
        "blocker_writes": [],
    }


def terminal_normalization_transition(snapshot: Any, approved_authority: Any) -> dict[str, Any]:
    data = _require_object(snapshot, "terminal normalization snapshot")
    plan, manifest_endpoints = _terminal_plan_binding(data, approved_authority)
    if not _is_digest(plan.get("pre_snapshot_digest")):
        raise DeliveryPolicyError("terminal normalization pre-snapshot digest is invalid")
    if data.get("immutable_evidence") is None:
        raise DeliveryPolicyError("terminal normalization immutable evidence is missing")
    observed = data.get("endpoints")
    if not isinstance(observed, list) or len(observed) != 4:
        return _terminal_rejected(["exactly four observed endpoints are required"])
    observed_map = {
        item.get("issue_id"): item
        for item in observed
        if isinstance(item, dict) and isinstance(item.get("issue_id"), str)
    }
    if len(observed_map) != 4 or set(observed_map) != set(plan["endpoint_order"]):
        return _terminal_rejected(["observed endpoints do not match the fixed manifest"])
    expected: dict[str, dict[str, Any]] = {}
    reasons = []
    for manifest_endpoint in manifest_endpoints:
        endpoint = observed_map[manifest_endpoint["issue_id"]]
        for key in ("identifier", "parent_issue_id", "workflow_object_type"):
            _add(
                reasons,
                endpoint.get(key) == manifest_endpoint.get(key),
                f"endpoint {manifest_endpoint['issue_id']} {key} drifted",
            )
        _add(reasons, endpoint.get("status") == "done", "endpoint status drifted")
        _add(
            reasons,
            endpoint.get("workspace_lease_scope") == "requirement",
            "endpoint lease scope drifted",
        )
        capacity = _metadata_capacity_reasons(endpoint, TERMINAL_NORMALIZATION_ENDPOINT_KEYS)
        reasons.extend(capacity)
        initial = manifest_endpoint["initial_tuple"]
        expected[manifest_endpoint["issue_id"]] = {
            "workspace_lease_state": initial["state"],
            "workspace_lease_owner_issue_id": initial["owner_issue_id"],
            "workspace_lease_owner_agent_id": initial["owner_agent_id"],
            TERMINAL_NORMALIZATION_RECORD_KEY: "",
        }
    if reasons:
        return _terminal_rejected(reasons)
    actual = {key: _terminal_endpoint_projection(value) for key, value in observed_map.items()}
    writes = _terminal_full_writes(plan, manifest_endpoints)
    prefixes = [0] if expected == actual else []
    for index, write in enumerate(writes, start=1):
        expected[write["issue_id"]][write["key"]] = write["value"]
        if expected == actual:
            prefixes.append(index)
    if not prefixes:
        return _terminal_rejected(["terminal normalization state is not a canonical prefix"])
    progress = max(prefixes)
    if progress == len(writes):
        return {
            "allowed": True,
            "outcome": "already_complete",
            "reasons": [],
            "writes": [],
            "progress": progress,
            "total_writes": len(writes),
            "complete": True,
            "no_action": True,
            "status_writes": [],
            "blocker_writes": [],
        }
    return {
        "allowed": True,
        "outcome": "next_write",
        "reasons": [],
        "writes": [writes[progress]],
        "progress": progress,
        "total_writes": len(writes),
        "complete": False,
        "no_action": False,
        "status_writes": [],
        "blocker_writes": [],
    }


ATTEST_AUTHORITY_FIELDS = {
    "schema_version", "reviewed_commit_sha", "delivery_evidence_record",
    "deployed_source_commit", "deployment_plan_digest", "pre_snapshot_digest",
    "post_snapshot_digest",
}


def terminal_normalization_attest(
    snapshot: Any, approved_authority: Any, attestation_authority: Any
) -> dict[str, Any]:
    data = _require_object(snapshot, "terminal normalization attestation snapshot")
    transitioned = terminal_normalization_transition(data, approved_authority)
    if not transitioned["allowed"] or not transitioned.get("complete"):
        return _terminal_rejected(["all fixed endpoints must be terminal before attestation"])
    root = _require_object(data.get("root"), "terminal normalization root")
    manifest = _require_object(data.get("manifest"), "terminal normalization manifest")
    plan = _require_object(data.get("plan"), "terminal normalization Plan evidence")
    deployment = _require_object(data.get("deployment"), "terminal normalization deployment evidence")
    attested = _require_object(attestation_authority, "approved attestation authority")
    if set(attested) != ATTEST_AUTHORITY_FIELDS or attested.get("schema_version") != 1:
        raise DeliveryPolicyError("approved attestation authority fields are invalid")
    reasons = []
    _add(reasons, root.get("issue_id") == root.get("root_requirement_id"), "attestation root is not top-level")
    _add(
        reasons,
        root.get("issue_id") == plan.get("root_requirement_id")
        and plan.get("parent_issue_id") == root.get("issue_id"),
        "attestation root is not the current Plan authority",
    )
    _add(reasons, root.get("workflow_object_type") == "requirement", "attestation root type is invalid")
    _add(reasons, root.get("workflow_instance_id") == manifest.get("workflow_instance_id"), "attestation workflow binding drifted")
    _add(reasons, root.get("plan_revision") == manifest["plan"]["plan_revision"], "attestation Plan revision drifted")
    _add(reasons, root.get("delivery_policy_digest") == manifest["plan"]["delivery_policy_digest"], "attestation policy digest drifted")
    _add(reasons, plan.get("fixed_operation_manifest_identity") == _terminal_manifest_identity(manifest), "attestation manifest identity drifted")
    _add(reasons, _is_sha(deployment.get("deployed_source_commit")), "deployed source commit is invalid")
    for key in ("deployment_plan_digest", "pre_snapshot_digest", "post_snapshot_digest"):
        _add(reasons, _is_digest(deployment.get(key)), f"{key} is invalid")
    _add(reasons, deployment.get("pre_snapshot_digest") == data.get("pre_snapshot_digest"), "pre-snapshot digest drifted")
    _add(
        reasons,
        deployment.get("post_snapshot_digest") == data.get("post_snapshot_digest"),
        "post-snapshot digest drifted",
    )
    _add(
        reasons,
        deployment.get("deployed_source_commit")
        == root.get("deployed_source_commit"),
        "deployed source is not bound to the current root",
    )
    _add(
        reasons,
        deployment.get("deployment_plan_digest")
        == root.get("deployment_plan_digest"),
        "deployment Plan is not bound to the current root",
    )
    delivery = decode_metadata_record(
        attested.get("delivery_evidence_record"), "approved delivery evidence"
    )
    _add(reasons, attested.get("reviewed_commit_sha") == delivery.get("reviewed_commit_sha"), "approved delivery reviewed head drifted")
    _add(reasons, attested.get("deployed_source_commit") == deployment.get("deployed_source_commit"), "approved deployed source drifted")
    _add(reasons, attested.get("deployment_plan_digest") == deployment.get("deployment_plan_digest"), "approved deployment Plan drifted")
    canonical_pre = data.get("canonical_pre_snapshot")
    canonical_post = data.get("canonical_post_snapshot")
    _add(reasons, isinstance(canonical_pre, dict), "canonical pre-snapshot is missing")
    _add(reasons, isinstance(canonical_post, dict), "canonical post-snapshot is missing")
    if isinstance(canonical_pre, dict) and isinstance(canonical_post, dict):
        recomputed_pre = digest(canonical_pre)
        recomputed_post = digest(canonical_post)
        _add(reasons, deployment.get("pre_snapshot_digest") == recomputed_pre == attested.get("pre_snapshot_digest"), "canonical pre-snapshot digest drifted")
        _add(reasons, deployment.get("post_snapshot_digest") == recomputed_post == attested.get("post_snapshot_digest"), "canonical post-snapshot digest drifted")
    endpoint_records = {
        endpoint["issue_id"]: endpoint.get(TERMINAL_NORMALIZATION_RECORD_KEY)
        for endpoint in data["endpoints"]
    }
    immutable_evidence_digest = _terminal_immutable_evidence_digest(
        data.get("immutable_evidence"), manifest
    )
    updates = {"terminal_normalization_evidence_record": "pending"}
    reasons.extend(_metadata_capacity_reasons(root, updates))
    if reasons:
        return _terminal_rejected(reasons)
    record = encode_metadata_record(
        {
            "schema_version": 1,
            "record_type": "terminal_normalization_evidence",
            "root_requirement_id": root["issue_id"],
            "workflow_instance_id": manifest["workflow_instance_id"],
            "plan_issue_id": manifest["plan"]["issue_id"],
            "plan_revision": manifest["plan"]["plan_revision"],
            "delivery_policy_digest": manifest["plan"]["delivery_policy_digest"],
            "fixed_operation_manifest_identity": _terminal_manifest_identity(manifest),
            "deployed_source_commit": deployment["deployed_source_commit"],
            "deployment_plan_digest": deployment["deployment_plan_digest"],
            "pre_snapshot_digest": deployment["pre_snapshot_digest"],
            "post_snapshot_digest": deployment["post_snapshot_digest"],
            "endpoint_records_digest": digest(endpoint_records),
            "immutable_evidence_digest": immutable_evidence_digest,
        }
    )
    return {
        "allowed": True,
        "outcome": "attested",
        "reasons": [],
        "writes": [
            {
                "kind": "metadata",
                "issue_id": root["issue_id"],
                "key": "terminal_normalization_evidence_record",
                "value": record,
            }
        ],
        "record": record,
        "status_writes": [],
        "blocker_writes": [],
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

    lease = subparsers.add_parser("lease-transition")
    lease.add_argument("--snapshot", required=True)
    terminal = subparsers.add_parser("terminal-normalization")
    terminal.add_argument("--action", required=True, choices=TERMINAL_NORMALIZATION_ACTIONS)
    terminal.add_argument("--snapshot", required=True)
    terminal.add_argument("--approved-authority", required=True)
    terminal.add_argument("--attestation-authority")
    superseded = subparsers.add_parser("superseded-task-release")
    superseded.add_argument("--snapshot", required=True)
    superseded.add_argument("--approved-authority", required=True)
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
        if args.command == "lease-transition":
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            result = lease_transition_preflight(snapshot)
            print_json(result)
            return 0 if result["allowed"] else 1
        if args.command == "terminal-normalization":
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            authority = json.loads(Path(args.approved_authority).read_text(encoding="utf-8"))
            result = terminal_normalization_transition(snapshot, authority) if args.action == "transition" else terminal_normalization_attest(
                snapshot,
                authority,
                json.loads(Path(args.attestation_authority).read_text(encoding="utf-8")) if args.attestation_authority else None,
            )
            print_json(result)
            return 0 if result["allowed"] else 1
        if args.command == "superseded-task-release":
            snapshot = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            authority = json.loads(Path(args.approved_authority).read_text(encoding="utf-8"))
            result = superseded_task_release(snapshot, authority)
            print_json(result)
            return 0 if result["allowed"] else 1
    except (DeliveryPolicyError, OSError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
