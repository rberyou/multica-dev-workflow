import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "skills/multica-delivery-policy/scripts/delivery_policy.py"
)
SCHEMA_PATH = (
    ROOT
    / "skills/multica-delivery-policy/references/project-delivery.schema.json"
)
PLAN_SCHEMA_PATH = (
    ROOT
    / "skills/multica-delivery-policy/references/plan-policy.schema.json"
)
SPEC = importlib.util.spec_from_file_location("delivery_policy", POLICY_PATH)
delivery_policy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(delivery_policy)


REVIEWED_SHA = "1" * 40
TARGET_BASE_SHA = "2" * 40
DEFAULT_BASE_SHA = "3" * 40
MERGED_SHA = "4" * 40
TREE_SHA = "5" * 40
POLICY_DIGEST = "v3.sha256:" + "6" * 64


def terminal_lease_group(
    root_id: str,
    authority_state: str,
    mirror_state: str,
    suffix: str,
    *,
    workspace_mode: str = "lightweight",
    holder_role: str = "integrator",
    metadata_key_count: int = 0,
) -> dict:
    workflow_instance = "workspace-test"
    scope = {
        "branch_only": "repository",
        "lightweight": "requirement",
        "isolated": "task",
    }[workspace_mode]
    owner_issue = f"HOLDER-{suffix}"
    owner_agent = f"agent-{suffix}"

    def endpoint(issue_id, role, state):
        canonical = state == "canonical"
        is_authority = role == "implementation_authority"
        return {
            "issue_id": issue_id,
            "endpoint_role": role,
            "workflow_object_type": (
                "implementation" if is_authority else "integration_validation"
            ),
            "workflow_stage": (
                "implementation" if is_authority else "integration_validation"
            ),
            "final_integration_validation": False if is_authority else True,
            "status": "done",
            "root_requirement_id": root_id,
            "workflow_instance_id": workflow_instance,
            "workspace_lease_scope": scope,
            "workspace_lease_state": "released" if canonical else state,
            "workspace_lease_owner_issue_id": "" if canonical else owner_issue,
            "workspace_lease_owner_agent_id": "" if canonical else owner_agent,
            "metadata_key_count": metadata_key_count,
        }

    branch = f"req/{root_id.lower()}"
    head = (suffix.lower()[0] if suffix else "a") * 40
    return {
        "root": {
            "issue_id": root_id,
            "workflow_object_type": "requirement",
            "status": "done",
            "workflow_instance_id": workflow_instance,
            "workspace_mode": workspace_mode,
            "plan_schema_version": 3,
            "plan_revision": 5,
            "current_plan_revision": 5,
            "approved_policy_digest": POLICY_DIGEST,
            "current_policy_digest": POLICY_DIGEST,
            "target_branch": "main",
            "current_target_branch": "main",
            "plan_frozen_fields": [
                "plan_revision",
                "policy_digest",
                "target_branch",
            ],
            "active_child_issue_ids": [],
            "pending_review_issue_ids": [],
            "review_evidence_current": True,
            "open_approval_gates": [],
            "approval_evidence_current": True,
            "merge_evidence_current": True,
            "delivery_evidence_current": True,
            "delivery_complete": True,
        },
        "authority": endpoint(
            f"AUTH-{suffix}", "implementation_authority", authority_state
        ),
        "mirror": endpoint(
            f"MIRROR-{suffix}",
            "final_integration_validation_mirror",
            mirror_state,
        ),
        "historical_holder": {
            "issue_id": owner_issue,
            "agent_id": owner_agent,
            "agent_role": holder_role,
            "root_requirement_id": root_id,
            "workflow_instance_id": workflow_instance,
            "status": "done",
            "active": False,
        },
        "git": {
            "exists": True,
            "clean": True,
            "unfinished_operations": [],
            "branch": branch,
            "expected_branch": branch,
            "head_sha": head,
            "expected_head_sha": head,
            "worktree_id": f"worktree-{suffix}",
            "expected_worktree_id": f"worktree-{suffix}",
        },
    }


def lease_snapshot(groups=None) -> dict:
    if groups is None:
        groups = [terminal_lease_group("R-41", "canonical", "canonical", "41")]
    endpoint_inventory = []
    discovered_issue_inventory = []
    for group in groups:
        for key in ("authority", "mirror"):
            endpoint = copy.deepcopy(group[key])
            endpoint.pop("metadata_key_count", None)
            endpoint_inventory.append(endpoint)
            discovered_issue_inventory.append(copy.deepcopy(endpoint))
        discovered_issue_inventory.append(
            {
                "issue_id": f"TASK-RESIDUE-{group['root']['issue_id']}",
                "endpoint_role": None,
                "workflow_object_type": "development_task",
                "workflow_stage": "development_task",
                "final_integration_validation": False,
                "root_requirement_id": group["root"]["issue_id"],
                "workspace_lease_state": "integrator",
            }
        )
    return {
        "schema_version": 1,
        "snapshot_read_id": "fresh-read-1",
        "workflow_instance_id": "workspace-test",
        "inventory_selection_rule": delivery_policy.LEASE_INVENTORY_SELECTION_RULE,
        "inventory_complete": True,
        "requirement_root_ids": [group["root"]["issue_id"] for group in groups],
        "discovered_requirement_root_ids": [
            group["root"]["issue_id"] for group in groups
        ],
        "discovered_requirement_inventory": [
            {
                "issue_id": group["root"]["issue_id"],
                "workflow_object_type": "requirement",
                "workflow_instance_id": group["root"]["workflow_instance_id"],
                "status": "done",
            }
            for group in groups
        ],
        "terminal_groups": copy.deepcopy(groups),
        "endpoint_inventory": endpoint_inventory,
        "discovered_issue_inventory": discovered_issue_inventory,
        "current_claims_complete": True,
        "current_claims": [],
    }


def refresh_lease_inventory(snapshot: dict) -> None:
    residue = [
        item
        for item in snapshot.get("discovered_issue_inventory", [])
        if item.get("endpoint_role") not in {
            "implementation_authority",
            "final_integration_validation_mirror",
        }
    ]
    endpoint_inventory = []
    for group in snapshot["terminal_groups"]:
        for key in ("authority", "mirror"):
            endpoint = copy.deepcopy(group[key])
            endpoint.pop("metadata_key_count", None)
            endpoint_inventory.append(endpoint)
    snapshot["endpoint_inventory"] = endpoint_inventory
    snapshot["discovered_issue_inventory"] = copy.deepcopy(endpoint_inventory) + residue


def final_gate_snapshot(
    mode: str = "direct_push", *, target_branch: str = "release/v2"
) -> dict:
    requirement_pr_enabled = mode == "requirement_pr"
    remote_configured = mode != "local_only"
    root = {
        "issue_id": "R-1",
        "root_requirement_id": "R-1",
        "workflow_object_type": "requirement",
        "status": "in_progress",
        "plan_status": "done",
        "implementation_status": "done",
        "integration_validation_status": "done",
        "integration_review_status": "approved",
        "tests_passed": True,
        "acceptance_complete": True,
        "policy_valid": True,
        "blockers_clear": True,
        "dependencies_satisfied": True,
        "plan_revision": 2,
        "delivery_policy_digest": POLICY_DIGEST,
        "reviewed_commit_sha": REVIEWED_SHA,
        "human_approver_id": "human-1",
        "target_branch": target_branch,
        "target_base_sha": TARGET_BASE_SHA,
        "default_branch": "main",
        "default_base_sha": DEFAULT_BASE_SHA,
        "requirement_pr_enabled": requirement_pr_enabled,
        "remote_configured": remote_configured,
        "direct_target_push": mode == "direct_push",
        "direct_default_push": mode == "direct_push",
        "final_approval_gate_state": "closed",
        "metadata_keys": [
            "managed_by",
            "workflow_instance_id",
            "workflow_object_type",
            "root_requirement_id",
            "workflow_version",
            "protocol_revision",
            "top_protocol_revision",
            "workflow_stage",
            "human_approver_id",
            "plan_revision",
            "delivery_policy_digest",
            "reviewed_commit_sha",
            "target_branch",
            "target_base_sha",
            "default_branch",
            "default_base_sha",
            "requirement_pr_enabled",
            "remote_configured",
            "final_approval_gate_state",
        ],
    }
    event = {
        "issue_id": "R-1",
        "author_type": "member",
        "author_id": "human-1",
        "comment_id": "approval-1",
        "revision": 2,
        "command": "APPROVE REQUIREMENT v2",
    }
    delivery = {
        "state": "complete",
        "mode": mode,
        "plan_revision": 2,
        "delivery_policy_digest": POLICY_DIGEST,
        "reviewed_commit_sha": REVIEWED_SHA,
        "current_requirement_head_sha": REVIEWED_SHA,
        "target_branch": target_branch,
        "verified_target_base_sha": TARGET_BASE_SHA,
        "verified_default_base_sha": DEFAULT_BASE_SHA,
        "merge_method": "merge_commit" if requirement_pr_enabled else "local_no_ff",
        "merged_commit_sha": MERGED_SHA,
        "merge_parent_target_sha": TARGET_BASE_SHA,
        "merge_parent_requirement_sha": REVIEWED_SHA,
        "reviewed_tree_sha": TREE_SHA,
        "merged_tree_sha": TREE_SHA,
        "local_target_sha": MERGED_SHA,
    }
    if mode == "requirement_pr":
        delivery.update(
            {
                "pr_merged": True,
                "pr_url": "https://github.com/example/project/pull/7",
                "pr_number": 7,
                "required_checks_passed": True,
                "pr_base_branch": target_branch,
                "pr_head_sha": REVIEWED_SHA,
                "pr_merge_commit_sha": MERGED_SHA,
                "remote_target_sha": MERGED_SHA,
            }
        )
    elif mode == "direct_push":
        delivery.update(
            {
                "push_completed": True,
                "remote_name": "origin",
                "remote_verified": True,
                "remote_target_sha": MERGED_SHA,
            }
        )
    else:
        delivery["push_completed"] = False
    return {
        "actor_role": "leader",
        "root": root,
        "event": event,
        "delivery": delivery,
    }


def apply_transition(snapshot: dict, result: dict) -> dict:
    updated = copy.deepcopy(snapshot)
    updated["root"].update(result["metadata_updates"])
    metadata_keys = set(updated["root"].get("metadata_keys", []))
    metadata_keys.update(result["metadata_updates"])
    updated["root"]["metadata_keys"] = sorted(metadata_keys)
    if result["status_write"]:
        updated["root"]["status"] = result["status_write"]
    return updated


def open_and_approve(snapshot: dict) -> dict:
    opened = delivery_policy.final_gate_transition(snapshot, "open")
    assert opened["allowed"]
    snapshot = apply_transition(snapshot, opened)
    snapshot["actor_role"] = "integrator"
    approved = delivery_policy.final_gate_transition(snapshot, "approve")
    assert approved["allowed"]
    return apply_transition(snapshot, approved)


def apply_handoff(snapshot: dict, outcome: str = "queued") -> dict:
    snapshot["actor_role"] = "integrator"
    snapshot["handoff"] = {
        "issue_id": "R-1",
        "comment_id": f"delivery-{outcome}",
        "mentioned_role": "leader",
        "trigger_outcomes": [
            {"recipient_role": "leader", "status": outcome}
        ],
    }
    handoff = delivery_policy.final_gate_transition(snapshot, "handoff")
    assert handoff["allowed"]
    return apply_transition(snapshot, handoff)


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return completed.stdout.strip()


def create_repo(parent: Path, remote: str | None = None) -> Path:
    repo = parent / "repo"
    repo.mkdir()
    run_git(repo, "init")
    run_git(repo, "config", "user.name", "Test User")
    run_git(repo, "config", "user.email", "test@example.invalid")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    run_git(repo, "add", "README.md")
    run_git(repo, "commit", "-m", "initial")
    if remote:
        run_git(repo, "remote", "add", "origin", remote)
    return repo


def legacy_policy_snapshot(snapshot: dict) -> dict:
    legacy = {
        key: copy.deepcopy(snapshot[key])
        for key in (
            "workflow_id",
            "policy_source",
            "policy_file",
            "project_policy",
            "capabilities",
            "effective",
            "selection_source",
        )
    }
    legacy["schema_version"] = 1
    legacy["capabilities"].pop("direct_target_push", None)
    legacy["policy_digest"] = delivery_policy.legacy_policy_digest(legacy)
    return legacy


def refresh_snapshot_record(snapshot: dict) -> None:
    snapshot["snapshot_record_digest"] = delivery_policy.snapshot_record_digest(
        snapshot
    )


def legacy_v2_policy_snapshot(repo: Path, **selections) -> dict:
    return delivery_policy.resolve_legacy_policy_snapshot(repo, **selections)


class DeliveryPolicyTests(unittest.TestCase):
    def test_schema_accepts_documented_project_policy(self):
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        document = {
            "schema_version": 1,
            "workflow_id": "development-delivery",
            "workspace_modes": {
                "allowed": ["branch_only", "lightweight", "isolated"],
                "default": "lightweight",
            },
            "task_pr": {"constraint": "optional", "default": False},
            "requirement_pr": {"constraint": "optional", "default": True},
            "remote": {
                "name": "origin",
                "provider": "auto",
                "allow_direct_default_push": False,
            },
        }
        Draft202012Validator(schema).validate(document)
        self.assertEqual(
            delivery_policy.normalize_policy(document)["workspace_modes"]["default"],
            "lightweight",
        )
        invalid = dict(document)
        invalid["task_pr"] = {"constraint": "required", "default": False}
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(invalid)))
        with self.assertRaisesRegex(
            delivery_policy.DeliveryPolicyError,
            "must be true when required",
        ):
            delivery_policy.normalize_policy(invalid)

    def test_compact_plan_schema_allows_only_the_three_frozen_fields(self):
        schema = json.loads(PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))
        contract = {
            "plan_revision": 2,
            "policy_digest": "v3.sha256:" + "a" * 64,
            "target_branch": "release/v2",
        }
        Draft202012Validator(schema).validate(contract)
        for forbidden in (
            "design_digest",
            "review_comment_id",
            "approval_comment_id",
            "approval_author_id",
            "resolver_provenance",
            "policy_digest_schema_version",
            "snapshot_record_digest",
            "project_policy",
            "capabilities",
            "effective",
            "selection_source",
        ):
            invalid = dict(contract)
            invalid[forbidden] = {}
            with self.subTest(forbidden=forbidden):
                self.assertTrue(
                    list(Draft202012Validator(schema).iter_errors(invalid))
                )

    def test_plan_revision_rules_invalidate_material_or_policy_contract_changes(self):
        approved = {
            "plan_revision": 2,
            "policy_digest": "v3.sha256:" + "a" * 64,
            "target_branch": "main",
        }
        unchanged = delivery_policy.plan_contract_requires_revision(
            approved, dict(approved)
        )
        self.assertTrue(unchanged["valid"])
        self.assertFalse(unchanged["requires_new_revision"])

        for label, update, design_changed in (
            ("design", {}, True),
            ("policy", {"policy_digest": "v3.sha256:" + "b" * 64}, False),
            ("target", {"target_branch": "release/v2"}, False),
        ):
            proposed = dict(approved)
            proposed.update(update)
            stale = delivery_policy.plan_contract_requires_revision(
                approved, proposed, design_changed=design_changed
            )
            proposed["plan_revision"] = 3
            current = delivery_policy.plan_contract_requires_revision(
                approved, proposed, design_changed=design_changed
            )
            with self.subTest(label=label):
                self.assertFalse(stale["valid"])
                self.assertTrue(stale["requires_plan_review"])
                self.assertTrue(stale["requires_plan_approval"])
                self.assertTrue(current["valid"])

    def test_resolver_only_change_with_same_digest_keeps_plan_revision(self):
        contract = {
            "plan_revision": 2,
            "policy_digest": "v3.sha256:" + "a" * 64,
            "target_branch": "main",
        }
        result = delivery_policy.plan_contract_requires_revision(
            contract, dict(contract), design_changed=False
        )
        self.assertTrue(result["valid"])
        self.assertFalse(result["requires_new_revision"])

    def test_unconfigured_github_repository_uses_new_defaults(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            result = delivery_policy.resolve_policy(repo)
        self.assertEqual(result["effective"]["workspace_mode"], "lightweight")
        self.assertFalse(result["effective"]["task_pr"])
        self.assertTrue(result["effective"]["requirement_pr"])
        self.assertFalse(result["effective"]["parallel_tasks"])
        self.assertEqual(result["capabilities"]["remote_provider"], "github")
        self.assertNotIn("github.com/example", json.dumps(result))

    def test_repository_without_remote_disables_both_prs(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp))
            result = delivery_policy.resolve_policy(repo)
        self.assertFalse(result["effective"]["task_pr"])
        self.assertFalse(result["effective"]["requirement_pr"])
        self.assertFalse(result["capabilities"]["remote_configured"])
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp))
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError,
                "requires a supported PR remote",
            ):
                delivery_policy.resolve_policy(repo, requirement_pr=True)

    def test_read_only_preflight_accepts_real_terminal_legacy_shapes_together(self):
        groups = [
            terminal_lease_group(
                "R-41", "released", "held_by_integrator", "41"
            ),
            terminal_lease_group("R-55", "integrator", "integrator", "55"),
            terminal_lease_group(
                "R-70",
                "released",
                "released",
                "70",
                metadata_key_count=50,
            ),
        ]
        result = delivery_policy.lease_transition(
            lease_snapshot(groups), "acquire-preflight"
        )
        self.assertTrue(result["valid"])
        self.assertTrue(result["acquisition_allowed"])
        self.assertTrue(result["read_only"])
        self.assertEqual(
            [item["state"] for item in result["groups"]],
            ["retired_terminal_compatible"] * 3,
        )
        for forbidden in [
            "ordered_writes",
            "status_writes",
            "transition_record",
            "metadata_updates",
            "recovery",
        ]:
            self.assertNotIn(forbidden, result)

    def test_preflight_accepts_canonical_groups_and_ignores_task_residue(self):
        snapshot = lease_snapshot()
        result = delivery_policy.lease_transition(snapshot, "acquire-preflight")
        self.assertTrue(result["valid"])
        self.assertEqual(result["groups"][0]["state"], "canonical")
        self.assertEqual(len(snapshot["discovered_issue_inventory"]), 3)

    def test_preflight_accepts_a_mixed_canonical_and_retired_endpoint(self):
        group = terminal_lease_group(
            "R-41", "canonical", "held_by_integrator", "41"
        )
        result = delivery_policy.lease_transition(
            lease_snapshot([group]), "acquire-preflight"
        )
        self.assertTrue(result["valid"])
        self.assertEqual(
            result["groups"][0]["state"], "retired_terminal_compatible"
        )

    def test_terminal_legacy_preflight_rejects_unsafe_tuple_or_holder(self):
        cases = {
            "held_lease_not_terminal": lambda group: group["authority"].update(
                {"workspace_lease_state": "held"}
            ),
            "unknown_lease_state": lambda group: group["mirror"].update(
                {"workspace_lease_state": "held_by_unknown"}
            ),
            "legacy_owner_missing": lambda group: group["authority"].update(
                {"workspace_lease_owner_agent_id": ""}
            ),
            "legacy_owner_inconsistent": lambda group: group["mirror"].update(
                {"workspace_lease_owner_issue_id": "OTHER-HOLDER"}
            ),
            "active_legacy_lease": lambda group: group["historical_holder"].update(
                {"active": True, "status": "in_progress"}
            ),
        }
        for expected, mutation in cases.items():
            with self.subTest(expected=expected):
                group = terminal_lease_group(
                    "R-41", "released", "held_by_integrator", "41"
                )
                mutation(group)
                snapshot = lease_snapshot([group])
                result = delivery_policy.lease_transition(
                    snapshot, "acquire-preflight"
                )
                self.assertFalse(result["valid"])
                self.assertIn(expected, result["blocker_reasons"])

    def test_terminal_legacy_preflight_rejects_workspace_or_evidence_drift(self):
        cases = {
            "git_state_unsafe_dirty": lambda group: group["git"].update(
                {"clean": False}
            ),
            "git_state_unsafe_missing": lambda group: group["git"].update(
                {"exists": False}
            ),
            "git_state_unsafe_branch": lambda group: group["git"].update(
                {"branch": "wrong"}
            ),
            "git_state_unsafe_head": lambda group: group["git"].update(
                {"head_sha": "f" * 40}
            ),
            "git_state_unsafe_worktree": lambda group: group["git"].update(
                {"worktree_id": "wrong"}
            ),
            "delivery_evidence_drift": lambda group: group["root"].update(
                {"delivery_evidence_current": False}
            ),
            "pending_review": lambda group: group["root"].update(
                {"review_evidence_current": False}
            ),
            "approval_gate_open": lambda group: group["root"].update(
                {"open_approval_gates": ["final"]}
            ),
            "active_child_present": lambda group: group["root"].update(
                {"active_child_issue_ids": ["TASK-ACTIVE"]}
            ),
            "plan_binding_drift": lambda group: group["root"].update(
                {"current_plan_revision": 6}
            ),
        }
        for name, mutation in cases.items():
            with self.subTest(name=name):
                group = terminal_lease_group(
                    "R-41", "released", "held_by_integrator", "41"
                )
                mutation(group)
                snapshot = lease_snapshot([group])
                result = delivery_policy.lease_transition(
                    snapshot, "acquire-preflight"
                )
                self.assertFalse(result["valid"])
                self.assertTrue(result["blocker_reasons"])

    def test_terminal_lease_preflight_requires_complete_domain_and_no_claims(self):
        incomplete = lease_snapshot(
            [terminal_lease_group("R-55", "integrator", "integrator", "55")]
        )
        incomplete["endpoint_inventory"].pop()
        blocked = delivery_policy.lease_transition(
            incomplete, "acquire-preflight"
        )
        self.assertIn("incomplete_lease_inventory", blocked["blocker_reasons"])

        claims_unknown = lease_snapshot(
            [terminal_lease_group("R-55", "integrator", "integrator", "55")]
        )
        claims_unknown["current_claims_complete"] = False
        blocked = delivery_policy.lease_transition(
            claims_unknown, "acquire-preflight"
        )
        self.assertIn("incomplete_lease_inventory", blocked["blocker_reasons"])

        duplicate = lease_snapshot(
            [terminal_lease_group("R-55", "integrator", "integrator", "55")]
        )
        duplicate["terminal_groups"][0]["mirror"]["endpoint_role"] = (
            "implementation_authority"
        )
        refresh_lease_inventory(duplicate)
        blocked = delivery_policy.lease_transition(duplicate, "acquire-preflight")
        self.assertIn("incomplete_lease_inventory", blocked["blocker_reasons"])

        missing_root = lease_snapshot(
            [terminal_lease_group("R-55", "integrator", "integrator", "55")]
        )
        missing_root["discovered_requirement_inventory"] = []
        blocked = delivery_policy.lease_transition(
            missing_root, "acquire-preflight"
        )
        self.assertIn("incomplete_lease_inventory", blocked["blocker_reasons"])

        forged_role = lease_snapshot(
            [terminal_lease_group("R-55", "integrator", "integrator", "55")]
        )
        forged_role["discovered_issue_inventory"][0]["workflow_object_type"] = (
            "development_task"
        )
        blocked = delivery_policy.lease_transition(forged_role, "acquire-preflight")
        self.assertIn("unknown_lease_authority_role", blocked["blocker_reasons"])

        cross_instance = lease_snapshot(
            [terminal_lease_group("R-55", "integrator", "integrator", "55")]
        )
        cross_instance["terminal_groups"][0]["root"]["workflow_instance_id"] = (
            "other-workspace"
        )
        blocked = delivery_policy.lease_transition(
            cross_instance, "acquire-preflight"
        )
        self.assertIn("workflow_instance_mismatch", blocked["blocker_reasons"])

        claimed = lease_snapshot(
            [terminal_lease_group("R-55", "integrator", "integrator", "55")]
        )
        claimed["current_claims"] = [
            {"root_requirement_id": "R-NEW", "workspace_lease_state": "held"}
        ]
        blocked = delivery_policy.lease_transition(claimed, "acquire-preflight")
        self.assertIn("competing_lease_claim", blocked["blocker_reasons"])

    def test_terminal_lease_preflight_rejects_isolated_and_unsupported_actions(self):
        isolated = lease_snapshot(
            [
                terminal_lease_group(
                    "R-55",
                    "integrator",
                    "integrator",
                    "55",
                    workspace_mode="isolated",
                )
            ]
        )
        blocked = delivery_policy.lease_transition(isolated, "acquire-preflight")
        self.assertIn("workspace_mode_not_eligible", blocked["blocker_reasons"])
        with self.assertRaisesRegex(
            delivery_policy.DeliveryPolicyError, "unsupported lease transition action"
        ):
            delivery_policy.lease_transition(isolated, "normalize-legacy-terminal")

    def test_new_lease_writer_only_emits_canonical_held_or_released(self):
        self.assertEqual(
            delivery_policy.canonical_lease_tuple("held", "TASK-1", "agent-1"),
            {
                "workspace_lease_state": "held",
                "workspace_lease_owner_issue_id": "TASK-1",
                "workspace_lease_owner_agent_id": "agent-1",
            },
        )
        self.assertEqual(
            delivery_policy.canonical_lease_tuple("released"),
            delivery_policy.CANONICAL_RELEASED_LEASE,
        )
        for args in [
            ("integrator", "TASK-1", "agent-1"),
            ("held", "", ""),
            ("released", "TASK-1", "agent-1"),
        ]:
            with self.subTest(args=args):
                with self.assertRaises(delivery_policy.DeliveryPolicyError):
                    delivery_policy.canonical_lease_tuple(*args)

    def test_remote_resolution_blocks_ambiguity_and_accepts_configured_name(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp))
            run_git(
                repo,
                "remote",
                "add",
                "upstream",
                "https://github.com/example/upstream.git",
            )
            run_git(
                repo,
                "remote",
                "add",
                "backup",
                "https://github.com/example/backup.git",
            )
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError,
                "multiple remotes",
            ):
                delivery_policy.resolve_policy(repo)
            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "remote": {"name": "upstream"},
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            result = delivery_policy.resolve_policy(repo)
        self.assertEqual(result["capabilities"]["remote_name"], "upstream")

    def test_task_and_requirement_pr_are_independent(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "git@github.com:example/project.git")
            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "remote": {"allow_direct_default_push": True},
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            result = delivery_policy.resolve_policy(
                repo,
                workspace_mode="isolated",
                task_pr=True,
                requirement_pr=False,
            )
        self.assertTrue(result["effective"]["task_pr"])
        self.assertFalse(result["effective"]["requirement_pr"])
        self.assertTrue(result["effective"]["parallel_tasks"])

    def test_unsupported_remote_requires_explicit_direct_delivery_policy(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://gitlab.example/project.git")
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError,
                "allow_direct_default_push=true",
            ):
                delivery_policy.resolve_policy(repo)
            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "requirement_pr": {"constraint": "forbidden", "default": False},
                "remote": {
                    "provider": "none",
                    "allow_direct_default_push": True,
                },
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            result = delivery_policy.resolve_policy(repo)
        self.assertFalse(result["effective"]["task_pr"])
        self.assertFalse(result["effective"]["requirement_pr"])
        self.assertTrue(result["capabilities"]["direct_target_push"])
        self.assertTrue(result["capabilities"]["direct_default_push"])

    def test_project_constraints_reject_disallowed_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "workspace_modes": {
                    "allowed": ["branch_only"],
                    "default": "branch_only",
                },
                "task_pr": {"constraint": "forbidden", "default": False},
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError, "workspace_mode is not allowed"
            ):
                delivery_policy.resolve_policy(repo, workspace_mode="isolated")
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError, "task_pr is forbidden"
            ):
                delivery_policy.resolve_policy(repo, task_pr=True)

    def test_plan_snapshot_detects_policy_change(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            snapshot = legacy_v2_policy_snapshot(repo)
            self.assertTrue(delivery_policy.verify_snapshot(repo, snapshot)["valid"])
            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "workspace_modes": {
                    "allowed": ["isolated"],
                    "default": "isolated",
                },
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            self.assertFalse(delivery_policy.verify_snapshot(repo, snapshot)["valid"])

    def test_plan_snapshot_rejects_tampered_content(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            snapshot = legacy_v2_policy_snapshot(repo)
            snapshot["effective"]["task_pr"] = True
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError,
                "does not match policy_digest",
            ):
                delivery_policy.verify_snapshot(repo, snapshot)

    def test_plan_snapshot_detects_remote_push_target_change(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            snapshot = legacy_v2_policy_snapshot(repo)
            run_git(
                repo,
                "remote",
                "set-url",
                "--push",
                "origin",
                "git@github.com:example/other.git",
            )
            self.assertFalse(delivery_policy.verify_snapshot(repo, snapshot)["valid"])

    def test_compact_policy_digest_is_stable_for_same_state(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            first = delivery_policy.resolve_policy(repo)
            second = delivery_policy.resolve_policy(repo)
            verified = delivery_policy.verify_approved_digest(
                repo, first["policy_digest"]
            )
        self.assertTrue(first["policy_digest"].startswith("v3.sha256:"))
        self.assertEqual(first["policy_digest"], second["policy_digest"])
        self.assertNotIn("resolver_provenance", first)
        self.assertNotIn("policy_digest_schema_version", first)
        self.assertNotIn("snapshot_record_digest", first)
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["verification_outcome"], "exact_match")

    def test_compact_digest_ignores_nonbinding_fields_and_derives_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "remote": {"allow_direct_default_push": True},
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            resolved = delivery_policy.resolve_policy(
                repo,
                workspace_mode="isolated",
                task_pr=True,
                requirement_pr=False,
            )
            annotated = copy.deepcopy(resolved)
            annotated["selection_source"] = {"workspace_mode": "changed"}
            annotated["resolver_provenance"] = {"implementation": "changed"}
            annotated["diagnostics"] = {"message": "changed"}
            annotated["policy_source"] = "changed"
            annotated["policy_file"] = "changed.json"
            annotated["effective"]["parallel_tasks"] = False
            annotated["effective"]["workspace_lease_scope"] = "changed"
            verified = delivery_policy.verify_approved_digest(
                repo, resolved["policy_digest"]
            )
            projection = delivery_policy.compact_policy_projection(resolved)
        self.assertEqual(
            delivery_policy.compact_policy_digest(resolved),
            delivery_policy.compact_policy_digest(annotated),
        )
        self.assertNotIn(
            "allow_direct_default_push", projection["project_policy"]["remote"]
        )
        self.assertTrue(
            projection["project_policy"]["remote"]["allow_direct_target_push"]
        )
        self.assertNotIn("direct_default_push", projection["selected_remote"])
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["workspace_mode"], "isolated")
        self.assertTrue(verified["task_pr_enabled"])
        self.assertFalse(verified["requirement_pr_enabled"])
        self.assertTrue(verified["parallel_tasks"])
        self.assertEqual(verified["workspace_lease_scope"], "task")

    def test_verify_approved_recovers_non_default_selection_uniquely(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "remote": {"allow_direct_default_push": True},
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            approved = delivery_policy.resolve_policy(
                repo,
                workspace_mode="branch_only",
                task_pr=True,
                requirement_pr=False,
            )
            verified = delivery_policy.verify_approved_digest(
                repo, approved["policy_digest"]
            )
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["workspace_mode"], "branch_only")
        self.assertTrue(verified["task_pr_enabled"])
        self.assertFalse(verified["requirement_pr_enabled"])
        self.assertFalse(verified["parallel_tasks"])
        self.assertEqual(verified["workspace_lease_scope"], "repository")

    def test_verify_approved_detects_drift_and_rejects_legacy_or_future_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            approved = delivery_policy.resolve_policy(repo)
            run_git(
                repo,
                "remote",
                "set-url",
                "--push",
                "origin",
                "git@github.com:example/other.git",
            )
            drifted = delivery_policy.verify_approved_digest(
                repo, approved["policy_digest"]
            )
            self.assertFalse(drifted["valid"])
            self.assertTrue(drifted["requires_plan_revision"])
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError,
                "legacy policy digests require verify --snapshot",
            ):
                delivery_policy.verify_approved_digest(repo, "a" * 64)
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError,
                "unsupported schema",
            ):
                delivery_policy.verify_approved_digest(
                    repo, "v4.sha256:" + "a" * 64
                )

    def test_verify_approved_rejects_an_ambiguous_digest_match(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            collided = "v3.sha256:" + "a" * 64
            with mock.patch.object(
                delivery_policy, "compact_policy_digest", return_value=collided
            ):
                with self.assertRaisesRegex(
                    delivery_policy.DeliveryPolicyError,
                    "matches multiple effective selections",
                ):
                    delivery_policy.verify_approved_digest(repo, collided)

    def test_verify_approved_cli_reports_non_default_selection(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            approved = delivery_policy.resolve_policy(
                repo, workspace_mode="isolated", task_pr=True
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(POLICY_PATH),
                    "verify-approved",
                    "--repo",
                    str(repo),
                    "--policy-digest",
                    approved["policy_digest"],
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(completed.returncode, 0)
        result = json.loads(completed.stdout)
        self.assertEqual(result["workspace_mode"], "isolated")
        self.assertTrue(result["task_pr_enabled"])

    def test_direct_target_alias_upgrade_requires_audited_digest_supersession(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "requirement_pr": {"constraint": "forbidden", "default": False},
                "remote": {"allow_direct_default_push": True},
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            current = legacy_v2_policy_snapshot(repo)
            frozen = legacy_policy_snapshot(current)
            legacy_current_digest = delivery_policy.legacy_policy_digest(current)

            proposed = delivery_policy.verify_snapshot(repo, frozen)
            self.assertFalse(proposed["valid"])
            self.assertTrue(proposed["semantically_equivalent"])
            self.assertTrue(proposed["recovery_required"])
            self.assertFalse(proposed["requires_plan_revision"])
            self.assertEqual(
                proposed["policy_digest_to_propagate"], frozen["policy_digest"]
            )
            self.assertNotEqual(frozen["policy_digest"], legacy_current_digest)
            self.assertEqual(
                proposed["superseded_policy_digests"][0]["policy_digest"],
                legacy_current_digest,
            )

            accepted = delivery_policy.verify_snapshot(
                repo,
                frozen,
                recovery_record=proposed["policy_digest_recovery_record"],
            )
        self.assertTrue(accepted["valid"])
        self.assertEqual(accepted["verification_outcome"], "pinned_equivalent")
        self.assertEqual(
            accepted["policy_digest_to_propagate"], frozen["policy_digest"]
        )
        recovery = delivery_policy.decode_metadata_record(
            accepted["policy_digest_recovery_record"]
        )
        self.assertEqual(recovery["pinned_policy_digest"], frozen["policy_digest"])
        self.assertEqual(recovery["resolved_policy_digest"], current["policy_digest"])
        self.assertEqual(
            recovery["frozen_resolver_provenance"]["provenance_status"],
            "legacy_undeclared",
        )

    def test_digest_schema_rollback_uses_the_same_explicit_pinning_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            current = legacy_v2_policy_snapshot(repo)
            rolled_back = legacy_policy_snapshot(current)
        proposed = delivery_policy.verify_resolved_snapshots(current, rolled_back)
        self.assertFalse(proposed["valid"])
        self.assertTrue(proposed["recovery_required"])
        accepted = delivery_policy.verify_resolved_snapshots(
            current,
            rolled_back,
            proposed["policy_digest_recovery_record"],
        )
        self.assertTrue(accepted["valid"])
        self.assertEqual(accepted["verification_outcome"], "pinned_equivalent")
        self.assertEqual(
            accepted["policy_digest_to_propagate"], current["policy_digest"]
        )

    def test_dev5_frozen_digest_preserves_the_pre_alias_digest_as_superseded(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "requirement_pr": {"constraint": "forbidden", "default": False},
                "remote": {"allow_direct_default_push": True},
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            current = legacy_v2_policy_snapshot(repo)
            frozen = legacy_policy_snapshot(current)
            frozen["capabilities"]["direct_target_push"] = frozen[
                "capabilities"
            ]["direct_default_push"]
            frozen["policy_digest"] = delivery_policy.legacy_policy_digest(frozen)
            predecessor = legacy_policy_snapshot(current)
            proposed = delivery_policy.verify_snapshot(repo, frozen)
        self.assertEqual(
            proposed["superseded_policy_digests"][0]["policy_digest"],
            predecessor["policy_digest"],
        )
        self.assertEqual(
            proposed["policy_digest_to_propagate"], frozen["policy_digest"]
        )

    def test_nonsemantic_resolver_fields_do_not_drift_policy_digest(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            frozen = legacy_v2_policy_snapshot(repo)
        annotated = copy.deepcopy(frozen)
        annotated["capabilities"]["resolver_annotation"] = {
            "diagnostic": "new output field"
        }
        annotated["resolver_provenance"]["build_annotation"] = "packaged"
        refresh_snapshot_record(annotated)
        verified = delivery_policy.verify_resolved_snapshots(frozen, annotated)
        self.assertEqual(frozen["policy_digest"], annotated["policy_digest"])
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["verification_outcome"], "exact_match")

    def test_real_selection_and_project_policy_changes_require_new_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            frozen = legacy_v2_policy_snapshot(repo)
            changed_selection = legacy_v2_policy_snapshot(
                repo, workspace_mode="isolated"
            )
            selection_result = delivery_policy.verify_resolved_snapshots(
                frozen, changed_selection
            )
            self.assertFalse(selection_result["valid"])
            self.assertTrue(selection_result["requires_plan_revision"])
            self.assertFalse(selection_result["semantically_equivalent"])

            policy = {
                "schema_version": 1,
                "workflow_id": "development-delivery",
                "task_pr": {"constraint": "required", "default": True},
            }
            (repo / "multica.delivery.json").write_text(
                json.dumps(policy), encoding="utf-8"
            )
            policy_result = delivery_policy.verify_snapshot(repo, frozen)
        self.assertFalse(policy_result["valid"])
        self.assertTrue(policy_result["requires_plan_revision"])
        self.assertEqual(policy_result["verification_outcome"], "semantic_drift")

    def test_remote_change_cannot_use_digest_recovery(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            frozen = legacy_v2_policy_snapshot(repo)
            run_git(
                repo,
                "remote",
                "set-url",
                "--push",
                "origin",
                "git@github.com:example/other.git",
            )
            result = delivery_policy.verify_snapshot(repo, frozen)
        self.assertFalse(result["valid"])
        self.assertFalse(result["recovery_required"])
        self.assertTrue(result["requires_plan_revision"])
        self.assertEqual(result["verification_outcome"], "semantic_drift")

    def test_recovery_record_is_bound_to_the_frozen_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            current = legacy_v2_policy_snapshot(repo)
            frozen = legacy_policy_snapshot(current)
            proposed = delivery_policy.verify_snapshot(repo, frozen)
            record = delivery_policy.decode_metadata_record(
                proposed["policy_digest_recovery_record"]
            )
            record["pinned_policy_digest"] = "0" * 64
            tampered = delivery_policy.encode_metadata_record(record)
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError, "was modified"
            ):
                delivery_policy.verify_snapshot(
                    repo, frozen, recovery_record=tampered
                )

    def test_verify_cli_requires_then_accepts_the_persisted_recovery_record(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            repo = create_repo(root, "https://github.com/example/project.git")
            current = legacy_v2_policy_snapshot(repo)
            frozen = legacy_policy_snapshot(current)
            snapshot_file = root / "snapshot.json"
            recovery_file = root / "recovery.json"
            snapshot_file.write_text(json.dumps(frozen), encoding="utf-8")
            command = [
                sys.executable,
                str(POLICY_PATH),
                "verify",
                "--repo",
                str(repo),
                "--snapshot",
                str(snapshot_file),
            ]
            proposed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(proposed.returncode, 1)
            proposed_result = json.loads(proposed.stdout)
            self.assertEqual(
                proposed_result["verification_outcome"], "recovery_required"
            )
            recovery_file.write_text(proposed.stdout, encoding="utf-8")
            accepted = subprocess.run(
                [*command, "--recovery-record", str(recovery_file)],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
        self.assertEqual(accepted.returncode, 0)
        self.assertEqual(
            json.loads(accepted.stdout)["verification_outcome"],
            "pinned_equivalent",
        )

    def test_guard_workspace_blocks_unknown_changes(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp))
            branch = run_git(repo, "symbolic-ref", "--short", "HEAD")
            head = run_git(repo, "rev-parse", "HEAD")
            result = delivery_policy.guard_workspace(
                repo,
                workspace_mode="branch_only",
                expected_branch=branch,
                expected_head=head,
            )
            self.assertTrue(result["valid"])
            (repo / "unknown.txt").write_text("user change\n", encoding="utf-8")
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError, "dirty"
            ):
                delivery_policy.guard_workspace(
                    repo,
                    workspace_mode="branch_only",
                    expected_branch=branch,
                    expected_head=head,
                )

    def test_final_gate_requires_done_implementation_before_root_review(self):
        snapshot = final_gate_snapshot()
        snapshot["root"]["implementation_status"] = "in_review"
        rejected = delivery_policy.final_gate_transition(snapshot, "open")
        self.assertFalse(rejected["allowed"])
        self.assertIn(
            "Implementation must be done before final approval",
            rejected["reasons"],
        )
        self.assertEqual(rejected["metadata_updates"], {})

        snapshot["root"]["implementation_status"] = "done"
        opened = delivery_policy.final_gate_transition(snapshot, "open")
        self.assertTrue(opened["allowed"])
        self.assertEqual(opened["status_write"], "in_review")
        self.assertEqual(
            opened["metadata_updates"]["final_approval_gate_state"], "open"
        )

    def test_early_and_child_approvals_are_rejected_without_metadata(self):
        snapshot = final_gate_snapshot()
        snapshot["actor_role"] = "integrator"
        early = delivery_policy.final_gate_transition(snapshot, "approve")
        self.assertFalse(early["allowed"])
        self.assertEqual(early["metadata_updates"], {})

        snapshot = final_gate_snapshot()
        opened = delivery_policy.final_gate_transition(snapshot, "open")
        snapshot = apply_transition(snapshot, opened)
        snapshot["actor_role"] = "integrator"
        snapshot["event"]["issue_id"] = "I-1"
        child = delivery_policy.final_gate_transition(snapshot, "approve")
        self.assertFalse(child["allowed"])
        self.assertIn(
            "approval comment must be posted on the top-level Requirement",
            child["reasons"],
        )
        self.assertEqual(child["metadata_updates"], {})

    def test_normal_top_level_approval_binds_current_gate_tuple(self):
        snapshot = final_gate_snapshot()
        opened = delivery_policy.final_gate_transition(snapshot, "open")
        snapshot = apply_transition(snapshot, opened)
        snapshot["actor_role"] = "integrator"
        approved = delivery_policy.final_gate_transition(snapshot, "approve")
        self.assertTrue(approved["allowed"])
        self.assertEqual(approved["outcome"], "approval_accepted")
        self.assertTrue(approved["merge_required"])
        self.assertEqual(
            approved["metadata_updates"]["approved_requirement_head_sha"],
            REVIEWED_SHA,
        )
        self.assertEqual(
            approved["metadata_updates"]["approved_delivery_policy_digest"],
            POLICY_DIGEST,
        )
        snapshot = apply_transition(snapshot, approved)
        snapshot["actor_role"] = "leader"
        repeated_open = delivery_policy.final_gate_transition(snapshot, "open")
        self.assertTrue(repeated_open["allowed"])
        self.assertEqual(repeated_open["outcome"], "already_accepted")
        self.assertEqual(repeated_open["metadata_updates"], {})

        invalid = final_gate_snapshot()
        invalid = apply_transition(
            invalid, delivery_policy.final_gate_transition(invalid, "open")
        )
        invalid["actor_role"] = "integrator"
        invalid["event"]["command"] = "APPROVE REQUIREMENT v1"
        rejected = delivery_policy.final_gate_transition(invalid, "approve")
        self.assertFalse(rejected["allowed"])
        self.assertEqual(rejected["metadata_updates"], {})

    def test_final_gate_retains_legacy_policy_digest_compatibility(self):
        snapshot = final_gate_snapshot()
        snapshot["root"]["delivery_policy_digest"] = "6" * 64
        snapshot["delivery"]["delivery_policy_digest"] = "6" * 64
        opened = delivery_policy.final_gate_transition(snapshot, "open")
        self.assertTrue(opened["allowed"])

    def test_delivery_modes_and_non_default_target_branch_converge(self):
        for mode in delivery_policy.DELIVERY_MODES:
            with self.subTest(mode=mode):
                snapshot = open_and_approve(final_gate_snapshot(mode))
                delivered = delivery_policy.final_gate_transition(snapshot, "delivery")
                self.assertTrue(delivered["allowed"])
                self.assertTrue(delivered["wake_leader"])
                self.assertIsNone(delivered["status_write"])
                record = delivery_policy.decode_metadata_record(
                    delivered["metadata_updates"]["delivery_evidence_record"]
                )
                self.assertEqual(
                    record["target_branch"],
                    "release/v2",
                )
                self.assertEqual(
                    record["merge_parent_requirement_sha"],
                    REVIEWED_SHA,
                )
                snapshot = apply_transition(snapshot, delivered)
                snapshot = apply_handoff(snapshot)
                snapshot["actor_role"] = "leader"
                converged = delivery_policy.final_gate_transition(
                    snapshot, "converge"
                )
                self.assertTrue(converged["allowed"])
                self.assertEqual(converged["status_write"], "done")

    def test_delivery_handoff_requires_confirmed_leader_routing(self):
        for outcome in delivery_policy.HANDOFF_OUTCOMES:
            with self.subTest(outcome=outcome):
                snapshot = open_and_approve(final_gate_snapshot())
                delivered = delivery_policy.final_gate_transition(snapshot, "delivery")
                snapshot = apply_transition(snapshot, delivered)
                snapshot["handoff"] = {
                    "issue_id": "R-1",
                    "comment_id": "delivery-comment",
                    "mentioned_role": "leader",
                    "trigger_outcomes": [
                        {"recipient_role": "leader", "status": outcome}
                    ],
                }
                handoff = delivery_policy.final_gate_transition(snapshot, "handoff")
                self.assertTrue(handoff["allowed"])
                record = delivery_policy.decode_metadata_record(
                    handoff["metadata_updates"]["delivery_handoff_record"]
                )
                self.assertEqual(
                    record["trigger_outcome"],
                    outcome,
                )

        snapshot["handoff"]["trigger_outcomes"] = []
        missing = delivery_policy.final_gate_transition(snapshot, "handoff")
        self.assertFalse(missing["allowed"])
        self.assertTrue(missing["retry_required"])

    def test_duplicate_approval_never_remerges_and_recovers_lost_wake(self):
        pending = open_and_approve(final_gate_snapshot())
        pending["delivery"]["state"] = "pending"
        pending["event"]["comment_id"] = "approval-pending-duplicate"
        resume = delivery_policy.final_gate_transition(pending, "approve")
        self.assertTrue(resume["allowed"])
        self.assertFalse(resume["merge_required"])
        self.assertTrue(resume["resume_delivery"])
        self.assertFalse(resume["wake_leader"])

        snapshot = open_and_approve(final_gate_snapshot())
        delivered = delivery_policy.final_gate_transition(snapshot, "delivery")
        snapshot = apply_transition(snapshot, delivered)
        snapshot["event"]["comment_id"] = "approval-duplicate"
        duplicate = delivery_policy.final_gate_transition(snapshot, "approve")
        self.assertTrue(duplicate["allowed"])
        self.assertEqual(duplicate["outcome"], "duplicate_approval")
        self.assertFalse(duplicate["merge_required"])
        self.assertFalse(duplicate["resume_delivery"])
        self.assertTrue(duplicate["wake_leader"])
        self.assertEqual(duplicate["metadata_updates"], {})

    def test_stale_or_missing_terminal_evidence_blocks_completion(self):
        cases = {
            "dependency": ("root", "dependencies_satisfied", False),
            "tests": ("root", "tests_passed", False),
            "reviewed head": (
                "delivery",
                "current_requirement_head_sha",
                "7" * 40,
            ),
            "policy digest": (
                "delivery",
                "delivery_policy_digest",
                "8" * 64,
            ),
            "target baseline": (
                "delivery",
                "verified_target_base_sha",
                "9" * 40,
            ),
            "merge evidence": ("delivery", "merged_commit_sha", None),
        }
        for name, (section, key, value) in cases.items():
            with self.subTest(name=name):
                snapshot = open_and_approve(final_gate_snapshot())
                snapshot[section][key] = value
                result = delivery_policy.final_gate_transition(snapshot, "delivery")
                self.assertFalse(result["allowed"])
                self.assertEqual(result["metadata_updates"], {})

    def test_revision_review_and_policy_drift_invalidate_approval_gate(self):
        cases = {
            "revision": ("plan_revision", 3),
            "reviewed head": ("reviewed_commit_sha", "a" * 40),
            "policy digest": ("delivery_policy_digest", "b" * 64),
        }
        for name, (key, value) in cases.items():
            with self.subTest(name=name):
                snapshot = final_gate_snapshot()
                opened = delivery_policy.final_gate_transition(snapshot, "open")
                snapshot = apply_transition(snapshot, opened)
                snapshot["root"][key] = value
                snapshot["actor_role"] = "integrator"
                if key == "plan_revision":
                    snapshot["event"]["revision"] = value
                result = delivery_policy.final_gate_transition(snapshot, "approve")
                self.assertFalse(result["allowed"])
                self.assertEqual(result["metadata_updates"], {})

    def test_accepted_approval_expires_after_root_tuple_drift(self):
        cases = {
            "revision": ("plan_revision", 3),
            "reviewed head": ("reviewed_commit_sha", "c" * 40),
            "policy digest": ("delivery_policy_digest", "d" * 64),
        }
        for name, (key, value) in cases.items():
            with self.subTest(name=name):
                snapshot = open_and_approve(final_gate_snapshot())
                snapshot["root"][key] = value
                result = delivery_policy.final_gate_transition(snapshot, "delivery")
                self.assertFalse(result["allowed"])
                self.assertIn(
                    "final approval gate is not accepted",
                    result["reasons"],
                )
                self.assertEqual(result["metadata_updates"], {})

    def test_done_requirement_is_idempotent_and_never_reopened(self):
        snapshot = open_and_approve(final_gate_snapshot())
        delivered = delivery_policy.final_gate_transition(snapshot, "delivery")
        snapshot = apply_transition(snapshot, delivered)
        snapshot = apply_handoff(snapshot)
        snapshot["actor_role"] = "leader"
        completed = delivery_policy.final_gate_transition(snapshot, "converge")
        snapshot = apply_transition(snapshot, completed)
        self.assertEqual(snapshot["root"]["status"], "done")

        for action in delivery_policy.FINAL_ACTIONS:
            with self.subTest(action=action):
                result = delivery_policy.final_gate_transition(snapshot, action)
                self.assertTrue(result["allowed"])
                self.assertEqual(result["outcome"], "already_done")
                self.assertIsNone(result["status_write"])
                self.assertFalse(result["merge_required"])

    def test_compact_terminal_records_preserve_metadata_capacity(self):
        metadata = {f"existing_{index}": index for index in range(38)}
        snapshot = final_gate_snapshot()
        snapshot["root"]["metadata_keys"] = list(metadata)
        opened = delivery_policy.final_gate_transition(snapshot, "open")
        metadata.update(opened["metadata_updates"])
        snapshot = apply_transition(snapshot, opened)
        snapshot["actor_role"] = "integrator"
        approved = delivery_policy.final_gate_transition(snapshot, "approve")
        metadata.update(approved["metadata_updates"])
        snapshot = apply_transition(snapshot, approved)
        delivered = delivery_policy.final_gate_transition(snapshot, "delivery")
        metadata.update(delivered["metadata_updates"])
        snapshot = apply_transition(snapshot, delivered)
        snapshot["handoff"] = {
            "issue_id": "R-1",
            "comment_id": "capacity-handoff",
            "mentioned_role": "leader",
            "trigger_outcomes": [
                {"recipient_role": "leader", "status": "queued"}
            ],
        }
        handoff = delivery_policy.final_gate_transition(snapshot, "handoff")
        metadata.update(handoff["metadata_updates"])
        snapshot = apply_transition(snapshot, handoff)

        self.assertLessEqual(len(metadata), 50)
        self.assertLessEqual(len(snapshot["root"]["metadata_keys"]), 50)
        self.assertIsInstance(metadata["delivery_evidence_record"], str)
        self.assertIsInstance(metadata["delivery_handoff_record"], str)
        self.assertRegex(
            metadata["delivery_evidence_record"], r"^v1\.[A-Za-z0-9_-]+$"
        )
        self.assertRegex(
            metadata["delivery_handoff_record"], r"^v1\.[A-Za-z0-9_-]+$"
        )
        self.assertEqual(
            delivery_policy.decode_metadata_record(
                metadata["delivery_evidence_record"]
            )["merged_commit_sha"],
            MERGED_SHA,
        )
        self.assertEqual(
            delivery_policy.decode_metadata_record(
                metadata["delivery_handoff_record"]
            )["plan_revision"],
            2,
        )

    def test_metadata_capacity_is_rejected_before_partial_writes(self):
        snapshot = final_gate_snapshot()
        snapshot["root"]["metadata_keys"] = [
            f"existing_{index}" for index in range(48)
        ]
        result = delivery_policy.final_gate_transition(snapshot, "open")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["metadata_updates"], {})
        self.assertIn(
            "metadata updates exceed the platform 50-key limit (48 current, 52 projected)",
            result["reasons"],
        )

    def test_convergence_requires_current_compact_delivery_and_handoff(self):
        snapshot = open_and_approve(final_gate_snapshot())
        delivered = delivery_policy.final_gate_transition(snapshot, "delivery")
        snapshot = apply_transition(snapshot, delivered)
        snapshot["actor_role"] = "leader"
        missing_handoff = delivery_policy.final_gate_transition(
            snapshot, "converge"
        )
        self.assertFalse(missing_handoff["allowed"])
        self.assertIn(
            "delivery_handoff_record is not a versioned metadata record",
            missing_handoff["reasons"],
        )

        snapshot = apply_handoff(snapshot)
        snapshot["actor_role"] = "leader"
        snapshot["delivery"]["merged_commit_sha"] = "7" * 40
        snapshot["delivery"]["local_target_sha"] = "7" * 40
        snapshot["delivery"]["remote_target_sha"] = "7" * 40
        drifted = delivery_policy.final_gate_transition(snapshot, "converge")
        self.assertFalse(drifted["allowed"])
        self.assertIn(
            "recorded delivery evidence does not match current delivery evidence",
            drifted["reasons"],
        )

        snapshot["actor_role"] = "integrator"
        refreshed_delivery = delivery_policy.final_gate_transition(
            snapshot, "delivery"
        )
        self.assertTrue(refreshed_delivery["allowed"])
        snapshot = apply_transition(snapshot, refreshed_delivery)
        snapshot["actor_role"] = "leader"
        stale_handoff = delivery_policy.final_gate_transition(
            snapshot, "converge"
        )
        self.assertFalse(stale_handoff["allowed"])
        self.assertIn(
            "recorded handoff does not match current delivery evidence",
            stale_handoff["reasons"],
        )

        snapshot["root"]["delivery_evidence_record"] = "v1.not+base64"
        corrupted = delivery_policy.final_gate_transition(snapshot, "converge")
        self.assertFalse(corrupted["allowed"])
        self.assertIn("delivery_evidence_record is invalid", corrupted["reasons"])


if __name__ == "__main__":
    unittest.main()
