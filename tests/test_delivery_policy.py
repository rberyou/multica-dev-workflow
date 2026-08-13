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
LEASE_SCHEMA_PATH = (
    ROOT
    / "skills/multica-delivery-policy/references/lease-transition.schema.json"
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


def lease_group(root_id: str, state: str, suffix: str) -> dict:
    owner_issue = f"T-holder-{suffix}"
    owner_agent = "agent-integrator"
    workflow_instance = f"workflow-{suffix}"
    lease = {
        "workspace_lease_state": state,
        "workspace_lease_owner_issue_id": owner_issue,
        "workspace_lease_owner_agent_id": owner_agent,
    }
    endpoint_base = {
        "status": "done",
        "root_requirement_id": root_id,
        "workflow_instance_id": workflow_instance,
        "workspace_lease_scope": "requirement",
        "workspace_lease_transition_record": None,
        "metadata_total_bytes": 4096,
        "metadata_byte_limit": 8192,
        "metadata_scalar_byte_limit": 8192,
        **lease,
    }
    return {
        "root": {
            "issue_id": root_id,
            "status": "done",
            "workflow_instance_id": workflow_instance,
            "workspace_mode": "lightweight",
            "plan_schema_version": 3,
            "plan_revision": 2,
            "current_plan_revision": 2,
            "approved_policy_digest": POLICY_DIGEST,
            "current_policy_digest": POLICY_DIGEST,
            "target_branch": f"req/{root_id}-completed",
            "current_target_branch": f"req/{root_id}-completed",
            "plan_frozen_fields": [
                "plan_revision",
                "policy_digest",
                "target_branch",
            ],
            "active_child_issue_ids": [],
            "pending_review_issue_ids": [],
            "open_approval_gates": [],
            "review_evidence_current": True,
            "approval_evidence_current": True,
            "merge_evidence_current": True,
            "delivery_evidence_current": True,
            "delivery_complete": True,
        },
        "git": {
            "clean": True,
            "unfinished_operations": [],
            "branch": f"req/{root_id}-completed",
            "expected_branch": f"req/{root_id}-completed",
            "head_sha": REVIEWED_SHA,
            "expected_head_sha": REVIEWED_SHA,
            "worktree_id": f"worktree-{suffix}",
            "expected_worktree_id": f"worktree-{suffix}",
        },
        "historical_holder": {
            "issue_id": owner_issue,
            "agent_id": owner_agent,
            "agent_role": state if state in {"developer", "reviewer", "integrator"} else "integrator",
            "status": "done",
            "active": False,
            "root_requirement_id": root_id,
            "workflow_instance_id": workflow_instance,
        },
        "authority": {
            "issue_id": f"I-authority-{suffix}",
            "endpoint_role": "implementation_authority",
            **endpoint_base,
        },
        "mirror": {
            "issue_id": f"I-mirror-{suffix}",
            "endpoint_role": "final_integration_validation_mirror",
            **endpoint_base,
        },
    }


def lease_snapshot(states=("integrator",)) -> dict:
    groups = [
        lease_group(f"R-{index + 1}", state, str(index + 1))
        for index, state in enumerate(states)
    ]
    endpoint_inventory = []
    for group in groups:
        endpoint_inventory.extend(
            [copy.deepcopy(group["authority"]), copy.deepcopy(group["mirror"])]
        )
    return {
        "schema_version": 2,
        "snapshot_read_id": "fresh-read-1",
        "inventory_selection_rule": (
            delivery_policy.LEASE_INVENTORY_SELECTION_RULE
        ),
        "inventory_complete": True,
        "requirement_root_ids": [group["root"]["issue_id"] for group in groups],
        "discovered_requirement_root_ids": [
            group["root"]["issue_id"] for group in groups
        ],
        "migration_groups": groups,
        "endpoint_inventory": endpoint_inventory,
        "discovered_issue_inventory": copy.deepcopy(endpoint_inventory),
        "current_claims": [],
    }


def apply_lease_write(snapshot: dict, result: dict) -> dict:
    updated = copy.deepcopy(snapshot)
    write = result["ordered_writes"][0]
    root_id = write["group_root_requirement_id"]
    group = next(
        item for item in updated["migration_groups"]
        if item["root"]["issue_id"] == root_id
    )
    endpoint = group[write["endpoint"]]
    assert write["expected_endpoint_digest"] == delivery_policy.digest(
        delivery_policy._lease_endpoint_projection(endpoint)
    )
    endpoint.update(write["metadata_updates"])
    if write["projected_metadata_total_bytes"] is not None:
        endpoint["metadata_total_bytes"] = write["projected_metadata_total_bytes"]
    for inventory_item in updated["endpoint_inventory"]:
        if inventory_item["issue_id"] == endpoint["issue_id"]:
            inventory_item.update(write["metadata_updates"])
            inventory_item["metadata_total_bytes"] = endpoint["metadata_total_bytes"]
    for inventory_item in updated["discovered_issue_inventory"]:
        if inventory_item["issue_id"] == endpoint["issue_id"]:
            inventory_item.update(write["metadata_updates"])
            inventory_item["metadata_total_bytes"] = endpoint["metadata_total_bytes"]
    current = int(updated["snapshot_read_id"].split("-")[-1])
    updated["snapshot_read_id"] = f"fresh-read-{current + 1}"
    return updated


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
    def test_lease_transition_schema_example_is_valid(self):
        schema = json.loads(LEASE_SCHEMA_PATH.read_text(encoding="utf-8"))
        example = json.loads(
            LEASE_SCHEMA_PATH.with_name("lease-transition.example.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator(schema).validate(example)

    def test_legacy_role_and_released_with_owner_normalize_resumably(self):
        for state, family in [
            ("developer", "role_state"),
            ("released", "released_with_owner"),
        ]:
            with self.subTest(state=state):
                snapshot = lease_snapshot((state,))
                preflight = delivery_policy.lease_transition(
                    snapshot, "acquire-preflight"
                )
                self.assertFalse(preflight["valid"])
                self.assertEqual(preflight["outcome"], "recovery_required")
                self.assertEqual(preflight["groups"][0]["legacy_family"], family)
                self.assertEqual(
                    preflight["blocker_reasons"],
                    ["legacy_terminal_normalization_required"],
                )

                expected_stages = [
                    "prepare_authority_checkpoint",
                    "prepare_mirror_checkpoint",
                    "normalize_mirror",
                    "normalize_authority",
                    "complete_authority_checkpoint",
                    "complete_mirror_checkpoint",
                ]
                records = []
                for sequence, expected_stage in enumerate(expected_stages, 1):
                    result = delivery_policy.lease_transition(
                        snapshot, "normalize-legacy-terminal"
                    )
                    self.assertTrue(result["valid"])
                    self.assertEqual(result["outcome"], "write_required")
                    self.assertEqual(result["resume_stage"], expected_stage)
                    self.assertEqual(result["ordered_writes"][0]["sequence"], sequence)
                    self.assertEqual(result["status_writes"], [])
                    records.append(result["transition_record"])
                    repeated = delivery_policy.lease_transition(
                        snapshot, "normalize-legacy-terminal"
                    )
                    self.assertEqual(result, repeated)
                    snapshot = apply_lease_write(snapshot, result)

                complete = delivery_policy.lease_transition(
                    snapshot, "normalize-legacy-terminal"
                )
                self.assertTrue(complete["valid"])
                self.assertEqual(complete["outcome"], "normalization_complete")
                self.assertEqual(complete["resume_stage"], "complete")
                group = snapshot["migration_groups"][0]
                self.assertEqual(group["authority"].get("status"), "done")
                self.assertEqual(group["mirror"].get("status"), "done")
                self.assertEqual(
                    delivery_policy._lease_tuple(group["authority"]),
                    delivery_policy.CANONICAL_RELEASED_LEASE,
                )
                self.assertEqual(records[:4], [records[0]] * 4)
                self.assertEqual(records[4:], [records[4]] * 2)
                self.assertNotEqual(records[0], records[4])
                allowed = delivery_policy.lease_transition(
                    snapshot, "acquire-preflight"
                )
                self.assertTrue(allowed["valid"])
                self.assertTrue(allowed["acquisition_allowed"])

    def test_lease_transition_fail_closed_boundaries(self):
        cases = []

        def add(code, mutate):
            cases.append((code, mutate))

        endpoints = lambda s: [s["migration_groups"][0]["authority"], s["migration_groups"][0]["mirror"], *s["endpoint_inventory"][:2]]
        add("held_lease_not_terminal", lambda s: [e.update(workspace_lease_state="held") for e in endpoints(s)])
        add("unknown_lease_state", lambda s: [e.update(workspace_lease_state="held_by_integrator") for e in endpoints(s)])
        add("legacy_owner_missing", lambda s: [e.update(workspace_lease_owner_agent_id="") for e in endpoints(s)])
        add("legacy_owner_inconsistent", lambda s: s["migration_groups"][0]["historical_holder"].update(agent_id="other-agent"))
        add("legacy_tuple_mismatch", lambda s: [s["migration_groups"][0]["mirror"].update(workspace_lease_owner_agent_id="other-agent"), s["endpoint_inventory"][1].update(workspace_lease_owner_agent_id="other-agent")])
        add("root_not_done", lambda s: s["migration_groups"][0]["root"].update(status="in_progress"))
        add("authority_not_done", lambda s: [s["migration_groups"][0]["authority"].update(status="in_progress"), s["endpoint_inventory"][0].update(status="in_progress")])
        add("mirror_not_done", lambda s: [s["migration_groups"][0]["mirror"].update(status="in_progress"), s["endpoint_inventory"][1].update(status="in_progress")])
        add("root_identity_mismatch", lambda s: [s["migration_groups"][0]["mirror"].update(root_requirement_id="R-other"), s["endpoint_inventory"][1].update(root_requirement_id="R-other")])
        add("workflow_instance_mismatch", lambda s: [s["migration_groups"][0]["mirror"].update(workflow_instance_id="other"), s["endpoint_inventory"][1].update(workflow_instance_id="other")])
        add("lease_scope_mismatch", lambda s: [s["migration_groups"][0]["authority"].update(workspace_lease_scope="repository"), s["endpoint_inventory"][0].update(workspace_lease_scope="repository")])
        add("isolated_mode_not_eligible", lambda s: s["migration_groups"][0]["root"].update(workspace_mode="isolated"))
        add("active_legacy_lease", lambda s: s["migration_groups"][0]["historical_holder"].update(status="in_progress", active=True))
        add("active_child_present", lambda s: s["migration_groups"][0]["root"].update(active_child_issue_ids=["T-active"]))
        add("pending_review", lambda s: s["migration_groups"][0]["root"].update(pending_review_issue_ids=["V-review"]))
        add("approval_gate_open", lambda s: s["migration_groups"][0]["root"].update(open_approval_gates=["final"]))
        add("git_state_unsafe", lambda s: s["migration_groups"][0]["git"].update(clean=False))
        add("delivery_evidence_drift", lambda s: s["migration_groups"][0]["root"].update(delivery_complete=False))
        add("plan_binding_drift", lambda s: s["migration_groups"][0]["root"].update(current_policy_digest="v3.sha256:" + "7" * 64))
        add("incomplete_lease_inventory", lambda s: s.update(inventory_complete=False))
        add("incomplete_lease_inventory", lambda s: s.update(inventory_selection_rule="wrong"))
        add("incomplete_lease_inventory", lambda s: s["endpoint_inventory"].pop(1))
        add("metadata_byte_capacity_unknown", lambda s: [s["migration_groups"][0]["authority"].pop("metadata_total_bytes"), s["endpoint_inventory"][0].pop("metadata_total_bytes")])

        for code, mutate in cases:
            with self.subTest(code=code, index=cases.index((code, mutate))):
                snapshot = lease_snapshot()
                mutate(snapshot)
                result = delivery_policy.lease_transition(
                    snapshot, "normalize-legacy-terminal"
                )
                self.assertFalse(result["valid"])
                self.assertEqual(result["ordered_writes"], [])
                self.assertIn(code, result["blocker_reasons"])

    def test_partial_transition_detects_conflict_and_concurrent_change(self):
        snapshot = lease_snapshot()
        prepared = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        snapshot = apply_lease_write(snapshot, prepared)
        snapshot["snapshot_read_id"] = "fresh-read-concurrent"
        snapshot["migration_groups"][0]["root"]["review_evidence_current"] = False
        changed = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        self.assertFalse(changed["valid"])
        self.assertIn("concurrent_inventory_change", changed["blocker_reasons"])
        self.assertIn("pending_review", changed["blocker_reasons"])

        snapshot = lease_snapshot()
        prepared = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        snapshot = apply_lease_write(snapshot, prepared)
        snapshot["snapshot_read_id"] = "fresh-read-concurrent"
        snapshot["migration_groups"][0]["authority"]["metadata_total_bytes"] += 1
        snapshot["endpoint_inventory"][0]["metadata_total_bytes"] += 1
        snapshot["discovered_issue_inventory"][0]["metadata_total_bytes"] += 1
        changed = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        self.assertFalse(changed["valid"])
        self.assertIn("concurrent_inventory_change", changed["blocker_reasons"])

        snapshot = lease_snapshot()
        prepared = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        snapshot = apply_lease_write(snapshot, prepared)
        snapshot["migration_groups"][0]["mirror"]["workspace_lease_transition_record"] = "v1.invalid+record"
        snapshot["endpoint_inventory"][1]["workspace_lease_transition_record"] = "v1.invalid+record"
        conflicted = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        self.assertFalse(conflicted["valid"])
        self.assertIn(
            "transition_checkpoint_conflict", conflicted["blocker_reasons"]
        )

    def test_complete_transition_rechecks_inventory_before_acquisition(self):
        snapshot = lease_snapshot()
        for _ in range(6):
            result = delivery_policy.lease_transition(
                snapshot, "normalize-legacy-terminal"
            )
            snapshot = apply_lease_write(snapshot, result)
        snapshot["current_claims"].append(
            {
                "root_requirement_id": "R-current",
                "owner_issue_id": "T-current",
                "owner_agent_id": "agent-current",
            }
        )
        preflight = delivery_policy.lease_transition(snapshot, "acquire-preflight")
        self.assertFalse(preflight["valid"])
        self.assertIn("competing_lease_claim", preflight["blocker_reasons"])

    def test_complete_transition_accepts_fresh_read_id_advance(self):
        snapshot = lease_snapshot()
        for _ in range(6):
            result = delivery_policy.lease_transition(
                snapshot, "normalize-legacy-terminal"
            )
            snapshot = apply_lease_write(snapshot, result)
        snapshot["snapshot_read_id"] = "fresh-read-later"
        complete = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        self.assertTrue(complete["valid"])
        self.assertEqual(complete["outcome"], "normalization_complete")
        preflight = delivery_policy.lease_transition(snapshot, "acquire-preflight")
        self.assertTrue(preflight["acquisition_allowed"])

    def test_lease_transition_accepts_legacy_v2_plan_binding(self):
        snapshot = lease_snapshot()
        root = snapshot["migration_groups"][0]["root"]
        root["plan_schema_version"] = 2
        root["approved_policy_digest"] = "6" * 64
        root["current_policy_digest"] = "6" * 64
        root.pop("plan_frozen_fields")
        result = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        self.assertTrue(result["valid"])
        self.assertEqual(
            result["groups"][0]["before"]["workspace_lease_state"],
            "integrator",
        )

    def test_multiple_terminal_groups_normalize_without_interlock(self):
        snapshot = lease_snapshot(("integrator", "released"))
        preflight = delivery_policy.lease_transition(snapshot, "acquire-preflight")
        self.assertEqual(
            [item["legacy_family"] for item in preflight["groups"]],
            ["role_state", "released_with_owner"],
        )
        self.assertEqual(preflight["recovery"]["pending_group_count"], 2)
        for _ in range(6):
            result = delivery_policy.lease_transition(
                snapshot, "normalize-legacy-terminal"
            )
            self.assertEqual(
                result["selected_group"]["root_requirement_id"], "R-1"
            )
            snapshot = apply_lease_write(snapshot, result)
        between = delivery_policy.lease_transition(snapshot, "acquire-preflight")
        self.assertFalse(between["valid"])
        self.assertEqual(between["groups"][0]["state"], "complete")
        self.assertNotIn(between["groups"][1]["state"], {"canonical", "complete"})
        for _ in range(6):
            result = delivery_policy.lease_transition(
                snapshot, "normalize-legacy-terminal"
            )
            self.assertEqual(
                result["selected_group"]["root_requirement_id"], "R-2"
            )
            snapshot = apply_lease_write(snapshot, result)
        allowed = delivery_policy.lease_transition(snapshot, "acquire-preflight")
        self.assertTrue(allowed["acquisition_allowed"])

    def test_task_residue_is_outside_authority_inventory_domain(self):
        snapshot = lease_snapshot()
        snapshot["discovered_issue_inventory"].append(
            {
                "issue_id": "T-history",
                "endpoint_role": None,
                "workflow_object_type": "development_task",
                "workflow_stage": "implementation",
                "workspace_lease_state": "integrator",
                "workspace_lease_owner_issue_id": "T-history",
                "workspace_lease_owner_agent_id": "agent-integrator",
            }
        )
        result = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        self.assertTrue(result["valid"])
        self.assertEqual(result["selected_group"]["root_requirement_id"], "R-1")

        duplicated = lease_snapshot()
        duplicate = copy.deepcopy(duplicated["endpoint_inventory"][0])
        duplicate["issue_id"] = "I-duplicate-authority"
        duplicated["endpoint_inventory"].append(duplicate)
        blocked = delivery_policy.lease_transition(
            duplicated, "normalize-legacy-terminal"
        )
        self.assertFalse(blocked["valid"])
        self.assertIn("incomplete_lease_inventory", blocked["blocker_reasons"])

        unknown = lease_snapshot()
        unknown["discovered_issue_inventory"].append(
            {
                "issue_id": "I-dev4",
                "endpoint_role": "legacy_unknown_authority",
                "workspace_lease_state": "held_by_integrator",
            }
        )
        blocked = delivery_policy.lease_transition(
            unknown, "normalize-legacy-terminal"
        )
        self.assertIn("unknown_lease_authority_role", blocked["blocker_reasons"])

    def test_checkpoint_enforces_utf8_scalar_and_total_metadata_bytes(self):
        snapshot = lease_snapshot()
        result = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        self.assertEqual(
            result["transition_record_bytes"],
            len(result["transition_record"].encode("utf-8")),
        )
        self.assertLessEqual(
            result["ordered_writes"][0]["projected_metadata_total_bytes"],
            8192,
        )

        blocked_snapshot = lease_snapshot()
        authority = blocked_snapshot["migration_groups"][0]["authority"]
        authority["metadata_total_bytes"] = 8180
        blocked_snapshot["endpoint_inventory"][0]["metadata_total_bytes"] = 8180
        blocked_snapshot["discovered_issue_inventory"][0]["metadata_total_bytes"] = 8180
        blocked = delivery_policy.lease_transition(
            blocked_snapshot, "normalize-legacy-terminal"
        )
        self.assertFalse(blocked["valid"])
        self.assertIn(
            "metadata_byte_capacity_exceeded", blocked["blocker_reasons"]
        )

        scalar_snapshot = lease_snapshot()
        authority = scalar_snapshot["migration_groups"][0]["authority"]
        authority["metadata_scalar_byte_limit"] = 64
        scalar_snapshot["endpoint_inventory"][0]["metadata_scalar_byte_limit"] = 64
        scalar_snapshot["discovered_issue_inventory"][0]["metadata_scalar_byte_limit"] = 64
        blocked = delivery_policy.lease_transition(
            scalar_snapshot, "normalize-legacy-terminal"
        )
        self.assertIn(
            "metadata_scalar_capacity_exceeded", blocked["blocker_reasons"]
        )

    def test_discovered_terminal_root_set_must_match_batch(self):
        snapshot = lease_snapshot()
        snapshot["discovered_requirement_root_ids"].append("R-omitted")
        blocked = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        self.assertIn("incomplete_lease_inventory", blocked["blocker_reasons"])

    def test_canonical_group_does_not_require_historical_holder(self):
        snapshot = lease_snapshot()
        for endpoint_name in ("authority", "mirror"):
            snapshot["migration_groups"][0][endpoint_name].update(
                delivery_policy.CANONICAL_RELEASED_LEASE
            )
        for inventory_name in ("endpoint_inventory", "discovered_issue_inventory"):
            for endpoint in snapshot[inventory_name]:
                endpoint.update(delivery_policy.CANONICAL_RELEASED_LEASE)
        snapshot["migration_groups"][0]["historical_holder"] = {}
        allowed = delivery_policy.lease_transition(snapshot, "acquire-preflight")
        self.assertTrue(allowed["acquisition_allowed"])

    def test_other_pending_group_change_stops_prepared_group(self):
        snapshot = lease_snapshot(("integrator", "released"))
        prepared = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        snapshot = apply_lease_write(snapshot, prepared)
        second = snapshot["migration_groups"][1]["authority"]
        second["metadata_total_bytes"] += 1
        snapshot["endpoint_inventory"][2]["metadata_total_bytes"] += 1
        snapshot["discovered_issue_inventory"][2]["metadata_total_bytes"] += 1
        blocked = delivery_policy.lease_transition(
            snapshot, "normalize-legacy-terminal"
        )
        self.assertIn("concurrent_inventory_change", blocked["blocker_reasons"])

    def test_new_lease_writer_only_accepts_canonical_states(self):
        self.assertEqual(
            delivery_policy.canonical_lease_tuple(
                "held", "T-current", "agent-current"
            )["workspace_lease_state"],
            "held",
        )
        self.assertEqual(
            delivery_policy.canonical_lease_tuple("released"),
            delivery_policy.CANONICAL_RELEASED_LEASE,
        )
        for args in [
            ("developer", "T-current", "agent-current"),
            ("released", "T-current", "agent-current"),
            ("held", "", "agent-current"),
        ]:
            with self.subTest(args=args), self.assertRaises(
                delivery_policy.DeliveryPolicyError
            ):
                delivery_policy.canonical_lease_tuple(*args)

    def test_lease_transition_cli_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            snapshot_path = Path(temp) / "lease.json"
            snapshot_path.write_text(
                json.dumps(lease_snapshot()), encoding="utf-8"
            )
            command = [
                sys.executable,
                str(POLICY_PATH),
                "lease-transition",
                "--action",
                "normalize-legacy-terminal",
                "--snapshot",
                str(snapshot_path),
            ]
            first = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", check=False
            )
            second = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8", check=False
            )
        self.assertEqual(first.returncode, 0)
        self.assertEqual(first.stdout, second.stdout)
        self.assertEqual(
            json.loads(first.stdout)["resume_stage"],
            "prepare_authority_checkpoint",
        )

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
