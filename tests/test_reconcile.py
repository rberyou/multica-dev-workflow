from contextlib import contextmanager
from pathlib import Path
import importlib.util
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_lib import (  # noqa: E402
    WorkflowError,
    _runtime_choice,
    apply_plan,
    build_plan,
    install_skills,
    load_deployment_record,
    mutation_actions,
    parse_marker,
    plan_has_blockers,
    redact,
    render_marker,
    save_plan,
    strip_marker,
    validate_repository,
)


CONSOLE_PATH = ROOT / "skills/multica-workflow-console/scripts/workflow_console.py"
CONSOLE_SPEC = importlib.util.spec_from_file_location("workflow_console", CONSOLE_PATH)
workflow_console = importlib.util.module_from_spec(CONSOLE_SPEC)
CONSOLE_SPEC.loader.exec_module(workflow_console)


def flag(args, name, default=None):
    try:
        return args[args.index(name) + 1]
    except ValueError:
        return default


@contextmanager
def committed_temp_repo():
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "repo"
        shutil.copytree(
            ROOT,
            root,
            ignore=shutil.ignore_patterns(
                ".git", ".multica", "exports", "build", "__pycache__", "*.pyc"
            ),
        )
        subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=root,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "Test"], cwd=root, check=True
        )
        subprocess.run(["git", "add", "."], cwd=root, check=True)
        subprocess.run(
            ["git", "commit", "-m", "test"],
            cwd=root,
            check=True,
            capture_output=True,
        )
        yield root


class FakeCLI:
    def __init__(self):
        self.profile = "test"
        self.workspace_id = "workspace-test"
        self.calls = []
        self.agents = []
        self.squads = []
        self.skills = []
        self.projects = []
        self.autopilots = []
        self.archived_agents = []
        self.fail_archive_once = False
        self.skill_details = {}
        self.agent_skills = {}
        self.members = {}
        self.runtimes = [
            {"id": "runtime-codex", "provider": "codex", "status": "online"},
            {
                "id": "runtime-opencode",
                "provider": "opencode",
                "status": "online",
            },
        ]
        self.user = {"id": "user-test", "name": "Tester"}

    def json(self, args, include_workspace=True):
        args = list(args)
        command = tuple(args)
        self.calls.append(command)
        if command[:2] == ("agent", "list"):
            return self.agents
        if command[:2] == ("agent", "get"):
            return next(item for item in self.agents if item["id"] == args[2])
        if command[:2] == ("squad", "list"):
            return self.squads
        if command[:2] == ("skill", "list"):
            return self.skills
        if command[:2] == ("project", "list"):
            return self.projects
        if command[:2] == ("autopilot", "list"):
            return self.autopilots
        if command[:2] == ("autopilot", "get"):
            return next(item for item in self.autopilots if item["id"] == args[2])
        if command[:2] == ("runtime", "list"):
            return self.runtimes
        if command[:3] == ("user", "profile", "get"):
            return self.user
        if command[:2] == ("skill", "get"):
            return self.skill_details[args[2]]
        if command[:3] == ("agent", "skills", "list"):
            return self.agent_skills.get(args[3], [])
        if command[:3] == ("squad", "member", "list"):
            return self.members.get(args[3], [])
        if command[:2] == ("skill", "import"):
            with zipfile.ZipFile(Path(flag(args, "--file"))) as archive:
                content = archive.read("SKILL.md").decode("utf-8")
            name = re.search(r"(?m)^name:\s*(.+)$", content).group(1).strip()
            package_hash = re.search(
                r"(?m)^\s*package_hash:\s*(\S+)\s*$", content
            ).group(1)
            current = next((item for item in self.skills if item["name"] == name), None)
            if current is None:
                current = {"id": f"skill-{len(self.skills) + 1}", "name": name}
                self.skills.append(current)
            self.skill_details[current["id"]] = {
                **current,
                "metadata": {
                    "managed_by": "multica-dev-workflow",
                    "workflow_id": "development-delivery",
                    "package_hash": package_hash,
                },
            }
            return self.skill_details[current["id"]]
        if command[:2] == ("skill", "delete"):
            skill_id = args[2]
            self.skills = [item for item in self.skills if item["id"] != skill_id]
            self.skill_details.pop(skill_id, None)
            return {}
        if command[:2] == ("agent", "create"):
            agent_id = f"agent-{len(self.agents) + 1}"
            permission = flag(args, "--permission-mode", "private")
            agent = {
                "id": agent_id,
                "name": flag(args, "--name"),
                "description": flag(args, "--description", ""),
                "instructions": flag(args, "--instructions", ""),
                "runtime_id": flag(args, "--runtime-id"),
                "model": flag(args, "--model", ""),
                "thinking_level": flag(args, "--thinking-level", ""),
                "max_concurrent_tasks": int(
                    flag(args, "--max-concurrent-tasks", "1")
                ),
                "permission_mode": permission,
                "invocation_targets": (
                    [{"target_type": "workspace", "target_id": self.workspace_id}]
                    if "--public-to-workspace" in args
                    else []
                ),
            }
            self.agents.append(agent)
            self.agent_skills[agent_id] = []
            return agent
        if command[:2] == ("agent", "update"):
            agent = next(item for item in self.agents if item["id"] == args[2])
            for option, key in {
                "--name": "name",
                "--description": "description",
                "--instructions": "instructions",
                "--runtime-id": "runtime_id",
                "--model": "model",
                "--thinking-level": "thinking_level",
            }.items():
                if option in args:
                    agent[key] = flag(args, option, "")
            if "--max-concurrent-tasks" in args:
                agent["max_concurrent_tasks"] = int(
                    flag(args, "--max-concurrent-tasks")
                )
            if "--permission-mode" in args:
                agent["permission_mode"] = flag(args, "--permission-mode")
                agent["invocation_targets"] = (
                    [{"target_type": "workspace", "target_id": self.workspace_id}]
                    if "--public-to-workspace" in args
                    else []
                )
            return agent
        if command[:2] == ("agent", "archive"):
            agent = next(item for item in self.agents if item["id"] == args[2])
            if self.fail_archive_once:
                self.fail_archive_once = False
                raise RuntimeError("simulated archive failure")
            if any(item.get("agent_id") == agent["id"] for item in self.autopilots):
                raise AssertionError("cannot archive an Agent referenced by an Autopilot")
            if any(
                member.get("member_type") == "agent"
                and member.get("member_id") == agent["id"]
                for members in self.members.values()
                for member in members
            ):
                raise AssertionError("cannot archive an Agent still present in a Squad")
            if any(item.get("lead_id") == agent["id"] for item in self.projects):
                raise AssertionError("cannot archive an Agent that still leads a Project")
            self.agents.remove(agent)
            self.archived_agents.append(agent)
            return agent
        if command[:3] == ("agent", "skills", "set"):
            agent_id = args[3]
            skill_ids = [item for item in flag(args, "--skill-ids", "").split(",") if item]
            self.agent_skills[agent_id] = [
                self.skill_details[skill_id] for skill_id in skill_ids
            ]
            return self.agent_skills[agent_id]
        if command[:2] == ("project", "create"):
            lead = next(item for item in self.agents if item["name"] == flag(args, "--lead"))
            project = {
                "id": f"project-{len(self.projects) + 1}",
                "title": flag(args, "--title"),
                "description": flag(args, "--description", ""),
                "lead_id": lead["id"],
                "status": flag(args, "--status", "in_progress"),
                "icon": flag(args, "--icon", ""),
            }
            self.projects.append(project)
            return project
        if command[:2] == ("autopilot", "delete"):
            autopilot = next(item for item in self.autopilots if item["id"] == args[2])
            self.autopilots.remove(autopilot)
            return {}
        if command[:2] == ("project", "update"):
            project = next(item for item in self.projects if item["id"] == args[2])
            if "--title" in args:
                project["title"] = flag(args, "--title")
            if "--description" in args:
                project["description"] = flag(args, "--description", "")
            if "--lead" in args:
                lead = next(
                    item for item in self.agents if item["name"] == flag(args, "--lead")
                )
                project["lead_id"] = lead["id"]
            if "--status" in args:
                project["status"] = flag(args, "--status", "in_progress")
            if "--icon" in args:
                project["icon"] = flag(args, "--icon", "")
            return project
        if command[:2] == ("squad", "create"):
            squad = {
                "id": f"squad-{len(self.squads) + 1}",
                "name": flag(args, "--name"),
                "description": flag(args, "--description", ""),
                "instructions": "",
                "leader_id": flag(args, "--leader"),
            }
            self.squads.append(squad)
            self.members[squad["id"]] = []
            return squad
        if command[:2] == ("squad", "update"):
            squad = next(item for item in self.squads if item["id"] == args[2])
            squad.update(
                {
                    "name": flag(args, "--name"),
                    "description": flag(args, "--description", ""),
                    "instructions": flag(args, "--instructions", ""),
                    "leader_id": flag(args, "--leader"),
                }
            )
            return squad
        if command[:3] == ("squad", "member", "add"):
            member = {
                "member_id": flag(args, "--member-id"),
                "member_type": flag(args, "--type"),
                "role": flag(args, "--role"),
            }
            self.members[args[3]].append(member)
            return member
        if command[:3] == ("squad", "member", "set-role"):
            member = next(
                item
                for item in self.members[args[3]]
                if item["member_id"] == flag(args, "--member-id")
                and item["member_type"] == flag(args, "--member-type")
            )
            member["role"] = flag(args, "--role")
            return member
        if command[:3] == ("squad", "member", "remove"):
            squad_id = args[3]
            member_id = flag(args, "--member-id")
            member_type = flag(args, "--type", "agent")
            member = next(
                item
                for item in self.members[squad_id]
                if item["member_id"] == member_id
                and item["member_type"] == member_type
            )
            self.members[squad_id].remove(member)
            return member
        raise AssertionError(f"unexpected fake command: {command}")


class ReconcileTests(unittest.TestCase):
    def test_marker_and_redaction_helpers(self):
        rendered = render_marker("wf", "agent.leader", "abc", "body\n")
        self.assertEqual(parse_marker(rendered)["object_key"], "agent.leader")
        self.assertEqual(strip_marker(rendered), "body\n")
        self.assertEqual(redact({"token": "secret", "safe": "value"})["token"], "<redacted>")

    def test_runtime_choice_preserves_compatible_binding_and_blocks_ambiguity(self):
        binding = {"provider": "codex", "required_status": "online"}
        runtime = {"id": "old", "provider": "codex", "status": "online"}
        selected, error = _runtime_choice(
            "binding",
            binding,
            {"bindings": {}},
            [runtime],
            {"name": "Agent", "runtime_id": "old"},
            {"old": runtime},
            False,
        )
        self.assertEqual((selected, error), ("old", None))
        selected, error = _runtime_choice(
            "binding",
            binding,
            {"bindings": {}},
            [runtime, {**runtime, "id": "other"}],
            None,
            {"old": runtime, "other": {**runtime, "id": "other"}},
            False,
        )
        self.assertIsNone(selected)
        self.assertIn("multiple Runtimes", error)

    def test_repository_contract_has_no_scheduled_automation(self):
        manifest, _ = validate_repository(ROOT, "quality")
        self.assertNotIn("autopilots", manifest)
        self.assertEqual(manifest["workflow"]["protocol_revision"], "v4")

    def test_initial_plan_apply_and_second_plan_are_idempotent(self):
        with committed_temp_repo() as root:
            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = root / ".multica/runtime-map.local.json"
            plan = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False
            )
            types = [item["type"] for item in plan["actions"]]
            self.assertFalse(plan_has_blockers(plan))
            self.assertIn("CREATE_AGENT", types)
            self.assertIn("CREATE_PROJECT", types)
            self.assertIn("CREATE_SQUAD", types)
            self.assertNotIn("CREATE_AUTOPILOT", types)
            plan_path = save_plan(root, plan)
            journal = apply_plan(root, cli, plan_path, plan["plan_digest"][:12])
            self.assertTrue(journal.get("finished_at"))
            self.assertEqual(len(cli.agents), 7)
            self.assertEqual(len(cli.projects), 1)
            self.assertEqual(len(cli.squads), 1)
            self.assertEqual(len(cli.skills), 2)
            self.assertIsNotNone(load_deployment_record(root, cli.workspace_id))
            second = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False, False
            )
            self.assertFalse(plan_has_blockers(second))
            self.assertEqual(mutation_actions(second), [])

    def test_apply_rejects_changed_multica_state(self):
        with committed_temp_repo() as root:
            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = root / ".multica/runtime-map.local.json"
            plan = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False
            )
            path = save_plan(root, plan)
            cli.runtimes.append(
                {"id": "new-runtime", "provider": "other", "status": "online"}
            )
            with self.assertRaisesRegex(WorkflowError, "Multica state changed"):
                apply_plan(root, cli, path, plan["plan_digest"][:12])

    def test_unmarked_same_name_agent_requires_adoption(self):
        with committed_temp_repo() as root:
            cli = FakeCLI()
            cli.agents.append(
                {
                    "id": "foreign",
                    "name": "开发队长",
                    "description": "",
                    "instructions": "unmanaged",
                    "runtime_id": "runtime-codex",
                    "model": "gpt-5.5",
                    "thinking_level": "xhigh",
                    "max_concurrent_tasks": 1,
                    "permission_mode": "public_to",
                    "invocation_targets": [
                        {"target_type": "workspace", "target_id": cli.workspace_id}
                    ],
                }
            )
            cli.agent_skills["foreign"] = []
            plan = build_plan(
                root,
                cli,
                {"id": cli.workspace_id, "name": "Test", "slug": "test"},
                "quality",
                root / ".multica/runtime-map.local.json",
                False,
                False,
                False,
            )
            self.assertTrue(plan_has_blockers(plan))
            self.assertTrue(
                any("requires --adopt" in item.get("reason", "") for item in plan["actions"])
            )

    def test_apply_retires_managed_objects_removed_from_desired_state(self):
        with committed_temp_repo() as root:
            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = root / ".multica/runtime-map.local.json"
            initial = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False
            )
            apply_plan(root, cli, save_plan(root, initial), initial["plan_digest"][:12])

            retired_skill = {
                "id": "skill-retired",
                "name": "multica-workflow-observer",
            }
            cli.skills.append(retired_skill)
            cli.skill_details["skill-retired"] = {
                **retired_skill,
                "metadata": {
                    "managed_by": "multica-dev-workflow",
                    "workflow_id": "development-delivery",
                    "package_hash": "old",
                },
            }
            retired_agent = {
                "id": "agent-retired",
                "name": "工作流观察员",
                "description": "",
                "instructions": render_marker(
                    "development-delivery", "agent.workflow-observer", "old", "old\n"
                ),
                "runtime_id": "runtime-codex",
                "model": "gpt-5.5",
                "thinking_level": "",
                "max_concurrent_tasks": 1,
                "permission_mode": "private",
                "invocation_targets": [],
            }
            cli.agents.append(retired_agent)
            cli.agent_skills["agent-retired"] = [
                cli.skill_details["skill-retired"]
            ]
            squad_id = cli.squads[0]["id"]
            cli.members[squad_id].append(
                {
                    "member_id": "agent-retired",
                    "member_type": "agent",
                    "role": "工作流观察员",
                }
            )
            leader_id = next(item["id"] for item in cli.agents if item["name"] == "开发队长")
            cli.agent_skills[leader_id].append(cli.skill_details["skill-retired"])
            cli.autopilots.append(
                {
                    "id": "autopilot-retired",
                    "title": "旧巡检",
                    "description": render_marker(
                        "development-delivery",
                        "autopilot.workflow-health-audit",
                        "old",
                        "old\n",
                    ),
                    "agent_id": "agent-retired",
                    "project_id": "project-1",
                    "status": "active",
                }
            )
            retired_project = {
                "id": "project-retired",
                "title": "旧工作流运维",
                "description": render_marker(
                    "development-delivery",
                    "project.workflow-operations",
                    "old",
                    "old\n",
                ),
                "lead_id": "agent-retired",
                "status": "in_progress",
                "icon": "",
            }
            cli.projects.append(retired_project)

            plan = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False
            )
            action_types = {item["type"] for item in plan["actions"]}
            self.assertIn("DELETE_RETIRED_SKILL", action_types)
            self.assertIn("ARCHIVE_RETIRED_AGENT", action_types)
            self.assertIn("DELETE_RETIRED_AUTOPILOT", action_types)
            self.assertIn("DETACH_RETIRED_SKILLS", action_types)
            self.assertIn("REMOVE_RETIRED_MEMBER", action_types)
            self.assertIn("REASSIGN_RETIRED_PROJECT_LEAD", action_types)
            apply_plan(root, cli, save_plan(root, plan), plan["plan_digest"][:12])
            self.assertFalse(cli.autopilots)
            self.assertNotIn(retired_skill, cli.skills)
            self.assertIn(retired_agent, cli.archived_agents)
            self.assertIn(retired_project, cli.projects)
            self.assertEqual(retired_project["lead_id"], leader_id)
            self.assertFalse(
                any(
                    item["member_id"] == "agent-retired"
                    for item in cli.members[squad_id]
                )
            )
            remove_index = next(
                index
                for index, call in enumerate(cli.calls)
                if call[:3] == ("squad", "member", "remove")
                and flag(call, "--member-id") == "agent-retired"
            )
            archive_index = next(
                index
                for index, call in enumerate(cli.calls)
                if call[:2] == ("agent", "archive")
                and call[2] == "agent-retired"
            )
            self.assertLess(remove_index, archive_index)
            autopilot_index = next(
                index
                for index, call in enumerate(cli.calls)
                if call[:2] == ("autopilot", "delete")
                and call[2] == "autopilot-retired"
            )
            self.assertLess(autopilot_index, archive_index)
            project_index = next(
                index
                for index, call in enumerate(cli.calls)
                if call[:2] == ("project", "update")
                and call[2] == "project-retired"
            )
            self.assertLess(project_index, archive_index)
            self.assertNotIn(
                "skill-retired",
                {
                    str(item.get("id") or item.get("skill_id"))
                    for item in cli.agent_skills[leader_id]
                },
            )

    def test_install_skills_copies_only_local_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            results = install_skills(ROOT, Path(temp), True, False)
            self.assertEqual(
                {item["skill"] for item in results},
                {
                    "multica-requirement-intake",
                    "multica-workflow-manager",
                    "multica-workflow-incidents",
                    "multica-workflow-console",
                },
            )
            self.assertFalse(
                any(Path(temp).rglob("__pycache__"))
            )

    def test_install_skills_removes_only_owned_retired_skills(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp)
            retired = target / "multica-workflow-observer"
            retired.mkdir()
            (retired / "SKILL.md").write_text(
                "---\n"
                "name: multica-workflow-observer\n"
                "metadata:\n"
                "  managed_by: multica-dev-workflow\n"
                "  workflow_id: development-delivery\n"
                "---\n",
                encoding="utf-8",
            )
            results = install_skills(ROOT, target, True, False)
            self.assertFalse(retired.exists())
            self.assertIn(
                {
                    "skill": "multica-workflow-observer",
                    "mode": "retired-removed",
                    "path": str(retired),
                },
                results,
            )

            foreign = target / "multica-workflow-maintainer"
            foreign.mkdir()
            (foreign / "SKILL.md").write_text(
                "---\nname: multica-workflow-maintainer\n---\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "not owned"):
                install_skills(ROOT, target, True, True)
            self.assertTrue(foreign.exists())

    def test_retirement_recovers_with_a_fresh_plan_after_partial_failure(self):
        with committed_temp_repo() as root:
            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = root / ".multica/runtime-map.local.json"
            initial = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False
            )
            apply_plan(root, cli, save_plan(root, initial), initial["plan_digest"][:12])

            retired_agent = {
                "id": "agent-retired-retry",
                "name": "旧工作流观察员",
                "description": "",
                "instructions": render_marker(
                    "development-delivery",
                    "agent.workflow-observer-retry",
                    "old",
                    "old\n",
                ),
                "runtime_id": "runtime-codex",
                "model": "gpt-5.5",
                "thinking_level": "",
                "max_concurrent_tasks": 1,
                "permission_mode": "private",
                "invocation_targets": [],
            }
            cli.agents.append(retired_agent)
            cli.agent_skills[retired_agent["id"]] = []
            squad_id = cli.squads[0]["id"]
            cli.members[squad_id].append(
                {
                    "member_id": retired_agent["id"],
                    "member_type": "agent",
                    "role": "旧观察员",
                }
            )

            plan = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False
            )
            cli.fail_archive_once = True
            with self.assertRaisesRegex(RuntimeError, "simulated archive failure"):
                apply_plan(
                    root, cli, save_plan(root, plan), plan["plan_digest"][:12]
                )
            self.assertIn(retired_agent, cli.agents)
            self.assertFalse(
                any(
                    item["member_id"] == retired_agent["id"]
                    for item in cli.members[squad_id]
                )
            )

            retry = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False
            )
            retry_types = {item["type"] for item in retry["actions"]}
            self.assertIn("ARCHIVE_RETIRED_AGENT", retry_types)
            self.assertNotIn("REMOVE_RETIRED_MEMBER", retry_types)
            apply_plan(
                root, cli, save_plan(root, retry), retry["plan_digest"][:12]
            )
            self.assertIn(retired_agent, cli.archived_agents)

    def test_console_runs_doctor_then_drift(self):
        calls = []

        def fake_run(command, cwd=None):
            calls.append(command)
            return subprocess.CompletedProcess(command, 0)

        with (
            patch.object(sys, "argv", ["workflow_console.py", "status", "--repo", str(ROOT)]),
            patch.dict("os.environ", {}, clear=True),
            patch.object(workflow_console.subprocess, "run", side_effect=fake_run),
        ):
            self.assertEqual(workflow_console.main(), 0)
        self.assertIn("doctor", calls[0])
        self.assertIn("drift", calls[1])

    def test_console_returns_blocked_for_host_precondition_failures(self):
        with (
            patch.object(
                sys,
                "argv",
                ["workflow_console.py", "status", "--repo", str(ROOT / "missing")],
            ),
            patch.dict("os.environ", {}, clear=True),
        ):
            self.assertEqual(workflow_console.main(), 2)

        with (
            patch.object(sys, "argv", ["workflow_console.py", "status"]),
            patch.dict("os.environ", {"MULTICA_AGENT_ID": "agent-1"}, clear=True),
        ):
            self.assertEqual(workflow_console.main(), 2)


if __name__ == "__main__":
    unittest.main()
