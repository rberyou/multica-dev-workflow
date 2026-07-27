from pathlib import Path
import re
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import release as release_cli  # noqa: E402
import workflow as workflow_cli  # noqa: E402


def subcommands(parser) -> set[str]:
    action = next(
        item
        for item in parser._actions
        if item.__class__.__name__ == "_SubParsersAction"
    )
    return set(action.choices)


def documentation_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "AGENTS.md"]
    files.extend((ROOT / "docs").rglob("*.md"))
    files.extend((ROOT / "instructions").rglob("*.md"))
    files.extend((ROOT / "skills").rglob("*.md"))
    return sorted(set(files))


class DocumentationTests(unittest.TestCase):
    def test_relative_markdown_links_resolve(self):
        pattern = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
        for source in documentation_files():
            for target in pattern.findall(source.read_text(encoding="utf-8")):
                if target.startswith(("http://", "https://", "#")):
                    continue
                relative = target.split("#", 1)[0]
                destination = (source.parent / relative).resolve()
                with self.subTest(source=source.relative_to(ROOT), target=target):
                    self.assertTrue(destination.exists())

    def test_documented_repository_commands_exist(self):
        workflow_commands = subcommands(workflow_cli.parser())
        release_commands = subcommands(release_cli.parser())
        workflow_pattern = re.compile(r"python\s+scripts/workflow\.py\s+([a-z][a-z-]+)")
        release_pattern = re.compile(r"python\s+scripts/release\.py\s+([a-z][a-z-]+)")
        for source in documentation_files():
            content = source.read_text(encoding="utf-8")
            for command in workflow_pattern.findall(content):
                with self.subTest(source=source.relative_to(ROOT), command=command):
                    self.assertIn(command, workflow_commands)
            for command in release_pattern.findall(content):
                with self.subTest(source=source.relative_to(ROOT), command=command):
                    self.assertIn(command, release_commands)

    def test_skills_have_consistent_interface_metadata(self):
        for skill_dir in sorted(path.parent for path in (ROOT / "skills").glob("*/SKILL.md")):
            skill = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
            name = re.search(r"(?m)^name:\s*(\S+)\s*$", skill)
            description = re.search(r"(?m)^description:\s*(.+)$", skill)
            interface = (skill_dir / "agents/openai.yaml").read_text(encoding="utf-8")
            with self.subTest(skill=skill_dir.name):
                self.assertIsNotNone(name)
                self.assertIsNotNone(description)
                self.assertIn("Use ", description.group(1))
                self.assertIn(f"${name.group(1)}", interface)

    def test_only_current_design_document_remains(self):
        self.assertEqual(
            {path.name for path in (ROOT / "docs").glob("*design*.md")},
            {"workflow-design.md"},
        )

    def test_active_docs_do_not_reference_retired_interfaces(self):
        content = "\n".join(
            path.read_text(encoding="utf-8") for path in documentation_files()
        )
        for obsolete in [
            "docs/design-plan-v",
            "docs/secure-runtime.md",
            "generate_audit_contract.py",
            "maintenance_loop.py",
            "observer.py",
            "workflow.py audit",
            "workflow.py health",
            "--disable-operations",
            "release.py approval-block",
            "release.py apply",
        ]:
            with self.subTest(obsolete=obsolete):
                self.assertNotIn(obsolete, content)
        self.assertNotIn("APPROVE WORKFLOW RELEASE", content)
        self.assertNotRegex(
            content,
            r"release\.py\s+(?:package|publish)[^\n]*--approve",
        )

    def test_agent_instructions_use_attached_incident_skill_commands(self):
        content = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "instructions").rglob("*.md")
        )
        self.assertNotIn("python scripts/workflow.py bind-workflow-issue", content)
        self.assertNotIn("`report-incident`", content)
        self.assertNotIn("`link-incident-fix`", content)
        self.assertNotIn("`close-incident`", content)
        self.assertNotIn("由 Audit", content)

    def test_requirement_intake_requires_protocol_binding_before_start(self):
        content = (
            ROOT / "skills/multica-requirement-intake/SKILL.md"
        ).read_text(encoding="utf-8")
        self.assertIn("multica-workflow-incidents", content)
        self.assertIn("bind-workflow-issue", content)
        self.assertIn("Browser Read-Only Fallback", content)
        self.assertNotIn("Create Through the Browser", content)

    def test_ci_and_agent_guide_run_documentation_tests(self):
        self.assertIn(
            "tests.test_docs",
            (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "tests.test_docs", (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        )

    def test_runtime_map_documentation_is_workspace_scoped(self):
        skill_root = ROOT / "skills/multica-workflow-manager"
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        commands = (skill_root / "references/commands.md").read_text(encoding="utf-8")
        runtime_maps = (skill_root / "references/runtime-maps.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("[commands.md](references/commands.md)", skill)
        self.assertIn("[runtime-maps.md](references/runtime-maps.md)", skill)
        self.assertIn("current Codex task", commands)
        self.assertIn("not a Multica Issue", commands)
        self.assertIn(".multica/runtime-maps/<workspace-id>.json", runtime_maps)
        self.assertIn("--runtime-map <path>", runtime_maps)
        self.assertIn("multica --profile", runtime_maps)
        documented = "\n".join(
            path.read_text(encoding="utf-8") for path in documentation_files()
        )
        self.assertNotIn("runtime-map.local.json", documented)


if __name__ == "__main__":
    unittest.main()
