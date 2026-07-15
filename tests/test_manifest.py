from pathlib import Path
import json
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_lib import validate_repository  # noqa: E402


class ManifestTests(unittest.TestCase):
    def test_all_deployment_profiles_validate(self):
        for profile in ["quality", "codex-only", "opencode-only"]:
            manifest, deployment = validate_repository(ROOT, profile)
            self.assertEqual(manifest["schema_version"], 1)
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
        ]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            self.assertIsNone(uuid.search(text), path)
            self.assertIsNone(user_path.search(text), path)
            self.assertIsNone(token.search(text), path)

    def test_skills_have_management_metadata(self):
        for skill in ["multica-requirement-intake", "multica-workflow-manager"]:
            content = (ROOT / f"skills/{skill}/SKILL.md").read_text(encoding="utf-8")
            self.assertIn("managed_by: multica-dev-workflow", content)
            self.assertIn("workflow_id: development-delivery", content)


if __name__ == "__main__":
    unittest.main()
