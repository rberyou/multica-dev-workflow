import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


from scripts import generate_secure_runtime_manifest


def run_git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), *args],
        check=True,
        capture_output=True,
    )
    if args == ("init",):
        subprocess.run(
            ["git", "-C", str(root), "config", "core.autocrlf", "false"],
            check=True,
            capture_output=True,
        )


def git_output(root: Path, *args: str, input_bytes: bytes | None = None) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_bytes,
        check=True,
        capture_output=True,
    ).stdout


class SecureRuntimeManifestTests(unittest.TestCase):
    def test_cache_build_temp_and_ignored_files_do_not_change_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "source"
            root.mkdir()
            run_git(root, "init")
            run_git(root, "config", "user.email", "manifest-test@example.invalid")
            run_git(root, "config", "user.name", "Manifest Test")
            (root / ".gitignore").write_text(
                "\n".join(
                    [
                        "skills/demo/references/ignored.md",
                        "",
                    ]
                ),
                encoding="utf-8",
            )
            skill = root / "skills/demo"
            (skill / "scripts").mkdir(parents=True)
            (skill / "references").mkdir()
            (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            (skill / "scripts/tool.py").write_text("print('demo')\n", encoding="utf-8")
            (skill / "references/guide.md").write_text("Guide\n", encoding="utf-8")
            tracked_target = skill / "target/config.json"
            tracked_target.parent.mkdir()
            tracked_target.write_text("{}\n", encoding="utf-8")
            tracked_bin = skill / "bin/helper.py"
            tracked_bin.parent.mkdir()
            tracked_bin.write_text("print('helper')\n", encoding="utf-8")
            policy = root / "secure-runtime/policy"
            policy.mkdir(parents=True)
            (policy / "requirements.template.toml").write_text(
                "[permissions]\nnetwork = false\n", encoding="utf-8"
            )
            ignored_tracked = skill / "references/ignored.md"
            ignored_tracked.write_text("ignored\n", encoding="utf-8")
            run_git(root, "add", ".gitignore", "skills", "secure-runtime")
            run_git(root, "add", "--force", str(ignored_tracked.relative_to(root)))
            run_git(root, "commit", "-m", "test fixture")
            host_excludes = parent / "host-excludes"
            host_excludes.write_text("*.md\n", encoding="utf-8")
            run_git(root, "config", "core.excludesFile", str(host_excludes))
            (root / ".git/info/exclude").write_text("*.yaml\n", encoding="utf-8")
            ignored_manifest = generate_secure_runtime_manifest.build_manifest(root)
            self.assertNotIn(
                "skills/demo/references/ignored.md", ignored_manifest["files"]
            )
            (root / ".gitignore").write_text("", encoding="utf-8")
            included_manifest = generate_secure_runtime_manifest.build_manifest(root)
            self.assertIn(
                "skills/demo/references/ignored.md", included_manifest["files"]
            )
            (root / ".gitignore").write_text(
                "skills/demo/references/ignored.md\n", encoding="utf-8"
            )

            checkout = parent / "clean-checkout"
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.autocrlf=false",
                    "clone",
                    "--quiet",
                    "--no-hardlinks",
                    str(root),
                    str(checkout),
                ],
                check=True,
                capture_output=True,
            )
            run_git(checkout, "config", "core.autocrlf", "false")
            clean = generate_secure_runtime_manifest.render_manifest(checkout)

            cache = skill / "scripts/__pycache__"
            cache.mkdir()
            (cache / "tool.cpython-313.pyc").write_bytes(b"pyc")
            (skill / "scripts/tool.pyc").write_bytes(b"pyc")
            (skill / "scripts/tool.pyo").write_bytes(b"pyo")
            build = skill / "build"
            build.mkdir()
            (build / "output.json").write_text("{}\n", encoding="utf-8")
            cache_data = skill / "cache/data.json"
            cache_data.parent.mkdir()
            cache_data.write_text("{}\n", encoding="utf-8")
            (skill / "scratch.tmp").write_text("temporary\n", encoding="utf-8")
            (skill / "notes.local").write_text("ignored\n", encoding="utf-8")

            dirty = generate_secure_runtime_manifest.render_manifest(root)

            self.assertEqual(clean, dirty)
            self.assertNotIn(b"\r\n", dirty)
            manifest_text = dirty.decode("utf-8")
            manifest = json.loads(manifest_text)
            self.assertIn("skills/demo/SKILL.md", manifest["files"])
            self.assertIn("skills/demo/target/config.json", manifest["files"])
            self.assertIn("skills/demo/bin/helper.py", manifest["files"])
            self.assertNotIn("__pycache__", manifest_text)
            self.assertNotIn(".pyc", manifest_text)
            self.assertNotIn(".pyo", manifest_text)
            self.assertNotIn("ignored.md", manifest_text)
            self.assertNotIn("notes.local", manifest_text)
            self.assertNotIn("build/output.json", manifest_text)
            self.assertNotIn("cache/data.json", manifest_text)
            self.assertNotIn("scratch.tmp", manifest_text)

    def test_nonignored_untracked_publishable_source_is_included(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_git(root, "init")
            host_excludes = root / "host-excludes"
            host_excludes.write_text("*.md\n", encoding="utf-8")
            run_git(root, "config", "core.excludesFile", str(host_excludes))
            (root / ".git/info/exclude").write_text("*.md\n", encoding="utf-8")
            skill = root / "skills/demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            policy = root / "secure-runtime/policy"
            policy.mkdir(parents=True)
            (policy / "requirements.template.toml").write_text(
                "[permissions]\nnetwork = false\n", encoding="utf-8"
            )
            run_git(root, "add", "skills", "secure-runtime")
            source = skill / "references/new.md"
            source.parent.mkdir()
            source.write_text("New reference\n", encoding="utf-8")

            manifest = generate_secure_runtime_manifest.build_manifest(root)

            self.assertIn("skills/demo/references/new.md", manifest["files"])

    def test_non_regular_git_modes_are_rejected_before_suffix_filtering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_git(root, "init")
            run_git(root, "config", "user.email", "manifest-test@example.invalid")
            run_git(root, "config", "user.name", "Manifest Test")
            skill = root / "skills/demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            policy = root / "secure-runtime/policy"
            policy.mkdir(parents=True)
            (policy / "requirements.template.toml").write_text(
                "[permissions]\nnetwork = false\n", encoding="utf-8"
            )
            run_git(root, "add", "skills", "secure-runtime")
            run_git(root, "commit", "-m", "test fixture")
            object_id = git_output(
                root, "hash-object", "-w", "--stdin", input_bytes=b"outside.md"
            ).decode("ascii").strip()
            run_git(
                root,
                "update-index",
                "--add",
                "--cacheinfo",
                f"120000,{object_id},skills/demo/link",
            )
            (skill / "link").write_text("outside.md\n", encoding="utf-8")

            with self.assertRaisesRegex(
                generate_secure_runtime_manifest.ManifestError,
                "unsupported Git mode 120000",
            ):
                generate_secure_runtime_manifest.build_manifest(root)
            run_git(root, "update-index", "--force-remove", "skills/demo/link")

            commit_id = git_output(root, "rev-parse", "HEAD").decode("ascii").strip()
            run_git(
                root,
                "update-index",
                "--add",
                "--cacheinfo",
                f"160000,{commit_id},skills/demo/submodule",
            )
            (skill / "submodule").mkdir()
            with self.assertRaisesRegex(
                generate_secure_runtime_manifest.ManifestError,
                "unsupported Git mode 160000",
            ):
                generate_secure_runtime_manifest.build_manifest(root)

    def test_unstaged_delete_and_rename_follow_worktree(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run_git(root, "init")
            run_git(root, "config", "user.email", "manifest-test@example.invalid")
            run_git(root, "config", "user.name", "Manifest Test")
            skill = root / "skills/demo"
            references = skill / "references"
            references.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            deleted = references / "deleted.md"
            deleted.write_text("delete me\n", encoding="utf-8")
            old_name = references / "old-name.md"
            old_name.write_text("rename me\n", encoding="utf-8")
            policy = root / "secure-runtime/policy"
            policy.mkdir(parents=True)
            (policy / "requirements.template.toml").write_text(
                "[permissions]\nnetwork = false\n", encoding="utf-8"
            )
            run_git(root, "add", "skills", "secure-runtime")
            run_git(root, "commit", "-m", "test fixture")

            deleted.unlink()
            new_name = references / "new-name.md"
            old_name.rename(new_name)

            manifest = generate_secure_runtime_manifest.build_manifest(root)

            self.assertNotIn("skills/demo/references/deleted.md", manifest["files"])
            self.assertNotIn("skills/demo/references/old-name.md", manifest["files"])
            self.assertIn("skills/demo/references/new-name.md", manifest["files"])

    def test_git_filter_normalization_fails_closed_then_matches_clean_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "source"
            root.mkdir()
            run_git(root, "init")
            run_git(root, "config", "user.email", "manifest-test@example.invalid")
            run_git(root, "config", "user.name", "Manifest Test")
            (root / ".gitattributes").write_text("*.md text eol=lf\n", encoding="utf-8")
            skill = root / "skills/demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_bytes(b"# Demo\n")
            policy = root / "secure-runtime/policy"
            policy.mkdir(parents=True)
            (policy / "requirements.template.toml").write_bytes(
                b"[permissions]\nnetwork = false\n"
            )
            run_git(root, "add", ".gitattributes", "skills", "secure-runtime")
            run_git(root, "commit", "-m", "test fixture")

            source = skill / "references/new.md"
            source.parent.mkdir()
            source.write_bytes(b"New reference\r\n")
            with self.assertRaisesRegex(
                generate_secure_runtime_manifest.ManifestError,
                "Git filters would change source bytes",
            ):
                generate_secure_runtime_manifest.build_manifest(root)

            source.write_bytes(b"New reference\n")
            before_stage = generate_secure_runtime_manifest.render_manifest(root)
            run_git(root, "add", str(source.relative_to(root)))
            run_git(root, "commit", "-m", "add reference")
            checkout = parent / "clean-checkout"
            subprocess.run(
                [
                    "git",
                    "-c",
                    "core.autocrlf=false",
                    "clone",
                    "--quiet",
                    "--no-hardlinks",
                    str(root),
                    str(checkout),
                ],
                check=True,
                capture_output=True,
            )

            self.assertEqual(
                before_stage,
                generate_secure_runtime_manifest.render_manifest(checkout),
            )

    def test_filesystem_link_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            parent = Path(temporary)
            root = parent / "source"
            root.mkdir()
            run_git(root, "init")
            skill = root / "skills/demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# Demo\n", encoding="utf-8")
            policy = root / "secure-runtime/policy"
            policy.mkdir(parents=True)
            (policy / "requirements.template.toml").write_text(
                "[permissions]\nnetwork = false\n", encoding="utf-8"
            )
            run_git(root, "add", "skills", "secure-runtime")
            if os.name == "nt":
                outside = parent / "outside"
                outside.mkdir()
                (outside / "linked.md").write_text("outside\n", encoding="utf-8")
                link = skill / "linked-directory"
                result = subprocess.run(
                    ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
                    capture_output=True,
                )
                if result.returncode != 0:
                    self.skipTest(
                        result.stderr.decode("utf-8", "replace").strip()
                        or "directory junctions are unavailable"
                    )
                try:
                    with self.assertRaisesRegex(
                        generate_secure_runtime_manifest.ManifestError,
                        "linked source path is not publishable",
                    ):
                        generate_secure_runtime_manifest.resolve_source(
                            root, "skills/demo/linked-directory/linked.md", None
                        )
                finally:
                    os.rmdir(link)
            else:
                outside = parent / "outside.md"
                outside.write_text("outside\n", encoding="utf-8")
                link = skill / "linked.md"
                os.symlink(outside, link)
                with self.assertRaisesRegex(
                    generate_secure_runtime_manifest.ManifestError,
                    "linked source path is not publishable",
                ):
                    generate_secure_runtime_manifest.build_manifest(root)


if __name__ == "__main__":
    unittest.main()
