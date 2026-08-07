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
        skill_root = ROOT / "skills/multica-requirement-intake"
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        submission = (skill_root / "references/submission-procedure.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("multica-workflow-incidents", skill)
        self.assertIn("bind-workflow-issue", submission)
        self.assertIn("Browser Read-Only Fallback", submission)
        self.assertNotIn("Create Through the Browser", skill + submission)
        portable = (skill_root / "references/portable-setup.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("用户在当前请求中明确指定的 profile", portable)
        self.assertIn("用户在当前请求中明确指定的 workspace", portable)
        self.assertIn("daemon-managed Multica 任务", portable)
        self.assertIn("不得回退到用户全局 CLI 配置", portable)

    def test_active_skills_use_proportionate_progressive_disclosure(self):
        intake_root = ROOT / "skills/multica-requirement-intake"
        intake = (intake_root / "SKILL.md").read_text(encoding="utf-8")
        for reference in [
            "requirement-template.md",
            "portable-setup.md",
            "submission-procedure.md",
            "follow-and-approvals.md",
            "operating-manual.md",
        ]:
            self.assertIn(f"(references/{reference})", intake)
        self.assertLess(len(intake.splitlines()), 100)

        incidents = (
            ROOT / "skills/multica-workflow-incidents/SKILL.md"
        ).read_text(encoding="utf-8")
        delivery = (
            ROOT / "skills/multica-delivery-policy/SKILL.md"
        ).read_text(encoding="utf-8")
        console = (ROOT / "skills/multica-workflow-console/SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("(references/incident-contract.md)", incidents)
        self.assertIn("(references/policy-contract.md)", delivery)
        self.assertIn("(references/evidence-contract.md)", delivery)
        self.assertIn("(references/final-approval-contract.md)", delivery)
        self.assertLess(len(incidents.splitlines()), 100)
        self.assertLess(len(delivery.splitlines()), 100)
        self.assertLess(len(console.splitlines()), 50)

    def test_delivery_policy_docs_match_protocol_contract(self):
        policy_root = ROOT / "skills/multica-delivery-policy"
        skill = (policy_root / "SKILL.md").read_text(encoding="utf-8")
        policy = (policy_root / "references/policy-contract.md").read_text(
            encoding="utf-8"
        )
        evidence = (policy_root / "references/evidence-contract.md").read_text(
            encoding="utf-8"
        )
        final = (
            policy_root / "references/final-approval-contract.md"
        ).read_text(encoding="utf-8")
        instructions = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (ROOT / "instructions").rglob("*.md")
        )
        for mode in ["branch_only", "lightweight", "isolated"]:
            self.assertIn(mode, skill + policy + instructions)
        self.assertIn("multica.delivery.json", policy)
        self.assertIn("policy_digest", policy + evidence + instructions)
        self.assertIn("approved_requirement_head_sha", evidence + instructions)
        self.assertIn("APPROVE REQUIREMENT vN", evidence + instructions)
        self.assertIn("不得让人工手工猜测或输入 SHA", instructions)
        self.assertIn("final_approval_gate_state", final + instructions)
        self.assertIn("top-level Requirement", final + evidence)
        self.assertIn("trigger_outcomes", final + instructions)
        self.assertIn("queued", final + instructions)
        self.assertIn("coalesced", final + instructions)
        self.assertIn("deferred", final + instructions)
        self.assertIn("local_only", final)
        self.assertIn("direct_push", final)
        self.assertIn("requirement_pr", final)
        self.assertIn("only managed Agent that writes", final)
        self.assertIn("非权威建议", instructions)

    def test_requirement_intake_targets_only_open_root_final_gate(self):
        skill_root = ROOT / "skills/multica-requirement-intake"
        skill = (skill_root / "SKILL.md").read_text(encoding="utf-8")
        follow = (skill_root / "references/follow-and-approvals.md").read_text(
            encoding="utf-8"
        )
        manual = (skill_root / "references/operating-manual.md").read_text(
            encoding="utf-8"
        )
        content = skill + follow + manual
        self.assertIn("top-level Requirement", content)
        self.assertIn("final_approval_gate_state=open", content)
        self.assertIn("Implementation", content)
        self.assertIn("must not create approval metadata", skill)
        self.assertIn("评论在 Implementation 等子 Issue 上必须拒绝", manual)
        self.assertIn("批准本身不是终点", manual)

    def test_ci_and_agent_guide_run_documentation_tests(self):
        self.assertIn(
            "tests.test_docs",
            (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8"),
        )
        self.assertIn(
            "tests.test_docs", (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        )
        self.assertIn(
            "tests.test_delivery_policy",
            (ROOT / "AGENTS.md").read_text(encoding="utf-8"),
        )

    def test_runtime_map_documentation_matches_local_state_boundaries(self):
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
        self.assertIn(
            "~/.multica/workflows/<workflow-id>/runtime-maps/<workspace-id>.json",
            runtime_maps,
        )
        self.assertIn("<source>/.multica/plans/", commands)
        self.assertIn("<source>/.multica/deployments/", commands)
        self.assertIn("release-manifest.json", commands)
        self.assertIn("without `.git`", commands)
        self.assertNotIn(".multica/runtime-maps/<workspace-id>.json", runtime_maps)
        self.assertIn("does not fall back to the old checkout path", runtime_maps)
        self.assertIn("saved deployment Plan", runtime_maps)
        self.assertIn("--runtime-map <path>", runtime_maps)
        self.assertIn("multica --profile", runtime_maps)
        documented = "\n".join(
            path.read_text(encoding="utf-8") for path in documentation_files()
        )
        self.assertNotIn("runtime-map.local.json", documented)


if __name__ == "__main__":
    unittest.main()
