from pathlib import Path
import sys
import tempfile
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from package_skills import build_archive, package_hash  # noqa: E402


class PackageSkillsTests(unittest.TestCase):
    def test_archive_is_deterministic_and_contains_hash(self):
        skill = ROOT / "skills/multica-requirement-intake"
        with tempfile.TemporaryDirectory() as temp:
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


if __name__ == "__main__":
    unittest.main()
