import copy
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = (
    ROOT / "skills/multica-delivery-policy/scripts/delivery_policy.py"
)
SCHEMA_PATH = (
    ROOT
    / "skills/multica-delivery-policy/references/project-delivery.schema.json"
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
POLICY_DIGEST = "6" * 64


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
                self.assertEqual(
                    delivered["metadata_updates"]["delivery_target_branch"],
                    "release/v2",
                )
                self.assertEqual(
                    delivered["metadata_updates"][
                        "delivery_merge_parent_requirement_sha"
                    ],
                    REVIEWED_SHA,
                )
                snapshot = apply_transition(snapshot, delivered)
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
                self.assertEqual(
                    handoff["metadata_updates"]["delivery_handoff_trigger_outcome"],
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


if __name__ == "__main__":
    unittest.main()
