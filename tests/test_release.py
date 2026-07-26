from pathlib import Path
import argparse
import json
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release  # noqa: E402


VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
HEAD = subprocess.run(
    ["git", "rev-parse", "HEAD"],
    cwd=ROOT,
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()


def completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


class ReleaseTests(unittest.TestCase):
    def test_repository_versions_and_expected_assets_match(self):
        checked = release.verify_versions(ROOT, VERSION)
        assets = release.expected_assets(ROOT, VERSION)
        self.assertIn("VERSION", checked)
        self.assertIn("CHANGELOG.md", checked)
        self.assertIn("checksums.txt", assets)
        self.assertIn(f"multica-workflow-incidents-v{VERSION}.zip", assets)
        self.assertNotIn("allow-non-main", release.parser().format_help())
        subparsers = next(
            action
            for action in release.parser()._actions
            if action.__class__.__name__ == "_SubParsersAction"
        )
        for command in ["package", "publish"]:
            with self.subTest(command=command):
                self.assertNotIn(
                    "approve",
                    {action.dest for action in subparsers.choices[command]._actions},
                )

    def test_verify_versions_rejects_invalid_or_mismatched_version(self):
        with self.assertRaisesRegex(release.ReleaseError, "semantic version"):
            release.verify_versions(ROOT, "not-a-version")
        with self.assertRaisesRegex(release.ReleaseError, "VERSION does not match"):
            release.verify_versions(ROOT, "9.9.9")

    @patch.object(release, "validate_repository")
    @patch.object(release, "git_dirty", return_value=False)
    @patch.object(release, "git_head", return_value="a" * 40)
    @patch.object(release, "git_branch", return_value="main")
    def test_build_plan_binds_clean_main_checkout(
        self, _branch, _head, _dirty, _validate
    ):
        plan = release.build_plan(ROOT, VERSION)
        self.assertFalse(plan["draft"])
        self.assertEqual(plan["tag"], f"v{VERSION}")
        self.assertEqual(
            release.digest(
                {key: value for key, value in plan.items() if key != "release_plan_digest"}
            ),
            plan["release_plan_digest"],
        )

    @patch.object(release, "validate_repository")
    @patch.object(release, "git_dirty", return_value=False)
    @patch.object(release, "git_head", return_value="a" * 40)
    @patch.object(release, "git_branch", return_value="feature")
    def test_build_plan_rejects_non_main(self, _branch, _head, _dirty, _validate):
        with self.assertRaisesRegex(release.ReleaseError, "from main"):
            release.build_plan(ROOT, VERSION)

    @patch.object(release, "validate_repository")
    @patch.object(release, "git_dirty", return_value=True)
    @patch.object(release, "git_head", return_value="a" * 40)
    @patch.object(release, "git_branch", return_value="main")
    def test_dirty_release_plan_is_draft(self, _branch, _head, _dirty, _validate):
        self.assertTrue(release.build_plan(ROOT, VERSION)["draft"])

    def test_verify_plan_rejects_tamper_and_dirty_checkout(self):
        plan = {
            "schema_version": 1,
            "created_at": "2026-01-01T00:00:00Z",
            "version": VERSION,
            "tag": f"v{VERSION}",
            "branch": "main",
            "source_commit": "a" * 40,
            "draft": False,
            "version_files": ["VERSION"],
            "expected_assets": ["checksums.txt"],
        }
        plan["release_plan_digest"] = release.digest(plan)
        tampered = {**plan, "tag": "v9.9.9"}
        with self.assertRaisesRegex(release.ReleaseError, "Plan digest"):
            release.verify_plan(ROOT, tampered)
        with patch.object(release, "git_dirty", return_value=True):
            with self.assertRaisesRegex(release.ReleaseError, "working tree is dirty"):
                release.verify_plan(ROOT, plan)

    def test_package_release_creates_exact_assets_and_checksums(self):
        plan = {
            "version": VERSION,
            "tag": f"v{VERSION}",
            "source_commit": HEAD,
            "expected_assets": release.expected_assets(ROOT, VERSION),
        }
        with tempfile.TemporaryDirectory() as temp:
            assets = release.package_release(ROOT, plan, Path(temp))
            self.assertEqual(sorted(path.name for path in assets), plan["expected_assets"])
            checksums = (Path(temp) / "checksums.txt").read_text(encoding="utf-8")
            for name in plan["expected_assets"]:
                if name != "checksums.txt":
                    self.assertIn(name, checksums)

    def test_publish_uses_human_host_gh_release_create(self):
        plan = {
            "tag": f"v{VERSION}",
            "version": VERSION,
            "source_commit": "a" * 40,
        }
        args = argparse.Namespace(plan="plan.json", directory="build", prerelease=False)
        assets = [Path("one.zip"), Path("checksums.txt")]
        with (
            patch.object(release, "load_plan", return_value=plan),
            patch.object(release, "verify_plan"),
            patch.object(release, "package_release", return_value=assets),
            patch.object(release, "run", return_value=completed()) as run,
        ):
            self.assertEqual(release.command_publish(args, ROOT), 0)
        command = run.call_args.args[0]
        self.assertEqual(command[:3], ["gh", "release", "create"])
        self.assertIn("--prerelease", command)

    def test_publish_refuses_daemon_agent_identity(self):
        args = argparse.Namespace(plan="plan.json", directory="build", prerelease=False)
        with patch.dict("os.environ", {"MULTICA_AGENT_ID": "agent-test"}, clear=True):
            with self.assertRaisesRegex(release.ReleaseError, "human host"):
                release.command_publish(args, ROOT)

    def test_verify_tag_checks_version_and_exact_release_assets(self):
        expected = release.expected_assets(ROOT, VERSION)

        def fake_run(args, root, check=True):
            if args[:3] == ["git", "rev-list", "-n"]:
                return completed("b" * 40 + "\n")
            if args[:2] == ["git", "show"] and args[2].endswith(":VERSION"):
                return completed(VERSION + "\n")
            if args[:2] == ["git", "show"] and args[2].endswith(":workflow.json"):
                return completed((ROOT / "workflow.json").read_text(encoding="utf-8"))
            if args[:2] == ["git", "show"] and args[2].endswith(":CHANGELOG.md"):
                return completed((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
            if args[:2] == ["git", "show"] and args[2].endswith("/SKILL.md"):
                relative = args[2].split(":", 1)[1]
                return completed((ROOT / relative).read_text(encoding="utf-8"))
            if args[:3] == ["gh", "release", "view"]:
                return completed(
                    json.dumps(
                        {"tagName": f"v{VERSION}", "assets": [{"name": name} for name in expected]}
                    )
                )
            raise AssertionError(args)

        args = argparse.Namespace(tag=f"v{VERSION}")
        with patch.object(release, "run", side_effect=fake_run):
            self.assertEqual(release.command_verify_tag(args, ROOT), 0)

    def test_verify_tag_rejects_unexpected_assets(self):
        def fake_run(args, root, check=True):
            if args[:3] == ["git", "rev-list", "-n"]:
                return completed("b" * 40 + "\n")
            if args[:2] == ["git", "show"] and args[2].endswith(":VERSION"):
                return completed(VERSION + "\n")
            if args[:2] == ["git", "show"] and args[2].endswith(":workflow.json"):
                return completed((ROOT / "workflow.json").read_text(encoding="utf-8"))
            if args[:2] == ["git", "show"] and args[2].endswith(":CHANGELOG.md"):
                return completed((ROOT / "CHANGELOG.md").read_text(encoding="utf-8"))
            if args[:2] == ["git", "show"] and args[2].endswith("/SKILL.md"):
                relative = args[2].split(":", 1)[1]
                return completed((ROOT / relative).read_text(encoding="utf-8"))
            return completed(json.dumps({"tagName": f"v{VERSION}", "assets": []}))

        with patch.object(release, "run", side_effect=fake_run):
            with self.assertRaisesRegex(release.ReleaseError, "assets differ"):
                release.command_verify_tag(argparse.Namespace(tag=f"v{VERSION}"), ROOT)

    def test_verify_tag_rejects_stale_skill_version(self):
        def fake_run(args, root, check=True):
            if args[:3] == ["git", "rev-list", "-n"]:
                return completed("b" * 40 + "\n")
            if args[:2] == ["git", "show"] and args[2].endswith(":VERSION"):
                return completed(VERSION + "\n")
            if args[:2] == ["git", "show"] and args[2].endswith(":workflow.json"):
                return completed((ROOT / "workflow.json").read_text(encoding="utf-8"))
            if args[:2] == ["git", "show"] and args[2].endswith("/SKILL.md"):
                return completed("---\nname: stale\nmetadata:\n  version: 1.0.0\n---\n")
            raise AssertionError(args)

        with patch.object(release, "run", side_effect=fake_run):
            with self.assertRaisesRegex(release.ReleaseError, "Skill version"):
                release.command_verify_tag(
                    argparse.Namespace(tag=f"v{VERSION}"), ROOT
                )


if __name__ == "__main__":
    unittest.main()
