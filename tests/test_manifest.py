from pathlib import Path
import json
import re
import shutil
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_lib import WorkflowError, validate_repository  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_all_deployment_profiles_validate(self):
        for profile in ["quality", "codex-only", "opencode-only"]:
            manifest, deployment = validate_repository(ROOT, profile)
            self.assertEqual(manifest["schema_version"], 2)
            self.assertEqual(deployment["name"], profile)

    def test_schema_and_manifest_are_json(self):
        json.loads((ROOT / "workflow.json").read_text(encoding="utf-8"))
        json.loads((ROOT / "workflow.schema.json").read_text(encoding="utf-8"))

    def test_portable_files_have_no_concrete_ids_or_user_paths(self):
        uuid = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
        user_path = re.compile(r"(?:[A-Za-z]:\\Users\\[^<\\]+|/Users/[^<\s/]+|/home/[^<\s/]+)")
        token = re.compile(r"\b(?:mul|gho)_[A-Za-z0-9_-]{8,}\b")
        paths = [
            ROOT / "workflow.json",
            *ROOT.glob("deployment-profiles/*.json"),
            *ROOT.glob("instructions/**/*.md"),
            *ROOT.glob("skills/**/*.md"),
            *ROOT.glob("skills/**/*.yaml"),
            *ROOT.glob("skills/**/*.py"),
            *ROOT.glob("docs/*.md"),
            *ROOT.glob("docs/*.json"),
            *ROOT.glob(".github/**/*.yml"),
            *ROOT.glob(".github/**/*.md"),
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(uuid.search(text), path)
            self.assertIsNone(user_path.search(text), path)
            self.assertIsNone(token.search(text), path)

    def test_skills_have_management_metadata(self):
        manifest = json.loads((ROOT / "workflow.json").read_text(encoding="utf-8"))
        for skill in [item["name"] for item in manifest["skills"]]:
            content = (ROOT / f"skills/{skill}/SKILL.md").read_text(encoding="utf-8")
            self.assertIn("managed_by: multica-dev-workflow", content)
            self.assertIn("workflow_id: development-delivery", content)

    def test_scheduled_observer_keeps_health_monitoring_out_of_band(self):
        manifest = json.loads((ROOT / "workflow.json").read_text(encoding="utf-8"))
        autopilot = next(
            item
            for item in manifest["autopilots"]
            if item["key"] == "workflow-health-audit"
        )
        self.assertIn("audit --scope issues --report", autopilot["description"])
        self.assertIn("workflow.py health", autopilot["description"])

    def test_release_workflow_resolves_explicit_tag_commit(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn(
            "if: github.event_name == 'push' || github.ref == 'refs/heads/main'",
            workflow,
        )
        self.assertIn(
            'temporary_ref="refs/release-fetch/${GITHUB_RUN_ID}"', workflow
        )
        self.assertNotIn("git fetch --force", workflow)
        self.assertIn(
            'test "$(git rev-parse "${remote_tag_ref}")" = "${remote_tag_object}"',
            workflow,
        )
        self.assertIn(
            'test "${release_sha}" = "$(git rev-parse HEAD)"', workflow
        )
        self.assertIn('RELEASE_SHA=${release_sha}', workflow)
        self.assertIn('merge-base --is-ancestor "${RELEASE_SHA}"', workflow)
        self.assertIn('gh run list --commit "${RELEASE_SHA}"', workflow)
        self.assertIn(
            'python scripts/release.py verify-tag --tag "${RELEASE_TAG}"',
            workflow,
        )
        self.assertIn(
            'python scripts/release.py verify-assets --tag "${RELEASE_TAG}"',
            workflow,
        )
        self.assertIn('gh release create "${RELEASE_TAG}"', workflow)
        self.assertNotIn(
            'python scripts/release.py verify-tag --tag "${GITHUB_REF_NAME}"',
            workflow,
        )

    def test_operations_contract_is_required_for_v2(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp) / "repo"
            shutil.copytree(
                ROOT,
                temp_root,
                ignore=shutil.ignore_patterns(
                    ".git", ".multica", "build", "__pycache__", "*.pyc"
                ),
            )
            manifest_path = temp_root / "workflow.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest.pop("operations")
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "operations"):
                validate_repository(temp_root, "quality")

    def test_operations_reporters_must_match_squad_contract(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_root = Path(temp) / "repo"
            shutil.copytree(
                ROOT,
                temp_root,
                ignore=shutil.ignore_patterns(
                    ".git", ".multica", "build", "__pycache__", "*.pyc"
                ),
            )
            manifest_path = temp_root / "workflow.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["operations"]["reporter_agents"].pop()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "exactly match"):
                validate_repository(temp_root, "quality")


if __name__ == "__main__":
    unittest.main()
