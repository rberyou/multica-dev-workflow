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
    def test_all_version_files_match_rc(self):
        checked = release.verify_versions(ROOT, "1.1.0-rc.3")
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
                    "workflow_version=1.1.0-rc.3",
                    "workflow_version=1.1.0-rc.1",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(release.ReleaseError, "workflow_version"):
                release.verify_versions(temp_root, "1.1.0-rc.3")

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
                release, "verify_origin_main_reachability", return_value="main-sha"
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
        self.assertEqual(plan["origin_main_sha"], "main-sha")
        self.assertEqual(len(plan["release_plan_digest"]), 64)
        self.assertIn("multica-workflow-observer-v1.1.0-rc.1.zip", plan["expected_assets"])
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
