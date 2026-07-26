from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from package_skills import build_archive, package_hash  # noqa: E402


class PackageSkillsTests(unittest.TestCase):
    def test_every_manifest_skill_packages_deterministically(self):
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
                    if skill.name == "multica-workflow-incidents":
                        self.assertIn("scripts/incidents.py", names)
                        self.assertIn("references/incident-contract.md", names)

    def test_package_omits_runtime_cache_files(self):
        with tempfile.TemporaryDirectory() as temp:
            skill = Path(temp) / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: test\nmetadata:\n  version: 1.0.0\n---\n", encoding="utf-8"
            )
            cache = skill / "__pycache__"
            cache.mkdir()
            (cache / "secret.pyc").write_bytes(b"cache")
            output = Path(temp) / "skill.zip"
            build_archive(skill, output)
            with zipfile.ZipFile(output) as archive:
                self.assertNotIn("__pycache__/secret.pyc", archive.namelist())

    def test_package_omits_git_ignored_files(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            subprocess.run(
                ["git", "init"], cwd=root, check=True, capture_output=True
            )
            (root / ".gitignore").write_text("*.local\n", encoding="utf-8")
            skill = root / "skill"
            skill.mkdir()
            (skill / "SKILL.md").write_text(
                "---\nname: test\nmetadata:\n  version: 1.0.0\n---\n",
                encoding="utf-8",
            )
            (skill / "credentials.local").write_text(
                "should-not-ship", encoding="utf-8"
            )
            output = root / "skill.zip"
            build_archive(skill, output)
            with zipfile.ZipFile(output) as archive:
                self.assertNotIn("credentials.local", archive.namelist())


if __name__ == "__main__":
    unittest.main()
