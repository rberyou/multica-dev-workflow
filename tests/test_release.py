import importlib.util
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
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
                "content": "APPROVED\nplan_revision=v3\nreviewed_commit_sha=head-sha",
            }
        ]

    def json(self, args):
        args = list(args)
        if args[:2] == ["issue", "get"]:
            return {"id": args[2], "identifier": args[2], "status": "in_review"}
        if args[:3] == ["issue", "metadata", "list"]:
            return self.metadata
        if args[:3] == ["issue", "comment", "list"]:
            return self.comments
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


class ReleaseTests(unittest.TestCase):
    def test_all_version_files_match_rc(self):
        checked = release.verify_versions(ROOT, "1.1.0-rc.1")
        self.assertIn("VERSION", checked)
        self.assertIn("skills/multica-workflow-observer/SKILL.md", checked)

    def test_release_plan_binds_exact_merge_pr_and_validation(self):
        pr = {
            "number": 3,
            "url": "https://example.test/pr/3",
            "title": "workflow v3",
            "headRefOid": "head-sha",
            "baseRefName": "main",
            "mergeCommit": {"oid": "merge-sha"},
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
            patch.object(
                release,
                "gh_json",
                return_value={
                    "mergeCommit": {"oid": "7e288e3f238daac5ddc71884c5d2b5b36353d74d"},
                    "comments": [
                        {
                            "id": "IC_kwDOTY4IZs8AAAABKL59XA",
                            "author": {"login": "rberyou"},
                            "body": "APPROVE WORKFLOW PLAN v6",
                        }
                    ],
                },
            ),
        ):
            plan = release.build_plan(ROOT, "1.1.0-rc.1", "v6", None)
        self.assertEqual(plan["merged_pr"]["head_sha"], "head-sha")
        self.assertEqual(plan["merged_pr"]["merge_commit_sha"], "merge-sha")
        self.assertEqual(plan["origin_main_sha"], "main-sha")
        self.assertEqual(len(plan["release_plan_digest"]), 64)
        self.assertIn("multica-workflow-observer-v1.1.0-rc.1.zip", plan["expected_assets"])
        self.assertEqual(plan["release_authorization"]["mode"], "bootstrap")
        self.assertEqual(plan["release_authorization"]["approver_login"], "rberyou")
        self.assertEqual(
            plan["release_authorization"]["plan_approval_comment_id"],
            "IC_kwDOTY4IZs8AAAABKL59XA",
        )

    def test_bootstrap_exception_is_limited_to_first_rc(self):
        with self.assertRaisesRegex(release.ReleaseError, "limited to v1.1.0-rc.1"):
            release.bootstrap_evidence(ROOT, "1.1.0", "v6", {"number": 3})

    def test_bootstrap_plan_rejects_approval_text_as_substring(self):
        value = {
            "mergeCommit": {"oid": "7e288e3f238daac5ddc71884c5d2b5b36353d74d"},
            "comments": [
                {
                    "id": "IC_kwDOTY4IZs8AAAABKL59XA",
                    "author": {"login": "rberyou"},
                    "body": "NOT APPROVE WORKFLOW PLAN v6",
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
        with patch.object(release, "gh_json", return_value=comments):
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
        pr = {"number": 3, "headRefOid": "head-sha", "mergeCommit": {"oid": "merge-sha"}}
        evidence = release.maintenance_evidence(ROOT, cli, "T-200", pr)
        self.assertEqual(evidence["maintenance_reviewer_id"], "agent-reviewer")
        self.assertEqual(evidence["reviewed_commit_sha"], "head-sha")
        self.assertEqual(evidence["review_comment_id"], "review-1")

    def test_maintenance_review_by_maintainer_is_rejected(self):
        cli = FakeMultica()
        cli.comments[0]["author_id"] = "agent-maintainer"
        pr = {"number": 3, "headRefOid": "head-sha", "mergeCommit": {"oid": "merge-sha"}}
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
        pr = {"number": 3, "headRefOid": "head-sha", "mergeCommit": {"oid": "merge-sha"}}
        with self.assertRaisesRegex(release.ReleaseError, "first non-empty line"):
            release.maintenance_evidence(ROOT, cli, "T-200", pr)

    def test_maintenance_review_requires_exact_unique_binding_lines(self):
        cli = FakeMultica()
        cli.comments[0]["content"] = (
            "APPROVED\n"
            "This review discusses plan_revision v3 and reviewed_commit_sha head-sha."
        )
        pr = {"number": 3, "headRefOid": "head-sha", "mergeCommit": {"oid": "merge-sha"}}
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
        pr = {"number": 3, "headRefOid": "head-sha", "mergeCommit": {"oid": "merge-sha"}}
        with self.assertRaisesRegex(release.ReleaseError, "stale"):
            release.maintenance_evidence(ROOT, cli, "T-200", pr)

    def test_release_apply_evidence_requires_exact_human_digest_comment(self):
        cli = FakeMultica()
        authorization = release.maintenance_evidence(
            ROOT,
            cli,
            "T-200",
            {"number": 3, "headRefOid": "head-sha", "mergeCommit": {"oid": "merge-sha"}},
        )
        plan = {
            "release_plan_digest": "a" * 64,
            "merged_pr": {
                "number": 3,
                "head_sha": "head-sha",
                "merge_commit_sha": "merge-sha",
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
            {"number": 3, "headRefOid": "head-sha", "mergeCommit": {"oid": "merge-sha"}},
        )
        plan = {
            "release_plan_digest": "b" * 64,
            "merged_pr": {
                "number": 3,
                "head_sha": "head-sha",
                "merge_commit_sha": "merge-sha",
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
        )

        def fake_run(args, root, check=True):
            if args[:3] == ["git", "rev-list", "-n"]:
                return SimpleNamespace(stdout=commit + "\n", stderr="", returncode=0)
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
