import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP_RECORD = json.loads(
    (ROOT / "docs/bootstrap-v6.json").read_text(encoding="utf-8")
)
MODULE_PATH = ROOT / "scripts/release.py"
SPEC = importlib.util.spec_from_file_location("workflow_release", MODULE_PATH)
release = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = release
SPEC.loader.exec_module(release)


def marker(object_key):
    return (
        "<!-- multica-workflow\n"
        "managed_by=multica-dev-workflow\n"
        "workflow_id=development-delivery\n"
        f"object_key={object_key}\n"
        "spec_hash=test\n"
        "-->\nbody\n"
    )


def maintenance_pr():
    return {
        "number": 3,
        "headRefOid": "head-sha",
        "mergeCommit": {"oid": "merge-sha"},
        "mergedAt": "2026-07-15T12:00:00Z",
    }


def asset_annotation(assets):
    normalized = sorted(assets)
    return (
        f"expected_assets_sha256={release.expected_assets_sha256(normalized)}\n"
        + "".join(f"expected_asset={name}\n" for name in normalized)
    )


BATCH_MAPPINGS = [
    {
        "incident": "WOR-9",
        "plan_issue": "WOR-11",
        "plan_revision": "v1",
        "implementation": "WOR-14",
    },
    {
        "incident": "WOR-15",
        "plan_issue": "WOR-25",
        "plan_revision": "v1",
        "implementation": "WOR-24",
    },
    {
        "incident": "WOR-20",
        "plan_issue": "WOR-32",
        "plan_revision": "v1",
        "implementation": "WOR-33",
    },
]


def bounded_review_content():
    return (
        "APPROVED\n"
        "\n"
        "plan_revision=v1\n"
        "reviewed_commit_sha=head-sha\n"
        "\n"
        "Batch mappings reviewed:\n"
        "- incident=WOR-9 plan_issue=WOR-11 plan_revision=v1 implementation=WOR-14\n"
        "- incident=WOR-15 plan_issue=WOR-25 plan_revision=v1 implementation=WOR-24\n"
        "- incident=WOR-20 plan_issue=WOR-32 plan_revision=v1 implementation=WOR-33"
    )


def bounded_batch_cli():
    cli = FakeMultica()
    cli.metadata.update(
        {
            "plan_revision": "v1",
            "review_issue_id": "WOR-14",
            "batch_review_mappings": json.dumps(BATCH_MAPPINGS),
            "delivery_batch": "RC3 Observer Reliability Batch",
            "source_incident_id": "WOR-9",
        }
    )
    cli.issues["WOR-14"] = {
        "id": "implementation-internal",
        "identifier": "WOR-14",
        "parent_issue_id": "maintenance-internal",
        "status": "in_review",
    }
    cli.metadata_by_issue["WOR-14"] = {
        "workflow_id": "development-delivery",
        "workflow_object_type": "maintenance_implementation",
        "maintenance_change_id": "T-200",
        "source_incident_id": "WOR-9",
        "maintenance_reviewer_id": "agent-reviewer",
        "review_comment_id": "review-1",
        "reviewed_commit_sha": "head-sha",
        "plan_revision": "v1",
        "github_pr_number": "3",
        "github_merge_commit_sha": "merge-sha",
    }
    cli.comments_by_issue["WOR-14"] = [
        {
            "id": "review-1",
            "author_type": "agent",
            "author_id": "agent-reviewer",
            "created_at": "2026-07-15T11:00:00Z",
            "content": bounded_review_content(),
        }
    ]
    return cli


class FakeMultica:
    def __init__(self):
        self.profile = "test-profile"
        self.workspace_id = "workspace-test"
        self.metadata = {
            "workflow_object_type": "maintenance_change",
            "maintainer_id": "agent-maintainer",
            "maintenance_reviewer_id": "agent-reviewer",
            "human_approver_id": "human-1",
            "plan_revision": "v3",
            "reviewed_commit_sha": "head-sha",
            "review_comment_id": "review-1",
            "github_pr_number": 3,
            "github_merge_commit_sha": "merge-sha",
        }
        self.comments = [
            {
                "id": "review-1",
                "author_type": "agent",
                "author_id": "agent-reviewer",
                "created_at": "2026-07-15T11:00:00Z",
                "content": "APPROVED\nplan_revision=v3\nreviewed_commit_sha=head-sha",
            }
        ]
        self.issues = {
            "T-200": {
                "id": "maintenance-internal",
                "identifier": "T-200",
                "status": "in_review",
            }
        }
        self.metadata_by_issue = {}
        self.comments_by_issue = {}

    def json(self, args):
        args = list(args)
        if args[:2] == ["issue", "get"]:
            return self.issues.get(
                args[2],
                {"id": args[2], "identifier": args[2], "status": "in_review"},
            )
        if args[:3] == ["issue", "metadata", "list"]:
            return self.metadata_by_issue.get(args[3], self.metadata)
        if args[:3] == ["issue", "comment", "list"]:
            return self.comments_by_issue.get(args[3], self.comments)
        if args[:2] == ["agent", "list"]:
            return [
                {
                    "id": "agent-maintainer",
                    "instructions": marker("agent.workflow-maintainer"),
                },
                {
                    "id": "agent-reviewer",
                    "instructions": marker("agent.workflow-maintenance-reviewer"),
                },
            ]
        if args[:2] == ["squad", "list"]:
            return [{"id": "squad-1", "instructions": marker("squad.development-delivery")}]
        if args[:3] == ["squad", "member", "list"]:
            return [{"member_type": "member", "member_id": "human-1", "role": "人工审批人"}]
        raise AssertionError(args)


class RecoveryMultica(FakeMultica):
    def __init__(self):
        super().__init__()
        self.metadata.update(
            {
                "affected_release": "v1.1.0-rc.1",
                "target_release": "v1.1.0-rc.2",
                "recovery_mode": release.RC2_RECOVERY_MODE,
                "pending_incident_source": "WOR-1",
                "source_issue": "WOR-1",
                "recovery_decision_comment_id": "decision-1",
            }
        )
        self.source_metadata = {
            "workflow_incident_pending": True,
            "workflow_incident_pending_index": "development-delivery",
            "workflow_incident_pending_payload": json.dumps(
                {
                    "dedupe_key": "development-delivery:v3:WF-AUTOPILOT-CONTRACT-001:autopilot.workflow-health-audit",
                    "rule_id": "WF-AUTOPILOT-CONTRACT-001",
                }
            ),
            "maintenance_change_id": "T-200",
        }
        self.source_comments = [
            {
                "id": "decision-1",
                "author_type": "member",
                "author_id": "human-1",
                "content": release.RC2_RECOVERY_DECISION,
            }
        ]

    def json(self, args):
        args = list(args)
        if args[:2] == ["issue", "get"]:
            if args[2] == "T-200":
                return {
                    "id": "maintenance-internal",
                    "identifier": "T-200",
                    "parent_issue_id": "source-internal",
                    "status": "in_review",
                }
            if args[2] == "WOR-1":
                return {
                    "id": "source-internal",
                    "identifier": "WOR-1",
                    "status": "blocked",
                }
        if args[:3] == ["issue", "metadata", "list"]:
            return self.source_metadata if args[3] == "WOR-1" else self.metadata
        if args[:3] == ["issue", "comment", "list"]:
            return self.source_comments if args[3] == "WOR-1" else self.comments
        if args[:2] == ["squad", "list"]:
            return []
        return super().json(args)


class ReleaseTests(unittest.TestCase):
    DISPATCHER_APP_ID = 11223
    DISPATCHER_INSTALLATION_ID = 44556
    PUBLISHER_APP_ID = 24680
    PUBLISHER_INSTALLATION_ID = 13579

    @classmethod
    def release_control_evidence_fixture(cls):
        environment_snapshot = {
            "id": 42,
            "name": "workflow-release",
            "protection_rules": [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [
                        {"type": "User", "id": 7, "login": "isolated-reviewer"}
                    ],
                }
            ],
            "admin_bypass": {
                "api_field": "can_admins_bypass",
                "readback_supported": False,
                "status": "not_exposed_by_rest_api",
                "residual_risk": "repository_owner_can_reconfigure_release_controls",
            },
            "deployment_branch_policy": {
                "protected_branches": False,
                "custom_branch_policies": True,
            },
            "deployment_branches": ["main"],
        }
        evidence = {
            "schema_version": 1,
            "status": "reviewed",
            "repository": "rberyou/multica-dev-workflow",
            "recorded_at": "2026-07-19T00:00:00Z",
            "recorded_by": "rberyou",
            "multica_evidence_id": "WOR-44-admin-evidence",
            "environment": {
                "id": 42,
                "name": "workflow-release",
                "sha256": release.digest(environment_snapshot),
            },
            "dispatcher_app": {
                "id": cls.DISPATCHER_APP_ID,
                "slug": "multica-workflow-dispatcher",
                "installation_id": cls.DISPATCHER_INSTALLATION_ID,
                "account_login": "rberyou",
                "repository_selection": "selected",
                "repositories": ["rberyou/multica-dev-workflow"],
                "permissions": {
                    "actions": "write",
                    "contents": "read",
                    "metadata": "read",
                },
            },
            "publisher_app": {
                "id": cls.PUBLISHER_APP_ID,
                "slug": "multica-workflow-publisher",
                "installation_id": cls.PUBLISHER_INSTALLATION_ID,
                "account_login": "rberyou",
                "repository_selection": "selected",
                "repositories": ["rberyou/multica-dev-workflow"],
                "permissions": {"contents": "write", "metadata": "read"},
            },
            "tag_ruleset": {
                "id": 99,
                "name": "workflow-release-tags",
                "source": "rberyou/multica-dev-workflow",
                "target": "tag",
                "enforcement": "active",
                "updated_at": "2026-07-19T00:00:00Z",
                "conditions": {
                    "ref_name": {"include": ["refs/tags/v*"], "exclude": []}
                },
                "rules": [
                    {"type": "creation"},
                    {"type": "update"},
                    {"type": "deletion"},
                ],
                "bypass_actors": [
                    {
                        "actor_type": "Integration",
                        "actor_id": cls.PUBLISHER_APP_ID,
                        "bypass_mode": "always",
                    }
                ],
            },
        }
        evidence["sha256"] = release.digest(evidence)
        return evidence

    def setUp(self):
        self.evidence_patch = patch.object(
            release,
            "load_release_control_evidence",
            side_effect=lambda root, control: self.release_control_evidence_fixture(),
        )
        self.evidence_patch.start()
        self.addCleanup(self.evidence_patch.stop)

    @staticmethod
    def release_boundary_fixture():
        return {
            "repository": "rberyou/multica-dev-workflow",
            "repository_owner": "rberyou",
            "repository_visibility": "public",
            "repository_default_branch": "main",
            "environment": "workflow-release",
            "deployment_branch": "main",
            "operator_type": "github_app",
            "dispatcher_app_id": ReleaseTests.DISPATCHER_APP_ID,
            "dispatcher_app_slug": "multica-workflow-dispatcher",
            "dispatcher_actor_login": "multica-workflow-dispatcher[bot]",
            "dispatcher_installation_id": ReleaseTests.DISPATCHER_INSTALLATION_ID,
            "dispatcher_account_login": "rberyou",
            "dispatcher_repository_selection": "selected",
            "dispatcher_repositories": ["rberyou/multica-dev-workflow"],
            "dispatcher_permissions": {
                "actions": "write",
                "contents": "read",
                "metadata": "read",
            },
            "dispatcher_installation_sha256": release.digest(
                {
                    "app_id": ReleaseTests.DISPATCHER_APP_ID,
                    "app_slug": "multica-workflow-dispatcher",
                    "installation_id": ReleaseTests.DISPATCHER_INSTALLATION_ID,
                    "account_login": "rberyou",
                    "repository_selection": "selected",
                    "repositories": ["rberyou/multica-dev-workflow"],
                    "permissions": {
                        "actions": "write",
                        "contents": "read",
                        "metadata": "read",
                    },
                }
            ),
            "publisher_app_id": ReleaseTests.PUBLISHER_APP_ID,
            "publisher_app_slug": "multica-workflow-publisher",
            "publisher_installation_id": ReleaseTests.PUBLISHER_INSTALLATION_ID,
            "publisher_account_login": "rberyou",
            "publisher_repository_selection": "selected",
            "publisher_repositories": ["rberyou/multica-dev-workflow"],
            "publisher_permissions": {"contents": "write", "metadata": "read"},
            "administrator_evidence_sha256": "4" * 64,
            "administrator_evidence_id": "WOR-44-admin-evidence",
            "reviewers": ["isolated-reviewer"],
            "prevent_self_review": True,
            "admin_bypass": {
                "api_field": "can_admins_bypass",
                "readback_supported": False,
                "status": "not_exposed_by_rest_api",
                "residual_risk": "repository_owner_can_reconfigure_release_controls",
            },
            "environment_sha256": "2" * 64,
            "tag_ruleset": {
                "name": "workflow-release-tags",
                "id": "99",
                "updated_at": "2026-07-19T00:00:00Z",
                "sha256": "3" * 64,
            },
            "dispatcher_token_verified": False,
        }

    @staticmethod
    def protected_environment_gh(
        args,
        reviewer="isolated-reviewer",
        visibility="public",
        permissions=None,
        can_admins_bypass=None,
        dispatcher_app_id=None,
        dispatcher_installation_id=None,
        dispatcher_permissions=None,
        dispatcher_repositories=None,
        branch_policy_marker_count=1,
        deployment_branch_policy=None,
        deployment_branch_policies=None,
        ruleset_bypass_actors="default",
    ):
        dispatcher_app_id = dispatcher_app_id or ReleaseTests.DISPATCHER_APP_ID
        dispatcher_installation_id = (
            dispatcher_installation_id or ReleaseTests.DISPATCHER_INSTALLATION_ID
        )
        dispatcher_permissions = dispatcher_permissions or {
            "actions": "write",
            "contents": "read",
            "metadata": "read",
        }
        dispatcher_repositories = dispatcher_repositories or [
            "rberyou/multica-dev-workflow"
        ]
        if args[:2] == ["api", "installation"]:
            return {
                "id": dispatcher_installation_id,
                "app_id": dispatcher_app_id,
                "app_slug": "multica-workflow-dispatcher",
                "account": {"login": "rberyou"},
                "repository_selection": "selected",
                "permissions": dispatcher_permissions,
            }
        if args[:2] == ["api", "installation/repositories?per_page=100"]:
            return {
                "total_count": len(dispatcher_repositories),
                "repositories": [
                    {"full_name": repository}
                    for repository in dispatcher_repositories
                ],
            }
        if args[:2] == ["api", "repos/rberyou/multica-dev-workflow"]:
            return {
                "visibility": visibility,
                "private": visibility != "public",
                "owner": {"login": "rberyou"},
                "default_branch": "main",
                "permissions": permissions
                or {
                    "admin": False,
                    "maintain": False,
                    "push": False,
                    "triage": True,
                    "pull": True,
                },
            }
        if args[:2] == ["api", "repos/rberyou/multica-dev-workflow/environments/workflow-release"]:
            if deployment_branch_policy is None:
                deployment_branch_policy = {
                    "protected_branches": False,
                    "custom_branch_policies": True,
                }
            protection_rules = [
                {
                    "type": "required_reviewers",
                    "prevent_self_review": True,
                    "reviewers": [
                        {
                            "type": "User",
                            "reviewer": {"id": 7, "login": reviewer},
                        }
                    ],
                }
            ]
            protection_rules.extend(
                {"type": "branch_policy"}
                for _ in range(branch_policy_marker_count)
            )
            detail = {
                "id": 42,
                "name": "workflow-release",
                "protection_rules": protection_rules,
                "deployment_branch_policy": deployment_branch_policy,
            }
            if can_admins_bypass is not None:
                detail["can_admins_bypass"] = can_admins_bypass
            return detail
        if args[:2] == [
            "api",
            "repos/rberyou/multica-dev-workflow/environments/workflow-release/deployment-branch-policies",
        ]:
            if deployment_branch_policies is None:
                deployment_branch_policies = ["main"]
            return {
                "branch_policies": [
                    {"name": name} for name in deployment_branch_policies
                ]
            }
        if args[:2] == ["api", "repos/rberyou/multica-dev-workflow/rulesets"]:
            return [{"id": 99, "name": "workflow-release-tags"}]
        if args[:2] == ["api", "repos/rberyou/multica-dev-workflow/rulesets/99"]:
            ruleset = {
                "id": 99,
                "name": "workflow-release-tags",
                "source": "rberyou/multica-dev-workflow",
                "target": "tag",
                "enforcement": "active",
                "updated_at": "2026-07-19T00:00:00Z",
                "conditions": {
                    "ref_name": {"include": ["refs/tags/v*"], "exclude": []}
                },
                "rules": [
                    {"type": "creation"},
                    {"type": "update"},
                    {"type": "deletion"},
                ],
            }
            if ruleset_bypass_actors == "default":
                ruleset["bypass_actors"] = [
                    {
                        "actor_type": "Integration",
                        "actor_id": ReleaseTests.PUBLISHER_APP_ID,
                        "bypass_mode": "always",
                    }
                ]
            elif ruleset_bypass_actors is not None:
                ruleset["bypass_actors"] = ruleset_bypass_actors
            return ruleset
        if args[:2] == ["api", "apps/multica-workflow-publisher"]:
            return {
                "id": ReleaseTests.PUBLISHER_APP_ID,
                "slug": "multica-workflow-publisher",
            }
        raise AssertionError(args)

    @staticmethod
    def release_request_fixture():
        request = {
            "schema_version": 1,
            "created_at": "2026-07-18T00:00:00Z",
            "version": "1.1.0-rc.4",
            "tag": "v1.1.0-rc.4",
            "source_commit": "a" * 40,
            "origin_main_sha": "a" * 40,
            "source_hash": "b" * 64,
            "merged_pr": {
                "number": 9,
                "head_sha": "c" * 40,
                "merge_commit_sha": "a" * 40,
                "merged_at": "2026-07-18T00:00:00Z",
            },
            "validation": {"databaseId": 123, "headSha": "a" * 40},
            "version_files": ["VERSION"],
            "changelog_hash": "d" * 64,
            "expected_assets": ["checksums.txt", "workflow.zip"],
            "release_plan_digest": "e" * 64,
            "maintenance_provenance": {
                "maintenance_issue": "WOR-43",
                "review_comment_id": "review-1",
                "multica_approval_comment_id": "approval-1",
                "maintenance_evidence_sha256": "f" * 64,
                "multica_approval_author_sha256": "1" * 64,
            },
            "implementation_provenance": [
                {
                    "issue_id": "WOR-45",
                    "maintenance_change_id": "WOR-43",
                    "plan_revision": "v3",
                    "review_comment_id": "review-45",
                    "reviewed_commit_sha": "7" * 40,
                    "github_pr_number": 9,
                    "github_merge_commit_sha": "8" * 40,
                    "github_merged_at": "2026-07-18T00:00:00Z",
                },
                {
                    "issue_id": "WOR-48",
                    "maintenance_change_id": "WOR-44",
                    "plan_revision": "v5",
                    "review_comment_id": "review-48",
                    "reviewed_commit_sha": "9" * 40,
                    "github_pr_number": 10,
                    "github_merge_commit_sha": "a" * 40,
                    "github_merged_at": "2026-07-19T00:00:00Z",
                },
            ],
            "release_control": {
                "repository": "rberyou/multica-dev-workflow",
                "required_visibility": "public",
                "environment": "workflow-release",
                "deployment_branch": "main",
                "operator_type": "github_app",
                "dispatcher_app_slug": "multica-workflow-dispatcher",
                "publisher_app_slug": "multica-workflow-publisher",
                "publisher_app_id_variable": "WORKFLOW_PUBLISHER_APP_ID",
                "publisher_private_key_secret": "WORKFLOW_PUBLISHER_PRIVATE_KEY",
                "tag_ruleset": "workflow-release-tags",
                "administrator_evidence_path": "docs/release-control-evidence.json",
            },
            "release_boundary": release.release_boundary_snapshot(
                ReleaseTests.release_boundary_fixture()
            ),
        }
        request["implementation_provenance_sha256"] = release.digest(
            request["implementation_provenance"]
        )
        request["release_request_digest"] = release.digest(request)
        return request

    def test_all_version_files_match_rc(self):
        checked = release.verify_versions(ROOT, "1.1.0-rc.4")
        self.assertIn("VERSION", checked)
        self.assertIn("skills/multica-workflow-observer/SKILL.md", checked)
        self.assertIn("instructions/roles/leader.md", checked)

    def test_stale_runtime_instruction_version_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp) / "repo"
            shutil.copytree(ROOT, temp_root, ignore=shutil.ignore_patterns(".git", "build", "__pycache__"))
            leader = temp_root / "instructions/roles/leader.md"
            leader.write_text(
                leader.read_text(encoding="utf-8").replace(
                    "workflow_version=1.1.0-rc.4",
                    "workflow_version=1.1.0-rc.1",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(release.ReleaseError, "workflow_version"):
                release.verify_versions(temp_root, "1.1.0-rc.4")

    def test_release_plan_binds_exact_merge_pr_and_validation(self):
        pr = {
            "number": 3,
            "url": "https://example.test/pr/3",
            "title": "workflow v3",
            "headRefOid": "head-sha",
            "baseRefName": "main",
            "mergeCommit": {"oid": "merge-sha"},
            "mergedAt": "2026-07-15T12:00:00Z",
        }
        validation = {
            "databaseId": 10,
            "status": "completed",
            "conclusion": "success",
            "url": "https://example.test/run/10",
            "headSha": "merge-sha",
        }
        with (
            patch.object(release, "git_head", return_value="merge-sha"),
            patch.object(release, "git_dirty", return_value=False),
            patch.object(
                release, "verify_origin_main_reachability", return_value="merge-sha"
            ),
            patch.object(release, "merged_pr_for_commit", return_value=pr),
            patch.object(release, "successful_validation", return_value=validation),
            patch.object(release, "tracked_source_hash", return_value="source-hash"),
            patch.object(release, "verify_versions", return_value=[]),
            patch.object(
                release,
                "gh_json",
                return_value={
                    "mergeCommit": {"oid": BOOTSTRAP_RECORD["plan_pr_merge_commit"]},
                    "comments": [
                        {
                            "id": BOOTSTRAP_RECORD["approval_comment_id"],
                            "author": {"login": BOOTSTRAP_RECORD["approver_github_login"]},
                            "body": BOOTSTRAP_RECORD["approval_text"],
                        }
                    ],
                },
            ),
        ):
            plan = release.build_plan(ROOT, "1.1.0-rc.1", "v6", None)
        self.assertEqual(plan["merged_pr"]["head_sha"], "head-sha")
        self.assertEqual(plan["merged_pr"]["merge_commit_sha"], "merge-sha")
        self.assertEqual(plan["merged_pr"]["merged_at"], "2026-07-15T12:00:00Z")
        self.assertEqual(plan["origin_main_sha"], "merge-sha")
        self.assertEqual(len(plan["release_plan_digest"]), 64)
        self.assertIn("multica-workflow-observer-v1.1.0-rc.1.zip", plan["expected_assets"])
        self.assertIn("multica-workflow-console-v1.1.0-rc.1.zip", plan["expected_assets"])
        self.assertIn(
            "multica-workflow-secure-runtime-win-x64-v1.1.0-rc.1.zip",
            plan["expected_assets"],
        )
        self.assertEqual(plan["release_authorization"]["mode"], "bootstrap")
        self.assertEqual(plan["release_authorization"]["approver_login"], "rberyou")
        self.assertEqual(
            plan["release_authorization"]["plan_approval_comment_id"],
            BOOTSTRAP_RECORD["approval_comment_id"],
        )

    def test_bootstrap_exception_is_limited_to_first_rc(self):
        with self.assertRaisesRegex(release.ReleaseError, "limited to v1.1.0-rc.1"):
            release.bootstrap_evidence(ROOT, "1.1.0", "v6", {"number": 3})

    def test_bootstrap_cannot_authorize_rc2(self):
        with self.assertRaisesRegex(release.ReleaseError, "limited to v1.1.0-rc.1"):
            release.bootstrap_evidence(ROOT, "1.1.0-rc.2", "v6", {"number": 3})

    def test_bootstrap_plan_rejects_approval_text_as_substring(self):
        value = {
            "mergeCommit": {"oid": BOOTSTRAP_RECORD["plan_pr_merge_commit"]},
            "comments": [
                {
                    "id": BOOTSTRAP_RECORD["approval_comment_id"],
                    "author": {"login": BOOTSTRAP_RECORD["approver_github_login"]},
                    "body": f"NOT {BOOTSTRAP_RECORD['approval_text']}",
                }
            ],
        }
        with (
            patch.object(release, "gh_json", return_value=value),
            self.assertRaisesRegex(release.ReleaseError, "approval comment"),
        ):
            release.bootstrap_evidence(ROOT, "1.1.0-rc.1", "v6", {"number": 3})

    def test_bootstrap_release_requires_exact_github_approval_comment(self):
        plan = {
            "release_plan_digest": "c" * 64,
            "release_authorization": {
                "mode": "bootstrap",
                "approver_login": "rberyou",
                "repository": "rberyou/multica-dev-workflow",
                "pr_number": 3,
            },
        }
        comments = {
            "comments": [
                {
                    "id": "comment-1",
                    "url": "https://example.test/comment-1",
                    "author": {"login": "rberyou"},
                    "body": f"APPROVE WORKFLOW RELEASE {plan['release_plan_digest'][:12]}",
                }
            ]
        }
        with (
            patch.object(release, "verify_bootstrap_authorization", return_value=plan["release_authorization"]),
            patch.object(release, "gh_json", return_value=comments),
        ):
            approval = release.verify_bootstrap_release_approval(ROOT, plan)
        self.assertEqual(approval["comment_id"], "comment-1")

    def test_bootstrap_release_rejects_wrong_github_author(self):
        plan = {
            "release_plan_digest": "d" * 64,
            "release_authorization": {
                "mode": "bootstrap",
                "approver_login": "rberyou",
                "repository": "rberyou/multica-dev-workflow",
                "pr_number": 3,
            },
        }
        comments = {
            "comments": [
                {
                    "id": "comment-2",
                    "author": {"login": "someone-else"},
                    "body": f"APPROVE WORKFLOW RELEASE {plan['release_plan_digest'][:12]}",
                }
            ]
        }
        with (
            patch.object(release, "verify_bootstrap_authorization", return_value=plan["release_authorization"]),
            patch.object(release, "gh_json", return_value=comments),
            self.assertRaisesRegex(release.ReleaseError, "found 0"),
        ):
            release.verify_bootstrap_release_approval(ROOT, plan)

    def test_github_release_approval_requires_exact_maintenance_provenance(self):
        digest_value = "e" * 64
        provenance = {
            "maintenance_issue": "T-200",
            "review_comment_id": "review-1",
            "multica_approval_comment_id": "approval-1",
            "maintenance_evidence_sha256": "1" * 64,
            "multica_approval_author_sha256": "2" * 64,
            "review_issue_id": "WOR-14",
            "batch_review_mappings_sha256": "3" * 64,
        }
        comment = {
            "comments": [
                {
                    "id": "comment-3",
                    "author": {"login": "rberyou"},
                    "body": f"APPROVE WORKFLOW RELEASE {digest_value[:12]}",
                }
            ]
        }
        with (
            patch.object(release, "gh_json", return_value=comment),
            self.assertRaisesRegex(release.ReleaseError, "found 0"),
        ):
            release.verify_github_release_approval(
                ROOT, digest_value, 3, expected_provenance=provenance
            )

        comment["comments"][0]["body"] += "\n" + "\n".join(
            f"{key}={value}" for key, value in provenance.items()
        )
        with patch.object(release, "gh_json", return_value=comment):
            approval = release.verify_github_release_approval(
                ROOT, digest_value, 3, expected_provenance=provenance
            )
        self.assertEqual(approval["comment_id"], "comment-3")

    def test_maintenance_github_approval_block_contains_bound_provenance(self):
        authorization = {
            "issue_id": "T-200",
            "review_comment_id": "review-1",
            "human_approver_id": "human-1",
        }
        approval = {"comment_id": "approval-1", "author_id": "human-1"}
        provenance = release.maintenance_github_provenance(authorization, approval)
        block = release.github_release_approval_block("a" * 64, provenance)
        self.assertIn("APPROVE WORKFLOW RELEASE aaaaaaaaaaaa", block)
        self.assertIn("maintenance_issue=T-200", block)
        self.assertIn("multica_approval_comment_id=approval-1", block)
        self.assertNotIn("human-1", block)

    def test_maintenance_evidence_binds_managed_reviewer_and_pr_head(self):
        cli = FakeMultica()
        pr = maintenance_pr()
        evidence = release.maintenance_evidence(ROOT, cli, "T-200", pr)
        self.assertEqual(evidence["maintenance_reviewer_id"], "agent-reviewer")
        self.assertEqual(evidence["reviewed_commit_sha"], "head-sha")
        self.assertEqual(evidence["review_comment_id"], "review-1")
        self.assertEqual(evidence["github_merged_at"], pr["mergedAt"])

    def test_maintenance_evidence_resolves_bounded_implementation_review(self):
        cli = bounded_batch_cli()
        evidence = release.maintenance_evidence(ROOT, cli, "T-200", maintenance_pr())
        self.assertEqual(evidence["review_issue_id"], "WOR-14")
        self.assertEqual(evidence["review_comment_id"], "review-1")
        self.assertEqual(evidence["plan_revision"], "v1")
        self.assertIn("batch_review_mappings_sha256", evidence)

    def test_planned_implementation_revalidation_avoids_dispatcher_pr_api(self):
        cli = FakeMultica()
        cli.issues["WOR-48"] = {
            "id": "implementation-internal",
            "identifier": "WOR-48",
            "status": "done",
        }
        cli.metadata_by_issue["WOR-48"] = {
            "workflow_id": "development-delivery",
            "workflow_object_type": "maintenance_implementation",
            "maintenance_change_id": "WOR-44",
            "maintenance_reviewer_id": "agent-reviewer",
            "plan_revision": "v5",
            "reviewed_commit_sha": "9" * 40,
            "review_comment_id": "review-48",
            "github_pr_number": "10",
            "github_merge_commit_sha": "a" * 40,
        }
        cli.comments_by_issue["WOR-48"] = [
            {
                "id": "review-48",
                "author_type": "agent",
                "author_id": "agent-reviewer",
                "created_at": "2026-07-18T23:00:00Z",
                "content": (
                    "APPROVED\nplan_revision=v5\n"
                    f"reviewed_commit_sha={'9' * 40}"
                ),
            }
        ]
        planned = {
            "issue_id": "WOR-48",
            "maintenance_change_id": "WOR-44",
            "plan_revision": "v5",
            "review_comment_id": "review-48",
            "reviewed_commit_sha": "9" * 40,
            "github_pr_number": 10,
            "github_merge_commit_sha": "a" * 40,
            "github_merged_at": "2026-07-19T00:00:00Z",
        }
        with patch.object(release, "merged_pr_by_number") as pr_mock:
            evidence = release.maintenance_implementation_evidence(
                ROOT, cli, "WOR-48", planned
            )
        pr_mock.assert_not_called()
        self.assertEqual(evidence, planned)

    def test_bounded_implementation_review_must_belong_to_maintenance_change(self):
        cases = {
            "wrong parent": lambda cli: cli.issues["WOR-14"].update(
                {"parent_issue_id": "other-maintenance"}
            ),
            "wrong maintenance_change_id": lambda cli: cli.metadata_by_issue[
                "WOR-14"
            ].update({"maintenance_change_id": "T-999"}),
            "wrong object type": lambda cli: cli.metadata_by_issue["WOR-14"].update(
                {"workflow_object_type": "maintenance_change"}
            ),
            "wrong source": lambda cli: cli.metadata_by_issue["WOR-14"].update(
                {"source_incident_id": "WOR-999"}
            ),
            "wrong review sha": lambda cli: cli.metadata_by_issue["WOR-14"].update(
                {"reviewed_commit_sha": "old-head"}
            ),
            "wrong reviewer": lambda cli: cli.metadata_by_issue["WOR-14"].update(
                {"maintenance_reviewer_id": "agent-maintainer"}
            ),
            "unreadable target": lambda cli: cli.issues.update({"WOR-14": []}),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                cli = bounded_batch_cli()
                mutate(cli)
                with self.assertRaises(release.ReleaseError):
                    release.maintenance_evidence(
                        ROOT, cli, "T-200", maintenance_pr()
                    )

    def test_delivery_batch_review_requires_exact_mappings(self):
        cli = bounded_batch_cli()
        cli.comments_by_issue["WOR-14"][0]["content"] = bounded_review_content().replace(
            "implementation=WOR-33", "implementation=WOR-34"
        )
        with self.assertRaisesRegex(release.ReleaseError, "batch mappings"):
            release.maintenance_evidence(ROOT, cli, "T-200", maintenance_pr())

        cli = bounded_batch_cli()
        cli.metadata["batch_review_mappings"] = json.dumps(BATCH_MAPPINGS[:2])
        with self.assertRaisesRegex(release.ReleaseError, "batch mappings"):
            release.maintenance_evidence(ROOT, cli, "T-200", maintenance_pr())

        cli = bounded_batch_cli()
        cli.metadata.pop("review_issue_id")
        with self.assertRaisesRegex(release.ReleaseError, "review_issue_id"):
            release.maintenance_evidence(ROOT, cli, "T-200", maintenance_pr())

    def test_maintenance_github_provenance_contains_bounded_review_hashes(self):
        authorization = release.maintenance_evidence(
            ROOT, bounded_batch_cli(), "T-200", maintenance_pr()
        )
        provenance = release.maintenance_github_provenance(
            authorization, {"comment_id": "approval-1", "author_id": "human-1"}
        )
        self.assertEqual(provenance["review_issue_id"], "WOR-14")
        self.assertEqual(
            provenance["batch_review_mappings_sha256"],
            authorization["batch_review_mappings_sha256"],
        )

    def test_rc2_pending_incident_exception_is_bounded_and_hash_only(self):
        cli = RecoveryMultica()
        evidence = release.maintenance_evidence(
            ROOT, cli, "T-200", maintenance_pr(), "1.1.0-rc.2"
        )
        self.assertEqual(evidence["recovery_mode"], release.RC2_RECOVERY_MODE)
        self.assertEqual(evidence["pending_incident_source"], "WOR-1")
        encoded = json.dumps(evidence, ensure_ascii=False)
        self.assertNotIn("agent-maintainer", encoded)
        self.assertNotIn("agent-reviewer", encoded)
        self.assertNotIn("human-1", encoded)
        self.assertNotIn("dedupe_key", encoded)

        provenance = release.maintenance_github_provenance(
            evidence, {"comment_id": "approval-1", "author_id": "human-1"}
        )
        self.assertEqual(provenance["recovery_mode"], release.RC2_RECOVERY_MODE)
        self.assertIn("pending_incident_evidence_sha256", provenance)
        self.assertNotIn("human-1", json.dumps(provenance))

    def test_rc2_pending_incident_exception_rejects_stale_or_reused_evidence(self):
        cases = {
            "not pending": lambda cli: cli.source_metadata.update(
                {"workflow_incident_pending": False}
            ),
            "already linked": lambda cli: cli.source_metadata.update(
                {"workflow_incident_id": "WOR-9"}
            ),
            "missing dedupe": lambda cli: cli.source_metadata.update(
                {
                    "workflow_incident_pending_payload": json.dumps(
                        {"rule_id": "WF-AUTOPILOT-CONTRACT-001"}
                    )
                }
            ),
            "wrong decision author": lambda cli: cli.source_comments[0].update(
                {"author_id": "human-2"}
            ),
            "altered decision": lambda cli: cli.source_comments[0].update(
                {"content": release.RC2_RECOVERY_DECISION + " changed"}
            ),
            "wrong target release": lambda cli: cli.metadata.update(
                {"target_release": "v1.1.0-rc.3"}
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label):
                cli = RecoveryMultica()
                mutate(cli)
                with self.assertRaises(release.ReleaseError):
                    release.maintenance_evidence(
                        ROOT, cli, "T-200", maintenance_pr(), "1.1.0-rc.2"
                    )

        with self.assertRaisesRegex(release.ReleaseError, "limited to 1.1.0-rc.2"):
            release.maintenance_evidence(
                ROOT,
                RecoveryMultica(),
                "T-200",
                maintenance_pr(),
                "1.1.0-rc.3",
            )

    def test_maintenance_review_must_predate_merge(self):
        cli = FakeMultica()
        cli.comments[0]["created_at"] = "2026-07-15T12:00:01Z"
        with self.assertRaisesRegex(release.ReleaseError, "before the PR is merged"):
            release.maintenance_evidence(ROOT, cli, "T-200", maintenance_pr())

    def test_maintenance_review_requires_timestamps(self):
        cli = FakeMultica()
        cli.comments[0].pop("created_at")
        with self.assertRaisesRegex(release.ReleaseError, "timestamp is missing"):
            release.maintenance_evidence(ROOT, cli, "T-200", maintenance_pr())

    def test_maintenance_review_by_maintainer_is_rejected(self):
        cli = FakeMultica()
        cli.comments[0]["author_id"] = "agent-maintainer"
        pr = maintenance_pr()
        with self.assertRaisesRegex(release.ReleaseError, "not authored by the managed Maintenance Reviewer"):
            release.maintenance_evidence(ROOT, cli, "T-200", pr)

    def test_maintenance_review_requires_approved_as_first_verdict(self):
        cli = FakeMultica()
        cli.comments[0]["content"] = (
            "CHANGES_REQUESTED\n"
            "quoted prior verdict:\n"
            "APPROVED\n"
            "plan_revision=v3\n"
            "reviewed_commit_sha=head-sha"
        )
        pr = maintenance_pr()
        with self.assertRaisesRegex(release.ReleaseError, "first non-empty line"):
            release.maintenance_evidence(ROOT, cli, "T-200", pr)

    def test_maintenance_review_requires_exact_unique_binding_lines(self):
        cli = FakeMultica()
        cli.comments[0]["content"] = (
            "APPROVED\n"
            "This review discusses plan_revision v3 and reviewed_commit_sha head-sha."
        )
        pr = maintenance_pr()
        with self.assertRaisesRegex(release.ReleaseError, "exact plan_revision"):
            release.maintenance_evidence(ROOT, cli, "T-200", pr)

        cli.comments[0]["content"] = (
            "APPROVED\n"
            "plan_revision=v3\n"
            "plan_revision=v2\n"
            "reviewed_commit_sha=head-sha"
        )
        with self.assertRaisesRegex(release.ReleaseError, "exact plan_revision"):
            release.maintenance_evidence(ROOT, cli, "T-200", pr)

    def test_stale_maintenance_review_is_rejected(self):
        cli = FakeMultica()
        cli.metadata["reviewed_commit_sha"] = "old-head"
        pr = maintenance_pr()
        with self.assertRaisesRegex(release.ReleaseError, "stale"):
            release.maintenance_evidence(ROOT, cli, "T-200", pr)

    def test_release_apply_evidence_requires_exact_human_digest_comment(self):
        cli = FakeMultica()
        authorization = release.maintenance_evidence(
            ROOT,
            cli,
            "T-200",
            maintenance_pr(),
        )
        plan = {
            "release_plan_digest": "a" * 64,
            "merged_pr": {
                "number": 3,
                "head_sha": "head-sha",
                "merge_commit_sha": "merge-sha",
                "merged_at": maintenance_pr()["mergedAt"],
            },
            "release_authorization": authorization,
        }
        cli.comments.append(
            {
                "id": "approval-1",
                "author_type": "member",
                "author_id": "human-1",
                "content": f"APPROVE WORKFLOW RELEASE {plan['release_plan_digest'][:12]}",
            }
        )
        approval = release.verify_release_approval(ROOT, cli, plan)
        self.assertEqual(approval["comment_id"], "approval-1")

    def test_release_approval_from_non_approver_is_rejected(self):
        cli = FakeMultica()
        authorization = release.maintenance_evidence(
            ROOT,
            cli,
            "T-200",
            maintenance_pr(),
        )
        plan = {
            "release_plan_digest": "b" * 64,
            "merged_pr": {
                "number": 3,
                "head_sha": "head-sha",
                "merge_commit_sha": "merge-sha",
                "merged_at": maintenance_pr()["mergedAt"],
            },
            "release_authorization": authorization,
        }
        cli.comments.append(
            {
                "id": "approval-2",
                "author_type": "member",
                "author_id": "human-2",
                "content": f"APPROVE WORKFLOW RELEASE {plan['release_plan_digest'][:12]}",
            }
        )
        with self.assertRaisesRegex(release.ReleaseError, "found 0"):
            release.verify_release_approval(ROOT, cli, plan)

    def test_modified_release_plan_is_rejected(self):
        plan = {"schema_version": 1, "draft": False, "version": "1.1.0-rc.1"}
        plan["release_plan_digest"] = release.digest(plan)
        plan["version"] = "1.1.0"
        with self.assertRaisesRegex(release.ReleaseError, "digest is invalid"):
            release.verify_plan_file(plan, plan["release_plan_digest"][:12])

    def test_release_environment_requires_isolated_reviewer(self):
        calls = []

        def fake_gh(root, args):
            calls.append(list(args))
            return self.protected_environment_gh(
                args, ruleset_bypass_actors=None
            )

        with (
            patch.dict(release.os.environ, {"GH_TOKEN": "dispatcher-token"}, clear=True),
            patch.object(release, "gh_json", side_effect=fake_gh),
        ):
            boundary = release.verify_release_environment(ROOT)
        self.assertEqual(boundary["reviewers"], ["isolated-reviewer"])
        self.assertEqual(boundary["operator_type"], "github_app")
        self.assertEqual(boundary["dispatcher_app_id"], self.DISPATCHER_APP_ID)
        self.assertTrue(boundary["dispatcher_token_verified"])
        self.assertEqual(boundary["publisher_app_id"], self.PUBLISHER_APP_ID)
        self.assertFalse(
            any(
                args[:2] == ["api", "apps/multica-workflow-publisher"]
                for args in calls
            )
        )

    def test_release_environment_requires_one_branch_policy_marker_and_main_policy(self):
        marker_cases = {
            "missing": 0,
            "duplicate": 2,
        }
        for label, count in marker_cases.items():
            with self.subTest(case=label):
                with (
                    patch.object(
                        release,
                        "gh_json",
                        side_effect=lambda root, args, count=count: self.protected_environment_gh(
                            args, branch_policy_marker_count=count
                        ),
                    ),
                    self.assertRaisesRegex(release.ReleaseError, "exactly one branch_policy"),
                ):
                    release.verify_release_environment(ROOT, verify_dispatcher_token=False)

        policy_cases = {
            "protected branches": {
                "deployment_branch_policy": {
                    "protected_branches": True,
                    "custom_branch_policies": True,
                }
            },
            "not custom": {
                "deployment_branch_policy": {
                    "protected_branches": False,
                    "custom_branch_policies": False,
                }
            },
            "wrong branch": {"deployment_branch_policies": ["release"]},
            "extra branch": {"deployment_branch_policies": ["main", "release"]},
        }
        for label, kwargs in policy_cases.items():
            with self.subTest(case=label):
                pattern = "branch policies must equal" if label in {"wrong branch", "extra branch"} else "custom main-only"
                with (
                    patch.object(
                        release,
                        "gh_json",
                        side_effect=lambda root, args, kwargs=kwargs: self.protected_environment_gh(
                            args, **kwargs
                        ),
                    ),
                    self.assertRaisesRegex(release.ReleaseError, pattern),
                ):
                    release.verify_release_environment(ROOT, verify_dispatcher_token=False)

    def test_release_tag_ruleset_bypass_actors_fail_closed_when_present_invalid(self):
        cases = {
            "non-list": (
                {"actor_type": "Integration"},
                "bypass_actors must be a list",
            ),
            "malformed": (
                [
                    {
                        "actor_type": "Integration",
                        "actor_id": self.PUBLISHER_APP_ID,
                    }
                ],
                "malformed bypass actor",
            ),
            "stale-id": (
                [
                    {
                        "actor_type": "Integration",
                        "actor_id": self.PUBLISHER_APP_ID + 1,
                        "bypass_mode": "always",
                    }
                ],
                "Publisher App bypass actor ID differs",
            ),
            "wrong-actor": (
                [
                    {
                        "actor_type": "RepositoryRole",
                        "actor_id": self.PUBLISHER_APP_ID,
                        "bypass_mode": "always",
                    }
                ],
                "sole always Integration bypass",
            ),
            "extra-actor": (
                [
                    {
                        "actor_type": "Integration",
                        "actor_id": self.PUBLISHER_APP_ID,
                        "bypass_mode": "always",
                    },
                    {
                        "actor_type": "RepositoryRole",
                        "actor_id": 5,
                        "bypass_mode": "always",
                    },
                ],
                "exactly one bypass actor",
            ),
        }
        for label, (bypass_actors, pattern) in cases.items():
            with self.subTest(case=label):
                with (
                    patch.object(
                        release,
                        "gh_json",
                        side_effect=lambda root, args, bypass_actors=bypass_actors: self.protected_environment_gh(
                            args, ruleset_bypass_actors=bypass_actors
                        ),
                    ),
                    self.assertRaisesRegex(release.ReleaseError, pattern),
                ):
                    release.verify_release_environment(ROOT, verify_dispatcher_token=False)

    def test_release_environment_requires_public_repository(self):
        with (
            patch.dict(release.os.environ, {"GH_TOKEN": "dispatcher-token"}, clear=True),
            patch.object(
                release,
                "gh_json",
                side_effect=lambda root, args: self.protected_environment_gh(
                    args, visibility="private"
                ),
            ),
            self.assertRaisesRegex(release.ReleaseError, "visibility must be public"),
        ):
            release.verify_release_environment(ROOT)

    def test_dispatcher_token_is_limited_to_reviewed_installation(self):
        evidence = self.release_control_evidence_fixture()
        with (
            patch.dict(release.os.environ, {"GH_TOKEN": "dispatcher-token"}, clear=True),
            patch.object(
                release,
                "gh_json",
                side_effect=lambda root, args: self.protected_environment_gh(args),
            ),
        ):
            dispatcher = release.verify_dispatcher_installation(
                ROOT,
                release.release_control(ROOT),
                evidence,
            )
        self.assertEqual(dispatcher["app_id"], self.DISPATCHER_APP_ID)
        self.assertEqual(
            dispatcher["permissions"],
            {"actions": "write", "contents": "read", "metadata": "read"},
        )

    def test_dispatcher_token_requires_explicit_gh_token(self):
        with (
            patch.dict(release.os.environ, {}, clear=True),
            self.assertRaisesRegex(release.ReleaseError, "explicitly through GH_TOKEN"),
        ):
            release.verify_dispatcher_installation(
                ROOT,
                release.release_control(ROOT),
                self.release_control_evidence_fixture(),
            )

    def test_dispatcher_token_allows_host_credentials_to_remain(self):
        with (
            patch.dict(
                release.os.environ,
                {
                    "GH_TOKEN": "dispatcher-token",
                    "GITHUB_TOKEN": "unrelated-host-token",
                    "GH_CONFIG_DIR": "C:/host/gh",
                    "GIT_SSH_COMMAND": "ssh -i C:/host/id_ed25519",
                },
                clear=True,
            ),
            patch.object(
                release,
                "gh_json",
                side_effect=lambda root, args: self.protected_environment_gh(args),
            ),
        ):
            dispatcher = release.verify_dispatcher_installation(
                ROOT,
                release.release_control(ROOT),
                self.release_control_evidence_fixture(),
            )
        self.assertEqual(dispatcher["installation_id"], self.DISPATCHER_INSTALLATION_ID)

    def test_dispatcher_token_rejects_identity_scope_or_permission_drift(self):
        cases = {
            "wrong app": {
                "dispatcher_app_id": self.DISPATCHER_APP_ID + 1,
            },
            "wrong installation": {
                "dispatcher_installation_id": self.DISPATCHER_INSTALLATION_ID + 1,
            },
            "wrong repository": {
                "dispatcher_repositories": ["rberyou/another-repository"],
            },
            "permission mismatch": {
                "dispatcher_permissions": {
                    "actions": "write",
                    "contents": "write",
                    "metadata": "read",
                },
            },
        }
        for label, case in cases.items():
            with self.subTest(case=label):
                with (
                    patch.dict(
                        release.os.environ,
                        {"GH_TOKEN": "dispatcher-token"},
                        clear=True,
                    ),
                    patch.object(
                        release,
                        "gh_json",
                        side_effect=lambda root, args, case=case: self.protected_environment_gh(
                            args, **case
                        ),
                    ),
                    self.assertRaisesRegex(release.ReleaseError, "does not match reviewed"),
                ):
                    release.verify_dispatcher_installation(
                        ROOT,
                        release.release_control(ROOT),
                        self.release_control_evidence_fixture(),
                    )

    def test_release_control_hashes_include_full_normalized_configuration(self):
        with patch.object(
            release,
            "gh_json",
            side_effect=lambda root, args: self.protected_environment_gh(args),
        ):
            baseline = release.verify_release_environment(
                ROOT, verify_dispatcher_token=False
            )

        def changed_environment(root, args):
            value = self.protected_environment_gh(args)
            if args[:2] == [
                "api",
                "repos/rberyou/multica-dev-workflow/environments/workflow-release",
            ]:
                value = json.loads(json.dumps(value))
                value["protection_rules"].append(
                    {"type": "wait_timer", "wait_timer": 5}
                )
            return value

        with (
            patch.object(release, "gh_json", side_effect=changed_environment),
            self.assertRaisesRegex(release.ReleaseError, "Environment differs"),
        ):
            release.verify_release_environment(ROOT, verify_dispatcher_token=False)

        def changed_ruleset(root, args):
            value = self.protected_environment_gh(args)
            if args[:2] == [
                "api",
                "repos/rberyou/multica-dev-workflow/rulesets/99",
            ]:
                value = json.loads(json.dumps(value))
                value["rules"].append(
                    {"type": "required_signatures", "parameters": {"required": True}}
                )
            return value

        with (
            patch.object(release, "gh_json", side_effect=changed_ruleset),
            self.assertRaisesRegex(release.ReleaseError, "Ruleset public readback differs"),
        ):
            release.verify_release_environment(ROOT, verify_dispatcher_token=False)

    def test_release_environment_records_or_rejects_admin_bypass(self):
        with patch.object(
            release,
            "gh_json",
            side_effect=lambda root, args: self.protected_environment_gh(args),
        ):
            unsupported = release.verify_release_environment(
                ROOT, verify_dispatcher_token=False
            )
        self.assertIs(
            unsupported["admin_bypass"]["readback_supported"], False
        )
        self.assertIn("residual_risk", unsupported["admin_bypass"])

        with (
            patch.object(
                release,
                "gh_json",
                side_effect=lambda root, args: self.protected_environment_gh(
                    args, can_admins_bypass=False
                ),
            ),
            self.assertRaisesRegex(release.ReleaseError, "Environment differs"),
        ):
            release.verify_release_environment(ROOT, verify_dispatcher_token=False)

        with (
            patch.object(
                release,
                "gh_json",
                side_effect=lambda root, args: self.protected_environment_gh(
                    args, can_admins_bypass=True
                ),
            ),
            self.assertRaisesRegex(release.ReleaseError, "administrator bypass"),
        ):
            release.verify_release_environment(ROOT, verify_dispatcher_token=False)

    def test_environment_approval_is_bound_to_required_reviewer(self):
        def fake_gh(root, args):
            if args[:2] == [
                "api",
                "repos/rberyou/multica-dev-workflow/actions/runs/123/approvals",
            ]:
                return [
                    {
                        "id": 7,
                        "state": "approved",
                        "user": {"login": "isolated-reviewer"},
                        "environments": [{"name": "workflow-release"}],
                    }
                ]
            return self.protected_environment_gh(args)

        with patch.object(release, "gh_json", side_effect=fake_gh):
            approval = release.verify_environment_approval(ROOT, "123")
        self.assertEqual(approval["actor_login"], "isolated-reviewer")
        self.assertRegex(approval["approval_sha256"], r"^[a-f0-9]{64}$")

    def test_release_request_rejects_modification(self):
        request = self.release_request_fixture()
        with (
            patch.dict(release.os.environ, {}, clear=True),
            patch.object(release, "verify_current_state"),
            patch.object(
                release,
                "release_control",
                return_value=request["release_control"],
            ),
        ):
            self.assertEqual(
                release.verify_release_request(
                    ROOT, request, expected_source=request["source_commit"]
                ),
                request["release_request_digest"],
            )
            request["tag"] = "v1.1.0-rc.4-forged"
            with self.assertRaisesRegex(release.ReleaseError, "digest is invalid"):
                release.verify_release_request(ROOT, request)

    def test_release_request_requires_reviewed_dispatcher_actor_in_actions(self):
        request = self.release_request_fixture()
        with (
            patch.dict(
                release.os.environ,
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_ACTOR": "different-actor",
                },
                clear=True,
            ),
            patch.object(release, "verify_current_state"),
            patch.object(
                release,
                "release_control",
                return_value=request["release_control"],
            ),
            self.assertRaisesRegex(release.ReleaseError, "reviewed Dispatcher App"),
        ):
            release.verify_release_request(
                ROOT, request, expected_source=request["source_commit"]
            )

        forged = json.loads(json.dumps(request))
        forged["release_boundary"]["dispatcher_actor_login"] = "different-actor"
        forged["release_request_digest"] = release.digest(
            {
                key: value
                for key, value in forged.items()
                if key != "release_request_digest"
            }
        )
        with (
            patch.dict(
                release.os.environ,
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_ACTOR": "different-actor",
                },
                clear=True,
            ),
            patch.object(release, "verify_current_state"),
            patch.object(
                release,
                "release_control",
                return_value=request["release_control"],
            ),
            self.assertRaisesRegex(release.ReleaseError, "trusted release control"),
        ):
            release.verify_release_request(
                ROOT, forged, expected_source=forged["source_commit"]
            )

        with (
            patch.dict(
                release.os.environ,
                {
                    "GITHUB_ACTIONS": "true",
                    "GITHUB_ACTOR": "multica-workflow-dispatcher[bot]",
                },
                clear=True,
            ),
            patch.object(release, "verify_current_state"),
            patch.object(
                release,
                "release_control",
                return_value=request["release_control"],
            ),
        ):
            self.assertEqual(
                release.verify_release_request(
                    ROOT, request, expected_source=request["source_commit"]
                ),
                request["release_request_digest"],
            )

    def test_rc4_release_request_requires_both_implementation_records(self):
        request = self.release_request_fixture()
        request["implementation_provenance"] = request["implementation_provenance"][:1]
        request["implementation_provenance_sha256"] = release.digest(
            request["implementation_provenance"]
        )
        request["release_request_digest"] = release.digest(
            {key: value for key, value in request.items() if key != "release_request_digest"}
        )
        with (
            patch.dict(release.os.environ, {}, clear=True),
            patch.object(release, "verify_current_state"),
            patch.object(
                release,
                "release_control",
                return_value=request["release_control"],
            ),
            self.assertRaisesRegex(release.ReleaseError, "bind two Implementation Issues"),
        ):
            release.verify_release_request(ROOT, request)

    def test_publish_gate_is_digest_bound_and_rejects_tampering(self):
        request = self.release_request_fixture()
        boundary = self.release_boundary_fixture()
        approval = {
            "run_id": "123",
            "environment": "workflow-release",
            "actor_login": "isolated-reviewer",
            "approval_sha256": "2" * 64,
        }
        gate = release.publish_gate_record(request, boundary, approval)
        self.assertEqual(
            release.verify_publish_gate(
                gate,
                request,
                gate["publish_gate_digest"],
                workflow_run_id="123",
                environment="workflow-release",
            ),
            gate["publish_gate_digest"],
        )
        gate["tag"] = "v1.1.0-rc.4-forged"
        with self.assertRaisesRegex(release.ReleaseError, "gate digest is invalid"):
            release.verify_publish_gate(
                gate,
                request,
                gate["publish_gate_digest"],
                workflow_run_id="123",
                environment="workflow-release",
            )

    def test_publisher_token_is_limited_to_reviewed_installation(self):
        evidence = self.release_control_evidence_fixture()

        def publisher_gh(root, args):
            if args[:2] == ["api", "installation"]:
                publisher = evidence["publisher_app"]
                return {
                    "id": publisher["installation_id"],
                    "app_id": publisher["id"],
                    "app_slug": publisher["slug"],
                    "account": {"login": publisher["account_login"]},
                    "repository_selection": publisher["repository_selection"],
                    "permissions": publisher["permissions"],
                }
            if args[:2] == ["api", "installation/repositories?per_page=100"]:
                return {
                    "total_count": 1,
                    "repositories": [{"full_name": "rberyou/multica-dev-workflow"}],
                }
            raise AssertionError(args)

        with (
            patch.dict(release.os.environ, {"GH_TOKEN": "publisher-token"}, clear=True),
            patch.object(release, "gh_json", side_effect=publisher_gh),
        ):
            publisher = release.verify_publisher_installation(
                ROOT,
                release.release_control(ROOT),
                evidence,
            )
        self.assertEqual(publisher["installation_id"], self.PUBLISHER_INSTALLATION_ID)

        overprivileged = json.loads(json.dumps(evidence))
        overprivileged["publisher_app"]["permissions"]["administration"] = "write"
        with (
            patch.dict(release.os.environ, {"GH_TOKEN": "publisher-token"}, clear=True),
            patch.object(release, "gh_json", side_effect=publisher_gh),
            self.assertRaisesRegex(release.ReleaseError, "does not match reviewed"),
        ):
            release.verify_publisher_installation(
                ROOT,
                release.release_control(ROOT),
                overprivileged,
            )

    def test_protected_tag_uses_git_database_api_without_git_push(self):
        request = self.release_request_fixture()
        boundary = self.release_boundary_fixture()
        gate = release.publish_gate_record(
            request,
            boundary,
            {
                "run_id": "123",
                "environment": "workflow-release",
                "actor_login": "isolated-reviewer",
                "approval_sha256": "2" * 64,
            },
        )
        publisher = {
            "app_id": self.PUBLISHER_APP_ID,
            "app_slug": "multica-workflow-publisher",
            "installation_id": self.PUBLISHER_INSTALLATION_ID,
            "sha256": "6" * 64,
        }

        def fake_run(args, root, check=True):
            if args[:3] == ["git", "tag", "--list"]:
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            if args[:3] == ["git", "ls-remote", "--tags"]:
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            if args[:3] == ["git", "fetch", "--no-tags"]:
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            raise AssertionError(args)

        with (
            patch.object(release, "run", side_effect=fake_run) as run_mock,
            patch.object(
                release,
                "gh_api_json",
                side_effect=[
                    {"sha": "7" * 40},
                    {"ref": "refs/tags/v1.1.0-rc.4", "object": {"sha": "7" * 40}},
                ],
            ) as api_mock,
        ):
            result = release.create_protected_tag(
                ROOT, request, gate, publisher
            )
        self.assertEqual(result["tag_object_sha"], "7" * 40)
        self.assertEqual(api_mock.call_args_list[0].args[1:3], ("POST", "repos/rberyou/multica-dev-workflow/git/tags"))
        self.assertEqual(api_mock.call_args_list[1].args[1:3], ("POST", "repos/rberyou/multica-dev-workflow/git/refs"))
        self.assertFalse(
            any(call.args[0][:2] == ["git", "push"] for call in run_mock.call_args_list)
        )

    def test_local_release_apply_only_dispatches_request(self):
        request = self.release_request_fixture()
        plan = {
            "release_plan_digest": "e" * 64,
            "release_authorization": {
                "mode": "maintenance",
                "workspace_id": "workspace-test",
            },
        }
        args = SimpleNamespace(
            plan="plan.json",
            approve="e" * 12,
            multica_bin=None,
            profile=None,
            workspace=None,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            plan_path = root / "plan.json"
            plan_path.write_text(json.dumps(plan), encoding="utf-8")
            args.plan = str(plan_path)
            fake_cli = SimpleNamespace(workspace_id="workspace-test")
            with (
                patch.object(release, "verify_plan_file", return_value="e" * 64),
                patch.object(
                    release,
                    "verify_dispatcher_installation",
                    return_value={"app_id": self.DISPATCHER_APP_ID},
                ),
                patch.object(release, "verify_current_state") as state_mock,
                patch.object(release, "release_cli", return_value=(fake_cli, {})),
                patch.object(
                    release,
                    "verify_release_approval",
                    return_value={"comment_id": "approval-1", "author_id": "human-1"},
                ),
                patch.object(release, "release_request", return_value=request),
                patch.object(
                    release,
                    "verify_release_environment",
                    return_value={
                        "repository": "rberyou/multica-dev-workflow",
                        "deployment_branch": "main",
                        "environment": "workflow-release",
                    },
                ),
                patch.object(
                    release,
                    "save_release_request",
                    return_value=root / "request.json",
                ),
                patch.object(
                    release,
                    "run",
                    return_value=SimpleNamespace(stdout="", stderr="", returncode=0),
                ) as run_mock,
                patch("builtins.print"),
            ):
                self.assertEqual(release.command_apply(args, root), 0)
        state_mock.assert_called_once_with(root, plan, verify_merged_pr=False)
        calls = [call.args[0] for call in run_mock.call_args_list]
        self.assertEqual(calls[0][:3], ["gh", "workflow", "run"])
        self.assertFalse(any(call[:2] == ["git", "tag"] for call in calls))
        self.assertFalse(any(call[:2] == ["git", "push"] for call in calls))
        self.assertFalse(any(call[:3] == ["gh", "release", "create"] for call in calls))

    def test_protected_tag_message_binds_environment_evidence(self):
        request = self.release_request_fixture()
        gate = {
            "publish_gate_digest": "5" * 64,
            "environment_approval": {
            "run_id": "123",
            "environment": "workflow-release",
            "actor_login": "isolated-reviewer",
            "approval_sha256": "2" * 64,
            },
        }
        publisher = {
            "app_id": self.PUBLISHER_APP_ID,
            "app_slug": "multica-workflow-publisher",
            "installation_id": self.PUBLISHER_INSTALLATION_ID,
            "sha256": "6" * 64,
        }
        message = release.protected_tag_message(request, gate, publisher)
        self.assertIn("authorization_mode=protected_environment", message)
        self.assertIn("release_workflow_run_id=123", message)
        self.assertIn("environment_approval_actor=isolated-reviewer", message)
        self.assertIn("release_operator_type=github_app", message)
        self.assertIn("dispatcher_app_slug=multica-workflow-dispatcher", message)
        self.assertIn(
            "dispatcher_actor_login=multica-workflow-dispatcher[bot]", message
        )
        self.assertIn(
            f"dispatcher_installation_id={self.DISPATCHER_INSTALLATION_ID}",
            message,
        )
        self.assertIn("publisher_app_slug=multica-workflow-publisher", message)
        self.assertIn("publish_gate_digest=" + "5" * 64, message)
        self.assertIn("repository_visibility=public", message)
        self.assertIn("release_environment_sha256=" + "2" * 64, message)
        self.assertIn("release_ruleset_id=99", message)
        self.assertIn("release_ruleset_sha256=" + "3" * 64, message)

    def test_publish_checks_environment_approval_before_tag_mutation(self):
        request = self.release_request_fixture()
        args = SimpleNamespace(
            request="request.json",
            gate="gate.json",
            approve="5" * 64,
            workflow_run_id="123",
            environment="workflow-release",
            recover_existing_tag=False,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            args.request = str(request_path)
            with (
                patch.dict(
                    release.os.environ,
                    {
                        "GITHUB_ACTIONS": "true",
                        "GITHUB_RUN_ID": "123",
                        "GITHUB_REPOSITORY": "rberyou/multica-dev-workflow",
                    },
                    clear=True,
                ),
                patch.object(release, "git_head", return_value=request["source_commit"]),
                patch.object(release, "verify_release_request"),
                patch.object(
                    release,
                    "release_control",
                    return_value=request["release_control"],
                ),
                patch.object(
                    release,
                    "load_publish_gate",
                    return_value={"publish_gate_digest": "5" * 64},
                ),
                patch.object(
                    release,
                    "verify_publish_gate",
                    side_effect=release.ReleaseError("approval missing"),
                ),
                patch.object(release, "run") as run_mock,
                self.assertRaisesRegex(release.ReleaseError, "approval missing"),
            ):
                release.command_publish(args, root)
        self.assertFalse(any(call.args[0][:2] == ["git", "tag"] for call in run_mock.call_args_list))
        self.assertFalse(any(call.args[0][:2] == ["git", "push"] for call in run_mock.call_args_list))

    def test_release_recovery_accepts_new_gate_for_existing_request_bound_tag(self):
        request = self.release_request_fixture()
        gate = {"publish_gate_digest": "5" * 64}
        annotation = (
            f"release_request_digest={request['release_request_digest']}\n"
            f"publish_gate_digest={'4' * 64}\n"
        )
        args = SimpleNamespace(
            request="request.json",
            gate="gate.json",
            approve="5" * 64,
            workflow_run_id="123",
            environment="workflow-release",
            directory="assets",
            recover_existing_tag=True,
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            args.request = str(request_path)
            args.directory = str(root / "assets")
            Path(args.directory).mkdir()
            with (
                patch.dict(
                    release.os.environ,
                    {
                        "GITHUB_ACTIONS": "true",
                        "GITHUB_RUN_ID": "123",
                        "GITHUB_REPOSITORY": "rberyou/multica-dev-workflow",
                        "GITHUB_ACTOR": "multica-workflow-dispatcher[bot]",
                    },
                    clear=True,
                ),
                patch.object(release, "git_head", return_value=request["source_commit"]),
                patch.object(release, "verify_release_request"),
                patch.object(
                    release,
                    "release_control",
                    return_value=request["release_control"],
                ),
                patch.object(release, "load_publish_gate", return_value=gate),
                patch.object(release, "verify_publish_gate"),
                patch.object(
                    release,
                    "verify_publisher_installation",
                    return_value={"app_id": self.PUBLISHER_APP_ID},
                ),
                patch.object(release, "annotated_tag_contents", return_value=annotation),
                patch.object(release, "verify_asset_directory", return_value=["asset.zip"]),
                patch.object(
                    release,
                    "run",
                    side_effect=[
                        SimpleNamespace(stdout="", stderr="not found", returncode=1),
                        SimpleNamespace(stdout="", stderr="", returncode=0),
                    ],
                ),
                patch("builtins.print"),
            ):
                self.assertEqual(release.command_publish_release(args, root), 0)

        args.recover_existing_tag = False
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            request_path = root / "request.json"
            request_path.write_text(json.dumps(request), encoding="utf-8")
            args.request = str(request_path)
            args.directory = str(root)
            with (
                patch.dict(
                    release.os.environ,
                    {
                        "GITHUB_ACTIONS": "true",
                        "GITHUB_RUN_ID": "123",
                        "GITHUB_REPOSITORY": "rberyou/multica-dev-workflow",
                    },
                    clear=True,
                ),
                patch.object(release, "git_head", return_value=request["source_commit"]),
                patch.object(release, "verify_release_request"),
                patch.object(
                    release,
                    "release_control",
                    return_value=request["release_control"],
                ),
                patch.object(release, "load_publish_gate", return_value=gate),
                patch.object(release, "verify_publish_gate"),
                patch.object(
                    release,
                    "verify_publisher_installation",
                    return_value={"app_id": self.PUBLISHER_APP_ID},
                ),
                patch.object(release, "annotated_tag_contents", return_value=annotation),
                self.assertRaisesRegex(release.ReleaseError, "approved publish gate"),
            ):
                release.command_publish_release(args, root)

    def test_exact_validation_run_is_reverified(self):
        validation = {
            "databaseId": 10,
            "status": "completed",
            "conclusion": "success",
            "url": "https://example.test/run/10",
            "event": "push",
            "headSha": "merge-sha",
            "createdAt": "2026-07-15T00:00:00Z",
        }
        with patch.object(release, "gh_json", return_value=validation):
            current = release.verify_validation_record(ROOT, validation, "merge-sha")
        self.assertEqual(current["databaseId"], 10)

    def test_changed_validation_run_provenance_is_rejected(self):
        planned = {
            "databaseId": 10,
            "status": "completed",
            "conclusion": "success",
            "url": "https://example.test/run/10",
            "event": "push",
            "headSha": "merge-sha",
            "createdAt": "2026-07-15T00:00:00Z",
        }
        current = {**planned, "url": "https://example.test/run/replaced"}
        with (
            patch.object(release, "gh_json", return_value=current),
            self.assertRaisesRegex(release.ReleaseError, "changed after release planning"),
        ):
            release.verify_validation_record(ROOT, planned, "merge-sha")

    def test_dispatcher_preflight_defers_pr_readback_but_rechecks_ci(self):
        plan = {
            "source_commit": "merge-sha",
            "source_hash": "source-hash",
            "origin_main_sha": "merge-sha",
            "version": "1.1.0-rc.4",
            "merged_pr": {
                "number": 10,
                "head_sha": "head-sha",
                "merged_at": "2026-07-19T00:00:00Z",
            },
            "validation": {"databaseId": 123},
        }
        with (
            patch.object(release, "git_dirty", return_value=False),
            patch.object(release, "git_head", return_value="merge-sha"),
            patch.object(release, "tracked_source_hash", return_value="source-hash"),
            patch.object(
                release,
                "verify_origin_main_reachability",
                return_value="merge-sha",
            ),
            patch.object(release, "verify_versions"),
            patch.object(release, "merged_pr_for_commit") as pr_mock,
            patch.object(release, "verify_validation_record") as validation_mock,
        ):
            release.verify_current_state(ROOT, plan, verify_merged_pr=False)
        pr_mock.assert_not_called()
        validation_mock.assert_called_once_with(ROOT, plan["validation"], "merge-sha")

    def test_release_preflight_rejects_source_behind_origin_main(self):
        plan = {
            "source_commit": "merge-sha",
            "source_hash": "source-hash",
            "origin_main_sha": "new-main-sha",
            "version": "1.1.0-rc.4",
            "validation": {"databaseId": 123},
        }
        with (
            patch.object(release, "git_dirty", return_value=False),
            patch.object(release, "git_head", return_value="merge-sha"),
            patch.object(release, "tracked_source_hash", return_value="source-hash"),
            patch.object(
                release,
                "verify_origin_main_reachability",
                return_value="new-main-sha",
            ),
            self.assertRaisesRegex(release.ReleaseError, "origin/main tip"),
        ):
            release.verify_current_state(ROOT, plan, verify_merged_pr=False)

    def test_release_commit_must_be_reachable_from_origin_main(self):
        def fake_run(args, root, check=True):
            if args[:3] == ["git", "fetch", "--no-tags"]:
                return SimpleNamespace(stdout="", stderr="", returncode=0)
            if args[:3] == ["git", "rev-parse", "origin/main"]:
                return SimpleNamespace(stdout="b" * 40 + "\n", stderr="", returncode=0)
            if args[:3] == ["git", "merge-base", "--is-ancestor"]:
                return SimpleNamespace(stdout="", stderr="", returncode=1)
            raise AssertionError(args)

        with (
            patch.object(release, "run", side_effect=fake_run),
            self.assertRaisesRegex(release.ReleaseError, "not reachable"),
        ):
            release.verify_origin_main_reachability(ROOT, "a" * 40)

    def test_bootstrap_authorization_is_recomputed_exactly(self):
        authorization = {
            "mode": "bootstrap",
            "plan": "docs/design-plan-v6.md",
            "plan_sha256": "1" * 64,
            "approval_record_sha256": "2" * 64,
            "plan_approval_comment_id": "plan-comment",
            "approver_login": "rberyou",
            "repository": "rberyou/multica-dev-workflow",
            "pr_number": 3,
        }
        plan = {
            "version": "1.1.0-rc.1",
            "bootstrap_plan": "v6",
            "merged_pr": {"number": 3},
            "release_authorization": authorization,
        }
        with patch.object(release, "bootstrap_evidence", return_value=authorization):
            self.assertEqual(release.verify_bootstrap_authorization(ROOT, plan), authorization)
        for field in [
            "plan_sha256",
            "approval_record_sha256",
            "plan_approval_comment_id",
        ]:
            with self.subTest(field=field):
                stale = {**authorization}
                stale.pop(field)
                plan["release_authorization"] = stale
                with (
                    patch.object(release, "bootstrap_evidence", return_value=authorization),
                    self.assertRaisesRegex(release.ReleaseError, "evidence changed"),
                ):
                    release.verify_bootstrap_authorization(ROOT, plan)
        plan["release_authorization"] = authorization

    def test_release_asset_directory_rejects_missing_and_extra_files(self):
        annotation = asset_annotation(["checksums.txt", "workflow.zip"])
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            (directory / "checksums.txt").write_text("hash\n", encoding="utf-8")
            with self.assertRaisesRegex(release.ReleaseError, "missing=.*workflow.zip"):
                release.verify_asset_directory(annotation, directory)
            (directory / "workflow.zip").write_bytes(b"zip")
            self.assertEqual(
                release.verify_asset_directory(annotation, directory),
                ["checksums.txt", "workflow.zip"],
            )
            (directory / "unexpected.txt").write_text("extra", encoding="utf-8")
            with self.assertRaisesRegex(release.ReleaseError, "extra=.*unexpected.txt"):
                release.verify_asset_directory(annotation, directory)

    def test_release_verification_rejects_lightweight_tags(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "config", "user.email", "test@example.com"],
                cwd=root,
                check=True,
            )
            subprocess.run(
                ["git", "config", "user.name", "Test"], cwd=root, check=True
            )
            (root / "payload.txt").write_text("payload\n", encoding="utf-8")
            subprocess.run(["git", "add", "payload.txt"], cwd=root, check=True)
            fake_message = (
                "fake release metadata\n\n"
                "release_plan_digest=" + "f" * 64 + "\n"
                "expected_assets_sha256=" + "1" * 64 + "\n"
                "expected_asset=checksums.txt\n"
            )
            subprocess.run(
                ["git", "commit", "-m", fake_message],
                cwd=root,
                check=True,
                capture_output=True,
            )
            subprocess.run(
                ["git", "tag", "v1.1.0-rc.1"], cwd=root, check=True
            )
            with (
                patch.object(release, "verify_versions", return_value=[]),
                self.assertRaisesRegex(release.ReleaseError, "annotated tag"),
            ):
                release.command_verify_tag(
                    SimpleNamespace(tag="v1.1.0-rc.1"), root
                )
            with self.assertRaisesRegex(release.ReleaseError, "annotated tag"):
                release.command_verify_assets(
                    SimpleNamespace(tag="v1.1.0-rc.1", directory=str(root)),
                    root,
                )

    def test_verify_tag_rejects_annotated_pr_that_is_not_the_tag_commit_pr(self):
        commit = "a" * 40
        annotation = (
            "release_plan_digest=" + "f" * 64 + "\n"
            f"source_commit={commit}\n"
            "merged_pr=3\n"
            "validation_run_id=10\n"
            "authorization_mode=maintenance\n"
            "github_approval_comment_id=github-approval-1\n"
            "maintenance_issue=T-200\n"
            "review_comment_id=review-1\n"
            "multica_approval_comment_id=approval-1\n"
            "maintenance_evidence_sha256=" + "1" * 64 + "\n"
            "multica_approval_author_sha256=" + "2" * 64 + "\n"
            + asset_annotation(["checksums.txt", "workflow.zip"])
        )

        def fake_run(args, root, check=True):
            if args[:3] == ["git", "rev-list", "-n"]:
                return SimpleNamespace(stdout=commit + "\n", stderr="", returncode=0)
            if args[:3] == ["git", "cat-file", "-t"]:
                return SimpleNamespace(stdout="tag\n", stderr="", returncode=0)
            if args[:3] == ["git", "tag", "-l"]:
                return SimpleNamespace(stdout=annotation, stderr="", returncode=0)
            raise AssertionError(args)

        with (
            patch.object(release, "verify_versions", return_value=[]),
            patch.object(release, "run", side_effect=fake_run),
            patch.object(release, "verify_origin_main_reachability", return_value="b" * 40),
            patch.object(release, "merged_pr_for_commit", return_value={"number": 4}),
            self.assertRaisesRegex(release.ReleaseError, "annotated merged PR"),
        ):
            release.command_verify_tag(SimpleNamespace(tag="v1.1.0-rc.1"), ROOT)

    def test_verify_tag_rechecks_exact_github_maintenance_provenance(self):
        commit = "a" * 40
        digest_value = "f" * 64
        provenance = {
            "maintenance_issue": "T-200",
            "review_comment_id": "review-1",
            "multica_approval_comment_id": "approval-1",
            "maintenance_evidence_sha256": "1" * 64,
            "multica_approval_author_sha256": "2" * 64,
        }
        annotation = "\n".join(
            [
                f"release_plan_digest={digest_value}",
                f"source_commit={commit}",
                "merged_pr=3",
                "validation_run_id=10",
                "authorization_mode=maintenance",
                "github_approval_comment_id=github-approval-1",
                *(f"{key}={value}" for key, value in provenance.items()),
                asset_annotation(["checksums.txt", "workflow.zip"]).strip(),
                "",
            ]
        )
        validation = {
            "databaseId": 10,
            "status": "completed",
            "conclusion": "success",
            "headSha": commit,
        }

        def fake_run(args, root, check=True):
            if args[:3] == ["git", "rev-list", "-n"]:
                return SimpleNamespace(stdout=commit + "\n", stderr="", returncode=0)
            if args[:3] == ["git", "cat-file", "-t"]:
                return SimpleNamespace(stdout="tag\n", stderr="", returncode=0)
            if args[:3] == ["git", "tag", "-l"]:
                return SimpleNamespace(stdout=annotation, stderr="", returncode=0)
            raise AssertionError(args)

        with (
            patch.object(release, "verify_versions", return_value=[]),
            patch.object(release, "run", side_effect=fake_run),
            patch.object(release, "verify_origin_main_reachability", return_value="b" * 40),
            patch.object(release, "merged_pr_for_commit", return_value={"number": 3}),
            patch.object(release, "gh_json", return_value=validation),
            patch.object(
                release,
                "verify_github_release_approval",
                return_value={"comment_id": "github-approval-1"},
            ) as verify_github,
            patch("builtins.print"),
        ):
            result = release.command_verify_tag(
                SimpleNamespace(tag="v1.1.0-rc.1"), ROOT
            )
        self.assertEqual(result, 0)
        verify_github.assert_called_once_with(
            ROOT, digest_value, 3, "github-approval-1", provenance
        )


if __name__ == "__main__":
    unittest.main()
