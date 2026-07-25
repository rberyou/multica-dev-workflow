from pathlib import Path
import json
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from package_skills import build_archive, package_hash  # noqa: E402
from generate_audit_contract import build_contract  # noqa: E402


class PackageSkillsTests(unittest.TestCase):
    def test_observer_control_plane_contract_matches_repository(self):
        path = ROOT / "skills/multica-workflow-observer/references/control-plane-contract.json"
        self.assertEqual(
            json.loads(path.read_text(encoding="utf-8")),
            build_contract(ROOT),
        )

    def test_archive_is_deterministic_and_contains_hash(self):
        manifest = json.loads((ROOT / "workflow.json").read_text(encoding="utf-8"))
        for skill in sorted(ROOT / item["path"] for item in manifest["skills"]):
            with self.subTest(skill=skill.name), tempfile.TemporaryDirectory() as temp:
                first = Path(temp) / "first.zip"
                second = Path(temp) / "second.zip"
                expected = package_hash(skill)
                self.assertEqual(build_archive(skill, first), expected)
                self.assertEqual(build_archive(skill, second), expected)
                self.assertEqual(first.read_bytes(), second.read_bytes())
                with zipfile.ZipFile(first) as archive:
                    names = sorted(archive.namelist())
                    self.assertIn("SKILL.md", names)
                    content = archive.read("SKILL.md").decode("utf-8")
                    self.assertIn(f"package_hash: {expected}", content)
                    if skill.name == "multica-workflow-observer":
                        self.assertIn("scripts/observer.py", names)


if __name__ == "__main__":
    unittest.main()
