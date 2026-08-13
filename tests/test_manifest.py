import argparse
import json
from pathlib import Path
import sys
import unittest
from unittest import mock

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_lib import validate_repository  # noqa: E402
import workflow as workflow_cli  # noqa: E402


class ManifestTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.manifest = json.loads((ROOT / "workflow.json").read_text(encoding="utf-8"))
        cls.schema = json.loads((ROOT / "workflow.schema.json").read_text(encoding="utf-8"))

    def test_manifest_matches_schema_and_repository_contract(self):
        Draft202012Validator(self.schema).validate(self.manifest)
        checked, profile = validate_repository(ROOT, "quality")
        self.assertEqual(checked["workflow"]["protocol_revision"], "v4")
        self.assertEqual(profile["name"], "quality")

    def test_v4_manages_only_the_ordinary_development_team(self):
        self.assertEqual(
            {item["key"] for item in self.manifest["agents"]},
            {
                "leader",
                "planner",
                "plan-reviewer",
                "integrator",
                "developer-a",
                "developer-b",
                "code-reviewer",
            },
        )
        self.assertNotIn("autopilots", self.manifest)
        self.assertFalse((ROOT / "secure-runtime/WorkflowSecureRuntime.sln").exists())
        self.assertFalse(
            (ROOT / "secure-runtime/src/WorkflowSecureRuntime.Core/WorkflowSecureRuntime.Core.csproj").exists()
        )
        self.assertFalse((ROOT / "instructions/roles/workflow-observer.md").exists())
        self.assertFalse((ROOT / "instructions/roles/workflow-maintainer.md").exists())
        self.assertFalse((ROOT / "instructions/roles/workflow-maintenance-reviewer.md").exists())
        self.assertFalse((ROOT / "skills/multica-workflow-observer/SKILL.md").exists())
        self.assertFalse((ROOT / "skills/multica-workflow-maintainer/SKILL.md").exists())

    def test_incident_skill_is_attached_to_every_squad_agent(self):
        incidents = self.manifest["incidents"]
        reporters = set(incidents["reporter_agents"])
        squad_agents = {item["agent"] for item in self.manifest["squad"]["agent_members"]}
        skill = next(item for item in self.manifest["skills"] if item["key"] == incidents["skill"])
        project = next(item for item in self.manifest["projects"] if item["key"] == incidents["project"])
        self.assertEqual(reporters, squad_agents)
        self.assertEqual(set(skill["attach_to"]), reporters)
        self.assertIn("workspace", skill["targets"])
        self.assertEqual(project["lead"], "leader")
        self.assertEqual(incidents["fix_execution_mode"], "external")

    def test_delivery_policy_skill_is_attached_to_every_squad_agent(self):
        squad_agents = {item["agent"] for item in self.manifest["squad"]["agent_members"]}
        skill = next(
            item for item in self.manifest["skills"] if item["key"] == "delivery-policy"
        )
        self.assertEqual(set(skill["attach_to"]), squad_agents)
        self.assertIn("workspace", skill["targets"])

    def test_versions_match_every_active_skill(self):
        version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(self.manifest["workflow"]["version"], version)
        for skill in self.manifest["skills"]:
            content = (ROOT / skill["path"] / "SKILL.md").read_text(encoding="utf-8")
            self.assertIn(f"version: {version}", content)

    def test_validation_workflow_targets_v4_tools_and_tests(self):
        workflow = (ROOT / ".github/workflows/validate.yml").read_text(encoding="utf-8")
        self.assertIn("tests.test_docs", workflow)
        self.assertIn("tests.test_incidents", workflow)
        self.assertIn("multica-workflow-incidents/scripts/incidents.py", workflow)
        self.assertIn("tests.test_delivery_policy", workflow)
        self.assertIn("multica-delivery-policy/scripts/delivery_policy.py", workflow)
        self.assertIn("references/plan-policy.schema.json", workflow)
        self.assertIn("references/plan-policy.example.json", workflow)
        self.assertNotIn("generate_audit_contract.py", workflow)
        self.assertNotIn("release-control-evidence", workflow)

    def test_cli_has_only_current_operational_commands(self):
        subparsers = next(
            action
            for action in workflow_cli.parser()._actions
            if action.__class__.__name__ == "_SubParsersAction"
        )
        self.assertEqual(
            set(subparsers.choices),
            {
                "doctor",
                "export",
                "plan",
                "drift",
                "verify",
                "apply",
                "install-skills",
                "bind-workflow-issue",
                "report-incident",
                "create-incident-fix-requirement",
                "link-incident-fix",
                "close-incident",
            },
        )

    def test_incident_wrappers_forward_external_creation_and_close_references(self):
        root = ROOT
        context_value = (
            argparse.Namespace(binary="multica", profile=None),
            {"id": "workspace-test"},
            self.manifest,
            {},
        )
        with mock.patch.object(
            workflow_cli, "context", return_value=context_value
        ), mock.patch.object(workflow_cli, "run_process") as run_process:
            run_process.return_value = argparse.Namespace(
                returncode=0, stdout="", stderr=""
            )
            create = workflow_cli.parser().parse_args(
                [
                    "create-incident-fix-requirement",
                    "--incident",
                    "INC-1",
                    "--project",
                    "external-project",
                    "--assignee-id",
                    "external-owner",
                ]
            )
            workflow_cli.command_incidents(create, root)
            command = run_process.call_args.args[0]
            self.assertIn("create-fix-requirement", command)
            self.assertIn("external-project", command)
            self.assertIn("external-owner", command)

            close = workflow_cli.parser().parse_args(
                [
                    "close-incident",
                    "--incident",
                    "INC-1",
                    "--result",
                    "passed",
                    "--evidence",
                    "verified",
                    "--fix-reference-type",
                    "artifact_version",
                    "--fix-reference",
                    "runtime-2",
                    "--deployment-verification-reference-type",
                    "deployment_record",
                    "--deployment-verification-reference",
                    "deploy-17",
                ]
            )
            workflow_cli.command_incidents(close, root)
            command = run_process.call_args.args[0]
            for expected in [
                "--fix-reference-type",
                "artifact_version",
                "--fix-reference",
                "runtime-2",
                "--deployment-verification-reference-type",
                "deployment_record",
                "--deployment-verification-reference",
                "deploy-17",
            ]:
                self.assertIn(expected, command)

    def test_default_runtime_map_path_is_scoped_by_workflow_and_workspace(self):
        args = argparse.Namespace(runtime_map=None)
        home = ROOT / "test-home"
        first = workflow_cli.runtime_map_path(
            args, {"id": "workspace-a"}, "development-delivery", home
        )
        second = workflow_cli.runtime_map_path(
            args, {"id": "workspace-b"}, "development-delivery", home
        )
        self.assertEqual(
            first,
            (
                home
                / ".multica/workflows/development-delivery/runtime-maps/workspace-a.json"
            ).resolve(),
        )
        self.assertEqual(
            second,
            (
                home
                / ".multica/workflows/development-delivery/runtime-maps/workspace-b.json"
            ).resolve(),
        )
        self.assertNotEqual(first, second)

        other_workflow = workflow_cli.runtime_map_path(
            args, {"id": "workspace-a"}, "another-workflow", home
        )
        self.assertNotEqual(first, other_workflow)

    def test_explicit_runtime_map_overrides_workspace_default(self):
        override = (ROOT / "custom-runtime-map.json").resolve()
        args = argparse.Namespace(runtime_map=str(override))
        self.assertEqual(
            workflow_cli.runtime_map_path(
                args, {"id": "workspace-a"}, "development-delivery"
            ),
            override,
        )

    def test_runtime_map_path_requires_a_resolved_workspace_id(self):
        args = argparse.Namespace(runtime_map=None)
        with self.assertRaisesRegex(
            workflow_cli.WorkflowError, "resolved workspace has no id"
        ):
            workflow_cli.runtime_map_path(args, {}, "development-delivery")

    def test_runtime_map_path_rejects_unsafe_workspace_id(self):
        args = argparse.Namespace(runtime_map=None)
        with self.assertRaisesRegex(
            workflow_cli.WorkflowError, "workspace id is not safe"
        ):
            workflow_cli.runtime_map_path(
                args, {"id": "../workspace-a"}, "development-delivery"
            )

    def test_runtime_map_path_rejects_unsafe_workflow_id(self):
        args = argparse.Namespace(runtime_map=None)
        with self.assertRaisesRegex(
            workflow_cli.WorkflowError, "workflow id is not safe"
        ):
            workflow_cli.runtime_map_path(
                args, {"id": "workspace-a"}, "../development-delivery"
            )


if __name__ == "__main__":
    unittest.main()
