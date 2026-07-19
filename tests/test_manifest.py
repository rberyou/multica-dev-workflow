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
    def test_manager_flow_requires_explicit_verify_after_apply(self):
        content = (ROOT / "skills/multica-workflow-manager/SKILL.md").read_text(
            encoding="utf-8"
        )
        apply_step = content.index("5. Run `apply`")
        verify_step = content.index("6. Run `verify`")
        self.assertLess(apply_step, verify_step)

    def test_generated_autopilot_contract_has_no_priority(self):
        contract = json.loads(
            (
                ROOT
                / "skills/multica-workflow-observer/references/control-plane-contract.json"
            ).read_text(encoding="utf-8")
        )
        for desired in contract["autopilots"].values():
            self.assertNotIn("priority", desired)

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

    def test_release_workflow_uses_protected_environment_request(self):
        workflow = (ROOT / ".github/workflows/release.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("push:\n    tags:", workflow)
        self.assertIn("ref: ${{ github.sha }}", workflow)
        self.assertIn("environment: workflow-release", workflow)
        self.assertIn("python scripts/release.py verify-request", workflow)
        self.assertIn("python scripts/release.py verify-publish-gate", workflow)
        self.assertIn("python scripts/release.py publish", workflow)
        self.assertIn("python scripts/release.py publish-release", workflow)
        self.assertIn(
            "actions/create-github-app-token@bcd2ba49218906704ab6c1aa796996da409d3eb1",
            workflow,
        )
        self.assertNotIn('git config user.name "github-actions[bot]"', workflow)
        self.assertNotIn("contents: write", workflow)
        self.assertGreaterEqual(workflow.count("persist-credentials: false"), 2)
        self.assertIn("GH_TOKEN: ${{ github.token }}", workflow)
        self.assertIn("recover_existing_tag", workflow)
        self.assertIn(
            'python scripts/release.py verify-assets --tag "${{ needs.validate-request.outputs.tag }}"',
            workflow,
        )
        self.assertIn("steps.publisher.outputs.token", workflow)
        self.assertIn("multica-workflow-console-${{ needs.validate-request.outputs.tag }}.zip", workflow)
        self.assertIn("multica-workflow-secure-runtime-win-x64-${{ needs.validate-request.outputs.tag }}.zip", workflow)
        self.assertIn(
            "release-request-${{ steps.request.outputs.release_request_digest }}",
            workflow,
        )

        control = json.loads((ROOT / "docs/release-control.json").read_text(encoding="utf-8"))
        self.assertEqual(control["required_visibility"], "public")

    def test_operations_disable_automatic_maintenance_expansion(self):
        manifest = json.loads((ROOT / "workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(
            manifest["operations"]["maintenance_intake_mode"], "human_gated"
        )
        self.assertIs(manifest["operations"]["automatic_expansion"], False)

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
