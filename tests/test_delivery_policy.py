import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "skills/multica-delivery-policy/scripts/delivery_policy.py"
)
INCIDENTS_PATH = ROOT / "skills/multica-workflow-incidents/scripts/incidents.py"
SCHEMA_PATH = (
    ROOT
    / "skills/multica-delivery-policy/references/project-delivery.schema.json"
)
SPEC = importlib.util.spec_from_file_location("delivery_policy", POLICY_PATH)
delivery_policy = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(delivery_policy)
INCIDENTS_SPEC = importlib.util.spec_from_file_location(
    "workflow_incidents_for_delivery_test", INCIDENTS_PATH
)
workflow_incidents = importlib.util.module_from_spec(INCIDENTS_SPEC)
assert INCIDENTS_SPEC.loader
sys.modules[INCIDENTS_SPEC.name] = workflow_incidents
INCIDENTS_SPEC.loader.exec_module(workflow_incidents)


REVIEWED_SHA = "1" * 40
TARGET_BASE_SHA = "2" * 40
DEFAULT_BASE_SHA = "3" * 40
MERGED_SHA = "4" * 40
TREE_SHA = "5" * 40
POLICY_DIGEST = "6" * 64
INTEGRATION_DEPENDENCY_DIGEST = "7" * 64
INTEGRATION_LEASE_DIGEST = "8" * 64
INTEGRATION_ROSTER = [
    {
        "agent_id": "integrator-1",
        "member_type": "agent",
        "role_key": "integrator",
        "active": True,
        "archived": False,
    },
    {
        "agent_id": "reviewer-1",
        "member_type": "agent",
        "role_key": "code_reviewer",
        "active": True,
        "archived": False,
    },
]
INTEGRATION_ROSTER_DIGEST = delivery_policy.digest(
    {
        "workspace_id": "workspace-1",
        "squad_id": "squad-1",
        "roster": INTEGRATION_ROSTER,
    }
)


def final_gate_snapshot(
    mode: str = "direct_push", *, target_branch: str = "release/v2"
) -> dict:
    requirement_pr_enabled = mode == "requirement_pr"
    remote_configured = mode != "local_only"
    integration_record = {
        "schema_version": 1,
        "record_type": "integration_review_role",
        "state": "approved",
        "workspace_id": "workspace-1",
        "squad_id": "squad-1",
        "roster_digest": INTEGRATION_ROSTER_DIGEST,
        "issue_id": "IV-1",
        "owner_id": "integrator-1",
        "reviewer_id": "reviewer-1",
        "plan_revision": 2,
        "delivery_policy_digest": POLICY_DIGEST,
        "base_commit_sha": TARGET_BASE_SHA,
        "reviewed_commit_sha": REVIEWED_SHA,
        "dependency_digest": INTEGRATION_DEPENDENCY_DIGEST,
        "lease_digest": INTEGRATION_LEASE_DIGEST,
        "handoff_comment_id": "review-handoff-1",
        "handoff_created_at": "2026-08-11T10:00:00Z",
        "trigger_run_id": "review-run-1",
        "trigger_outcome": "queued",
        "review_comment_id": "review-comment-1",
        "review_author_id": "reviewer-1",
        "review_created_at": "2026-08-11T10:01:00Z",
    }
    integration_record["review_binding_digest"] = (
        delivery_policy._integration_review_binding_digest(integration_record)
    )
    integration_record["review_epoch_id"] = delivery_policy._integration_review_epoch_id(
        integration_record
    )
    integration_role_record = delivery_policy.encode_metadata_record(integration_record)
    root = {
        "issue_id": "R-1",
        "root_requirement_id": "R-1",
        "workflow_object_type": "requirement",
        "status": "in_progress",
        "workflow_instance_id": "workspace-1",
        "plan_status": "done",
        "implementation_status": "done",
        "integration_validation_status": "done",
        "integration_review_status": "approved",
        "integration_squad_id": "squad-1",
        "integration_roster_complete": True,
        "integration_roster": copy.deepcopy(INTEGRATION_ROSTER),
        "integration_roster_digest": INTEGRATION_ROSTER_DIGEST,
        "integration_validation_issue_id": "IV-1",
        "integration_validation_assignee_id": "integrator-1",
        "integration_original_owner_id": "integrator-1",
        "integration_reviewer_id": "reviewer-1",
        "integration_base_commit_sha": TARGET_BASE_SHA,
        "integration_dependency_digest": INTEGRATION_DEPENDENCY_DIGEST,
        "integration_lease_digest": INTEGRATION_LEASE_DIGEST,
        "integration_review_role_record": integration_role_record,
        "integration_review_recovery_record": "",
        "integration_review_comment_id": "review-comment-1",
        "integration_review_comment_author_id": "reviewer-1",
        "integration_review_epoch_id": integration_record["review_epoch_id"],
        "integration_review_handoff_comment_id": "review-handoff-1",
        "integration_review_trigger_run_id": "review-run-1",
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


def lease_snapshot(direction: str = "release") -> dict:
    if direction == "release":
        current = {
            "workspace_lease_state": "held",
            "workspace_lease_owner_issue_id": "IV-1",
            "workspace_lease_owner_agent_id": "integrator-1",
        }
        desired = {
            "workspace_lease_state": "released",
            "workspace_lease_owner_issue_id": "",
            "workspace_lease_owner_agent_id": "",
        }
    else:
        current = {
            "workspace_lease_state": "released",
            "workspace_lease_owner_issue_id": "",
            "workspace_lease_owner_agent_id": "",
        }
        desired = {
            "workspace_lease_state": "held",
            "workspace_lease_owner_issue_id": "IV-1",
            "workspace_lease_owner_agent_id": "integrator-1",
        }
    blocker = {
        "status": "blocked",
        "waiting_on": "workflow_fix",
        "blocked_reason": "workflow Incident INC-1",
        "workflow_blocked_by_incident_id": "INC-1",
        "workflow_blocked_previous_status": "in_progress",
    }
    snapshot = {
        "context": {
            "workspace_id": "workspace-1",
            "squad_id": "squad-1",
            "roster_digest": "a" * 64,
            "lease_inventory_complete": direction == "acquire",
        },
        "plan": {
            "plan_revision": 2,
            "delivery_policy_digest": POLICY_DIGEST,
        },
        "guard": {
            "valid": True,
            "clean": True,
            "branch": "req/R-1",
            "expected_branch": "req/R-1",
            "head": REVIEWED_SHA,
            "expected_head": REVIEWED_SHA,
            "unfinished_operations": [],
        },
        "authority": {
            "issue_id": "IMP-1",
            "metadata_keys": [f"authority_{index}" for index in range(42)],
            "workspace_lease_scope": "requirement",
            "workspace_lease_transition_record": "",
            **current,
        },
        "mirror": {
            "issue_id": "IV-1",
            "metadata_keys": [f"mirror_{index}" for index in range(42)],
            "workspace_lease_scope": "requirement",
            "workspace_lease_transition_record": "",
            **current,
            **blocker,
        },
        "desired": desired,
    }
    if direction == "acquire":
        snapshot["other_lease"] = {
            "requirement_id": "R-OTHER",
            "authority": {
                "issue_id": "IMP-OTHER",
                "workspace_lease_state": "released",
                "workspace_lease_owner_issue_id": "",
                "workspace_lease_owner_agent_id": "",
            },
            "mirror": {
                "issue_id": "TASK-OTHER",
                "workspace_lease_state": "released",
                "workspace_lease_owner_issue_id": "",
                "workspace_lease_owner_agent_id": "",
            },
        }
    return snapshot


def apply_lease_write(snapshot: dict, write: dict) -> dict:
    updated = copy.deepcopy(snapshot)
    endpoint = updated[write["endpoint"]]
    endpoint[write["key"]] = write["value"]
    endpoint["metadata_keys"] = sorted(
        set(endpoint["metadata_keys"]) | {write["key"]}
    )
    return updated


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


class DeliveryPolicyTests(unittest.TestCase):
    def test_original_cli_and_metadata_record_encoding_remain_compatible(self):
        command_choices = next(
            action.choices
            for action in delivery_policy.parser()._actions
            if isinstance(getattr(action, "choices", None), dict)
        )
        self.assertTrue(
            {"resolve", "verify", "guard-workspace", "final-gate"}.issubset(
                command_choices
            )
        )
        self.assertEqual(
            delivery_policy.encode_metadata_record(
                {"schema_version": 1, "record_type": "test", "a": "value"}
            ),
            "v1.eyJhIjoidmFsdWUiLCJyZWNvcmRfdHlwZSI6InRlc3QiLCJzY2hlbWFfdmVyc2lvbiI6MX0",
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
            snapshot = delivery_policy.resolve_policy(repo)
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
            snapshot = delivery_policy.resolve_policy(repo)
            snapshot["effective"]["task_pr"] = True
            with self.assertRaisesRegex(
                delivery_policy.DeliveryPolicyError,
                "does not match policy_digest",
            ):
                delivery_policy.verify_snapshot(repo, snapshot)

    def test_plan_snapshot_detects_remote_push_target_change(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            snapshot = delivery_policy.resolve_policy(repo)
            run_git(
                repo,
                "remote",
                "set-url",
                "--push",
                "origin",
                "git@github.com:example/other.git",
            )
            self.assertFalse(delivery_policy.verify_snapshot(repo, snapshot)["valid"])

    def test_versioned_policy_digest_is_stable_for_same_resolver_and_state(self):
        with tempfile.TemporaryDirectory() as temp:
            repo = create_repo(Path(temp), "https://github.com/example/project.git")
            first = delivery_policy.resolve_policy(repo)
            second = delivery_policy.resolve_policy(repo)
            verified = delivery_policy.verify_snapshot(repo, first)
        self.assertEqual(first["schema_version"], 2)
        self.assertEqual(first["policy_digest_schema_version"], 2)
        self.assertEqual(first["policy_digest"], second["policy_digest"])
        self.assertEqual(
            first["resolver_provenance"], second["resolver_provenance"]
        )
        self.assertEqual(
            first["snapshot_record_digest"], second["snapshot_record_digest"]
        )
        self.assertTrue(verified["valid"])
        self.assertEqual(verified["verification_outcome"], "exact_match")

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
            current = delivery_policy.resolve_policy(repo)
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
            current = delivery_policy.resolve_policy(repo)
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
            current = delivery_policy.resolve_policy(repo)
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
            frozen = delivery_policy.resolve_policy(repo)
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
            frozen = delivery_policy.resolve_policy(repo)
            changed_selection = delivery_policy.resolve_policy(
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
            frozen = delivery_policy.resolve_policy(repo)
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
            current = delivery_policy.resolve_policy(repo)
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
            current = delivery_policy.resolve_policy(repo)
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

    def test_final_gate_reuses_integration_identity_on_every_action(self):
        snapshots = {}
        snapshots["open"] = final_gate_snapshot()
        opened = apply_transition(
            final_gate_snapshot(),
            delivery_policy.final_gate_transition(final_gate_snapshot(), "open"),
        )
        opened["actor_role"] = "integrator"
        snapshots["approve"] = opened
        approved = open_and_approve(final_gate_snapshot())
        snapshots["delivery"] = approved
        delivered = apply_transition(
            approved,
            delivery_policy.final_gate_transition(approved, "delivery"),
        )
        delivered["handoff"] = {
            "issue_id": "R-1",
            "comment_id": "handoff-identity",
            "mentioned_role": "leader",
            "trigger_outcomes": [
                {"recipient_role": "leader", "status": "queued"}
            ],
        }
        snapshots["handoff"] = delivered
        converging = apply_handoff(copy.deepcopy(delivered))
        converging["actor_role"] = "leader"
        snapshots["converge"] = converging
        for action, snapshot in snapshots.items():
            with self.subTest(action=action):
                candidate = copy.deepcopy(snapshot)
                candidate["root"]["integration_original_owner_id"] = "reviewer-1"
                result = delivery_policy.final_gate_transition(candidate, action)
                self.assertFalse(result["allowed"])
                self.assertIn(
                    "integration owner and reviewer must be independent",
                    result["reasons"],
                )
                self.assertEqual(result["metadata_updates"], {})
                self.assertIsNone(result["status_write"])

    def test_final_gate_accepts_the_incident_skill_review_record_contract(self):
        integration = {
            "context": {
                "workspace_id": "workspace-1",
                "squad_id": "squad-1",
                "roster_complete": True,
                "roster": copy.deepcopy(INTEGRATION_ROSTER),
            },
            "issue": {
                "issue_id": "IV-1",
                "status": "in_progress",
                "workflow_id": "development-delivery",
                "protocol_revision": "v4",
                "workflow_object_type": "integration_validation",
                "workflow_instance_id": "workspace-1",
                "assignee_id": "integrator-1",
                "original_owner_id": "integrator-1",
                "reviewer_id": "reviewer-1",
                "plan_revision": 2,
                "delivery_policy_digest": POLICY_DIGEST,
                "base_commit_sha": TARGET_BASE_SHA,
                "reviewed_commit_sha": REVIEWED_SHA,
                "metadata_keys": [f"key_{index}" for index in range(42)],
                "workflow_blocked_by_incident_id": "",
            },
            "dependency": {"satisfied": True, "contract": "done:T-1"},
            "lease": {
                "scope": "requirement",
                "state": "held",
                "owner_issue_id": "IV-1",
                "owner_agent_id": "integrator-1",
            },
            "used_review_comment_ids": [],
            "used_review_comment_ids_complete": True,
        }

        def apply_review(result):
            integration["issue"].update(result["metadata_updates"])
            integration["issue"]["metadata_keys"] = sorted(
                set(integration["issue"]["metadata_keys"])
                | set(result["metadata_updates"])
            )

        apply_review(workflow_incidents.integration_review_transition(integration, "prepare"))
        apply_review(workflow_incidents.integration_review_transition(integration, "start"))
        integration["handoff"] = {
            "issue_id": "IV-1",
            "author_type": "agent",
            "author_id": "integrator-1",
            "mentioned_agent_id": "reviewer-1",
            "comment_id": "handoff-1",
            "created_at": "2026-08-11T10:00:00Z",
            "trigger_run_id": "run-1",
            "attempt": 1,
            "max_attempts": 3,
            "previous_attempts_complete": True,
            "previous_attempts": [],
            "trigger_outcomes": [
                {"recipient_id": "reviewer-1", "status": "queued", "run_id": "run-1"}
            ],
        }
        apply_review(workflow_incidents.integration_review_transition(integration, "handoff"))
        role = workflow_incidents.decode_metadata_record(
            integration["issue"]["integration_review_role_record"],
            "role",
            "integration_review_role",
        )
        integration["review"] = {
            "issue_id": "IV-1",
            "author_type": "agent",
            "author_id": "reviewer-1",
            "comment_id": "review-1",
            "created_at": "2026-08-11T10:01:00Z",
            "verdict": "APPROVED",
            "review_epoch_id": role["review_epoch_id"],
            "trigger_comment_id": "handoff-1",
            "source_run_id": "run-1",
        }
        apply_review(workflow_incidents.integration_review_transition(integration, "approve"))
        role_value = integration["issue"]["integration_review_role_record"]
        role = delivery_policy.decode_metadata_record(role_value)

        snapshot = final_gate_snapshot()
        root = snapshot["root"]
        root.update(
            {
                "integration_review_role_record": role_value,
                "integration_roster_digest": role["roster_digest"],
                "integration_dependency_digest": role["dependency_digest"],
                "integration_lease_digest": role["lease_digest"],
                "integration_review_comment_id": role["review_comment_id"],
                "integration_review_comment_author_id": role["review_author_id"],
                "integration_review_epoch_id": role["review_epoch_id"],
                "integration_review_handoff_comment_id": role["handoff_comment_id"],
                "integration_review_trigger_run_id": role["trigger_run_id"],
            }
        )
        accepted = delivery_policy.final_gate_transition(snapshot, "open")
        self.assertTrue(accepted["allowed"], accepted["reasons"])

    def test_final_gate_rejects_duplicate_archived_or_colliding_current_roles(self):
        cases = {}
        duplicate = final_gate_snapshot()
        duplicate["root"]["integration_roster"].append(
            {
                "agent_id": "reviewer-2",
                "member_type": "agent",
                "role_key": "code_reviewer",
                "active": True,
                "archived": False,
            }
        )
        cases["duplicate"] = duplicate
        archived = final_gate_snapshot()
        archived["root"]["integration_roster"][1]["archived"] = True
        cases["archived"] = archived
        incomplete = final_gate_snapshot()
        incomplete["root"]["integration_roster_complete"] = False
        cases["incomplete"] = incomplete
        collision = final_gate_snapshot()
        collision["root"]["integration_roster"][1]["agent_id"] = "integrator-1"
        cases["collision"] = collision
        for name, snapshot in cases.items():
            with self.subTest(name=name):
                result = delivery_policy.final_gate_transition(snapshot, "open")
                self.assertFalse(result["allowed"])
                self.assertEqual(result["metadata_updates"], {})
                self.assertIsNone(result["status_write"])

    def test_final_gate_recomputes_review_epoch_and_timestamp_freshness(self):
        snapshot = final_gate_snapshot()
        role = delivery_policy.decode_metadata_record(
            snapshot["root"]["integration_review_role_record"]
        )
        role["review_epoch_id"] = "forged-epoch"
        snapshot["root"]["integration_review_role_record"] = (
            delivery_policy.encode_metadata_record(role)
        )
        forged = delivery_policy.final_gate_transition(snapshot, "open")
        self.assertFalse(forged["allowed"])
        self.assertIn("integration Review epoch digest is invalid", forged["reasons"])

        snapshot = final_gate_snapshot()
        role = delivery_policy.decode_metadata_record(
            snapshot["root"]["integration_review_role_record"]
        )
        role["review_created_at"] = role["handoff_created_at"]
        snapshot["root"]["integration_review_role_record"] = (
            delivery_policy.encode_metadata_record(role)
        )
        stale = delivery_policy.final_gate_transition(snapshot, "open")
        self.assertFalse(stale["allowed"])
        self.assertIn(
            "integration Review comment predates or coincides with its handoff",
            stale["reasons"],
        )

        for record_key, root_key in (
            ("review_comment_id", "integration_review_comment_id"),
            ("handoff_comment_id", "integration_review_handoff_comment_id"),
            ("trigger_run_id", "integration_review_trigger_run_id"),
        ):
            with self.subTest(missing=record_key):
                candidate = final_gate_snapshot()
                candidate_role = delivery_policy.decode_metadata_record(
                    candidate["root"]["integration_review_role_record"]
                )
                candidate_role[record_key] = ""
                candidate_role["review_epoch_id"] = (
                    delivery_policy._integration_review_epoch_id(candidate_role)
                )
                candidate["root"]["integration_review_role_record"] = (
                    delivery_policy.encode_metadata_record(candidate_role)
                )
                candidate["root"][root_key] = ""
                candidate["root"]["integration_review_epoch_id"] = candidate_role[
                    "review_epoch_id"
                ]
                result = delivery_policy.final_gate_transition(candidate, "open")
                self.assertFalse(result["allowed"])
                self.assertIn("ID is missing", " ".join(result["reasons"]))

    def test_final_gate_rejects_stale_integration_review_bindings(self):
        cases = {
            "roster": ("integration_roster_digest", "a" * 64),
            "assignee": ("integration_validation_assignee_id", "reviewer-1"),
            "owner": ("integration_original_owner_id", "integrator-2"),
            "reviewer": ("integration_reviewer_id", "reviewer-2"),
            "base": ("integration_base_commit_sha", "b" * 40),
            "dependency": ("integration_dependency_digest", "c" * 64),
            "lease": ("integration_lease_digest", "d" * 64),
            "comment": ("integration_review_comment_id", "old-comment"),
            "author": ("integration_review_comment_author_id", "integrator-1"),
            "epoch": ("integration_review_epoch_id", "old-epoch"),
            "handoff": ("integration_review_handoff_comment_id", "old-handoff"),
            "run": ("integration_review_trigger_run_id", "old-run"),
        }
        for name, (key, value) in cases.items():
            with self.subTest(name=name):
                snapshot = final_gate_snapshot()
                snapshot["root"][key] = value
                result = delivery_policy.final_gate_transition(snapshot, "open")
                self.assertFalse(result["allowed"])
                self.assertEqual(result["metadata_updates"], {})

    def test_final_gate_binds_current_recovery_record(self):
        snapshot = final_gate_snapshot()
        root = snapshot["root"]
        role = delivery_policy.decode_metadata_record(
            root["integration_review_role_record"]
        )
        recovery = {
            "schema_version": 1,
            "record_type": "integration_review_recovery",
            **{
                key: role[key]
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
                )
            },
            "incident_id": "INC-1",
            "recovery_id": "recovery-1",
            "from_assignee_id": "reviewer-1",
            "from_original_owner_id": "reviewer-1",
            "from_reviewer_id": "reviewer-1",
            "lease_transition_digest": "7" * 64,
            "blocker_digest": "8" * 64,
        }
        recovery_value = delivery_policy.encode_metadata_record(recovery)
        role["recovery_record_digest"] = delivery_policy.hashlib.sha256(
            recovery_value.encode("utf-8")
        ).hexdigest()
        role["review_binding_digest"] = delivery_policy._integration_review_binding_digest(
            role
        )
        role["review_epoch_id"] = delivery_policy._integration_review_epoch_id(role)
        root["integration_review_recovery_record"] = recovery_value
        root["integration_review_role_record"] = delivery_policy.encode_metadata_record(role)
        root["integration_review_epoch_id"] = role["review_epoch_id"]
        self.assertTrue(delivery_policy.final_gate_transition(snapshot, "open")["allowed"])
        root["integration_review_recovery_record"] = delivery_policy.encode_metadata_record(
            {**recovery, "recovery_id": "recovery-2"}
        )
        stale = delivery_policy.final_gate_transition(snapshot, "open")
        self.assertFalse(stale["allowed"])
        self.assertIn("integration recovery record is stale", stale["reasons"])

        for key, value in (
            ("incident_id", ""),
            ("from_assignee_id", ""),
            ("lease_transition_digest", "invalid"),
            ("blocker_digest", "invalid"),
        ):
            with self.subTest(key=key):
                candidate = final_gate_snapshot()
                candidate_root = candidate["root"]
                candidate_role = delivery_policy.decode_metadata_record(
                    candidate_root["integration_review_role_record"]
                )
                invalid_recovery = {**recovery, key: value}
                invalid_value = delivery_policy.encode_metadata_record(invalid_recovery)
                candidate_role["recovery_record_digest"] = (
                    delivery_policy.hashlib.sha256(
                        invalid_value.encode("utf-8")
                    ).hexdigest()
                )
                candidate_role["review_binding_digest"] = (
                    delivery_policy._integration_review_binding_digest(candidate_role)
                )
                candidate_role["review_epoch_id"] = (
                    delivery_policy._integration_review_epoch_id(candidate_role)
                )
                candidate_root["integration_review_recovery_record"] = invalid_value
                candidate_root["integration_review_role_record"] = (
                    delivery_policy.encode_metadata_record(candidate_role)
                )
                candidate_root["integration_review_epoch_id"] = candidate_role[
                    "review_epoch_id"
                ]
                result = delivery_policy.final_gate_transition(candidate, "open")
                self.assertFalse(result["allowed"])
                self.assertEqual(result["metadata_updates"], {})

    def test_lease_release_and_acquire_resume_at_every_write_prefix(self):
        for direction in ("release", "acquire"):
            with self.subTest(direction=direction):
                snapshot = lease_snapshot(direction)
                initial_blocker = {
                    key: snapshot["mirror"][key]
                    for key in delivery_policy.LEASE_BLOCKER_FIELDS
                }
                first = delivery_policy.lease_transition_preflight(snapshot)
                self.assertTrue(first["allowed"])
                full_writes = first["writes"]
                for cut in range(len(full_writes) + 1):
                    candidate = copy.deepcopy(snapshot)
                    for write in full_writes[:cut]:
                        candidate = apply_lease_write(candidate, write)
                        self.assertEqual(
                            {
                                key: candidate["mirror"][key]
                                for key in delivery_policy.LEASE_BLOCKER_FIELDS
                            },
                            initial_blocker,
                        )
                        authority_held = (
                            candidate["authority"]["workspace_lease_state"] == "held"
                        )
                        mirror_held = (
                            candidate["mirror"]["workspace_lease_state"] == "held"
                        )
                        if direction == "acquire" and mirror_held:
                            self.assertTrue(authority_held)
                        if direction == "release" and not authority_held:
                            self.assertFalse(mirror_held)
                    resumed = delivery_policy.lease_transition_preflight(candidate)
                    self.assertTrue(resumed["allowed"])
                    self.assertEqual(resumed["progress"], cut)
                    self.assertEqual(resumed["blocker_writes"], [])
                    self.assertEqual(resumed["status_writes"], [])
                    if direction == "release":
                        self.assertEqual(
                            resumed["next_requirement_acquire_allowed"],
                            resumed["complete"],
                        )
                    else:
                        self.assertFalse(resumed["next_requirement_acquire_allowed"])

    def test_lease_acquire_rejects_double_owner_guard_drift_and_capacity(self):
        incomplete_inventory = lease_snapshot("acquire")
        incomplete_inventory["context"]["lease_inventory_complete"] = False
        with self.assertRaisesRegex(
            delivery_policy.DeliveryPolicyError, "complete other Requirement"
        ):
            delivery_policy.lease_transition_preflight(incomplete_inventory)

        double = lease_snapshot("acquire")
        double["other_lease"]["authority"]["workspace_lease_state"] = "held"
        with self.assertRaisesRegex(
            delivery_policy.DeliveryPolicyError, "double ownership"
        ):
            delivery_policy.lease_transition_preflight(double)

        aliased = lease_snapshot("acquire")
        aliased["other_lease"]["authority"]["issue_id"] = "IMP-1"
        with self.assertRaisesRegex(
            delivery_policy.DeliveryPolicyError, "includes the current endpoints"
        ):
            delivery_policy.lease_transition_preflight(aliased)

        guard = lease_snapshot("release")
        guard["guard"]["head"] = "f" * 40
        rejected = delivery_policy.lease_transition_preflight(guard)
        self.assertFalse(rejected["allowed"])
        self.assertIn("workspace guard head drifted", rejected["reasons"])

        capacity = lease_snapshot("release")
        capacity["mirror"]["metadata_keys"] = [f"key_{index}" for index in range(50)]
        before = copy.deepcopy(capacity)
        rejected = delivery_policy.lease_transition_preflight(capacity)
        self.assertFalse(rejected["allowed"])
        self.assertEqual(rejected["writes"], [])
        self.assertEqual(capacity, before)

    def test_lease_retry_rejects_blocker_roster_policy_and_record_drift(self):
        base = lease_snapshot("release")
        first = delivery_policy.lease_transition_preflight(base)
        partial = apply_lease_write(base, first["writes"][0])
        cases = []
        blocker = copy.deepcopy(partial)
        blocker["mirror"]["waiting_on"] = "workspace_lease_recovery"
        cases.append(("blocker", blocker, "Incident blocker"))
        roster = copy.deepcopy(partial)
        roster["context"]["roster_digest"] = "b" * 64
        cases.append(("roster", roster, "roster binding drifted"))
        policy = copy.deepcopy(partial)
        policy["plan"]["delivery_policy_digest"] = "c" * 64
        cases.append(("policy", policy, "policy digest drifted"))
        record = copy.deepcopy(partial)
        record["mirror"]["workspace_lease_transition_record"] = "v1.not+base64"
        cases.append(("record", record, "invalid"))
        acquire = lease_snapshot("acquire")
        acquire_first = delivery_policy.lease_transition_preflight(acquire)
        acquire_partial = apply_lease_write(acquire, acquire_first["writes"][0])
        acquire_partial["other_lease"]["authority"]["workspace_lease_state"] = "held"
        cases.append(("other lease", acquire_partial, "inventory drifted"))
        for name, snapshot, message in cases:
            with self.subTest(name=name):
                with self.assertRaisesRegex(
                    delivery_policy.DeliveryPolicyError, message
                ):
                    delivery_policy.lease_transition_preflight(snapshot)

        tampered = lease_snapshot("release")
        first = delivery_policy.lease_transition_preflight(tampered)
        tampered = apply_lease_write(tampered, first["writes"][0])
        record = delivery_policy.decode_metadata_record(
            tampered["authority"]["workspace_lease_transition_record"]
        )
        record["direction"] = "acquire"
        tampered["authority"]["workspace_lease_transition_record"] = (
            delivery_policy.encode_metadata_record(record)
        )
        with self.assertRaisesRegex(
            delivery_policy.DeliveryPolicyError, "acquire tuple is invalid"
        ):
            delivery_policy.lease_transition_preflight(tampered)

    def test_completed_lease_records_allow_idempotence_and_the_next_transition(self):
        release = lease_snapshot("release")
        result = delivery_policy.lease_transition_preflight(release)
        completed = copy.deepcopy(release)
        for write in result["writes"]:
            completed = apply_lease_write(completed, write)
        again = delivery_policy.lease_transition_preflight(completed)
        self.assertTrue(again["complete"])
        self.assertEqual(again["writes"], [])

        completed["desired"] = {
            "workspace_lease_state": "held",
            "workspace_lease_owner_issue_id": "IV-1",
            "workspace_lease_owner_agent_id": "integrator-1",
        }
        completed["context"]["lease_inventory_complete"] = True
        completed["other_lease"] = {
            "requirement_id": "R-OTHER",
            "authority": {
                "issue_id": "IMP-OTHER",
                "workspace_lease_state": "released",
                "workspace_lease_owner_issue_id": "",
                "workspace_lease_owner_agent_id": "",
            },
            "mirror": {
                "issue_id": "TASK-OTHER",
                "workspace_lease_state": "released",
                "workspace_lease_owner_issue_id": "",
                "workspace_lease_owner_agent_id": "",
            },
        }
        acquired = delivery_policy.lease_transition_preflight(completed)
        self.assertTrue(acquired["allowed"])
        self.assertEqual(acquired["direction"], "acquire")
        self.assertFalse(acquired["complete"])

    def test_lease_capacity_rejects_duplicate_or_empty_inventory_keys(self):
        for metadata_keys in (["same", "same"], [""]):
            with self.subTest(metadata_keys=metadata_keys):
                snapshot = lease_snapshot("release")
                snapshot["authority"]["metadata_keys"] = metadata_keys
                rejected = delivery_policy.lease_transition_preflight(snapshot)
                self.assertFalse(rejected["allowed"])
                self.assertEqual(rejected["writes"], [])

    def test_lease_preflight_cli_is_deterministic_and_zero_write(self):
        snapshot = lease_snapshot("release")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lease.json"
            original = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            path.write_text(original, encoding="utf-8")
            first = subprocess.run(
                [
                    sys.executable,
                    str(POLICY_PATH),
                    "lease-transition",
                    "--snapshot",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            second = subprocess.run(
                [
                    sys.executable,
                    str(POLICY_PATH),
                    "lease-transition",
                    "--snapshot",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=False,
            )
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(first.stdout, second.stdout)
            self.assertEqual(path.read_text(encoding="utf-8"), original)


if __name__ == "__main__":
    unittest.main()
