from pathlib import Path
from contextlib import contextmanager
import importlib.util
import io
import json
import re
import shutil
import subprocess
import tempfile
import sys
import unittest
import zipfile


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from workflow_lib import (  # noqa: E402
    _runtime_choice,
    WorkflowError,
    apply_plan,
    build_plan,
    install_skills,
    parse_marker,
    plan_has_blockers,
    redact,
    render_marker,
    sha256_value,
    strip_marker,
    validate_repository,
    write_json,
)
import workflow as workflow_cli  # noqa: E402


class FakeCLI:
    def __init__(self):
        self.profile = "test"
        self.workspace_id = "workspace-test"
        self.calls = []

    def json(self, args, include_workspace=True):
        command = tuple(args)
        self.calls.append(command)
        if command[:2] == ("agent", "list"):
            return []
        if command[:2] == ("squad", "list"):
            return []
        if command[:2] == ("skill", "list"):
            return []
        if command[:2] == ("project", "list"):
            return []
        if command[:2] == ("autopilot", "list"):
            return []
        if command[:2] == ("runtime", "list"):
            return [
                {"id": "runtime-codex", "provider": "codex", "status": "online"},
                {"id": "runtime-opencode", "provider": "opencode", "status": "online"},
            ]
        if command[:3] == ("user", "profile", "get"):
            return {"id": "user-test", "name": "Tester"}
        raise AssertionError(f"unexpected fake command: {command}")


def flag(args, name, default=None):
    try:
        return args[args.index(name) + 1]
    except ValueError:
        return default


@contextmanager
def committed_temp_repo():
    with tempfile.TemporaryDirectory() as temp:
        temp_root = Path(temp) / "repo"
        shutil.copytree(
            ROOT,
            temp_root,
            ignore=shutil.ignore_patterns(".git", ".multica", "exports", "build", "__pycache__", "*.pyc"),
        )
        subprocess.run(["git", "init"], cwd=temp_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=temp_root, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_root, check=True)
        subprocess.run(["git", "add", "."], cwd=temp_root, check=True)
        subprocess.run(["git", "commit", "-m", "test"], cwd=temp_root, check=True, capture_output=True)
        yield temp_root


class MutatingCLI:
    def __init__(self):
        self.profile = "test"
        self.workspace_id = "workspace-test"
        self.agents = []
        self.squads = []
        self.skills = []
        self.projects = []
        self.autopilots = []
        self.issues = []
        self.fail_project_create = False
        self.autopilot_list_summaries = False
        self.skill_details = {}
        self.agent_skills = {}
        self.members = {}
        self.runtimes = [
            {"id": "runtime-codex", "provider": "codex", "status": "online"},
            {"id": "runtime-opencode", "provider": "opencode", "status": "online"},
        ]
        self.user = {"id": "user-test", "name": "Tester"}

    def json(self, args, include_workspace=True):
        args = list(args)
        command = tuple(args)
        if command[:2] == ("agent", "list"):
            return self.agents
        if command[:2] == ("squad", "list"):
            return self.squads
        if command[:2] == ("skill", "list"):
            return self.skills
        if command[:2] == ("project", "list"):
            return self.projects
        if command[:2] == ("autopilot", "list"):
            if self.autopilot_list_summaries:
                return [
                    {
                        key: value
                        for key, value in item.items()
                        if key not in {"description", "triggers", "subscribers"}
                    }
                    for item in self.autopilots
                ]
            return self.autopilots
        if command[:2] == ("issue", "list"):
            limit = int(flag(args, "--limit", "100"))
            offset = int(flag(args, "--offset", "0"))
            return self.issues[offset : offset + limit]
        if command[:2] == ("autopilot", "get"):
            return next(item for item in self.autopilots if item["id"] == args[2])
        if command[:2] == ("runtime", "list"):
            return self.runtimes
        if command[:3] == ("user", "profile", "get"):
            return self.user
        if command[:3] == ("skill", "get", command[2] if len(command) > 2 else None):
            return self.skill_details[args[2]]
        if command[:3] == ("agent", "skills", "list"):
            return self.agent_skills.get(args[3], [])
        if command[:3] == ("squad", "member", "list"):
            return self.members.get(args[3], [])
        if command[:2] == ("skill", "import"):
            import zipfile

            archive_path = Path(flag(args, "--file"))
            with zipfile.ZipFile(archive_path) as archive:
                content = archive.read("SKILL.md").decode("utf-8")
            name = re.search(r"(?m)^name:\s*(.+)$", content).group(1).strip()
            existing = next((item for item in self.skills if item["name"] == name), None)
            if existing:
                skill_id = existing["id"]
            else:
                skill_id = f"skill-{len(self.skills) + 1}"
                existing = {"id": skill_id, "name": name, "description": ""}
                self.skills.append(existing)
            detail = {**existing, "content": content}
            self.skill_details[skill_id] = detail
            return detail
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
                "max_concurrent_tasks": int(flag(args, "--max-concurrent-tasks", "1")),
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
            mapping = {
                "--name": "name",
                "--description": "description",
                "--instructions": "instructions",
                "--runtime-id": "runtime_id",
                "--model": "model",
                "--thinking-level": "thinking_level",
            }
            for option, key in mapping.items():
                if option in args:
                    agent[key] = flag(args, option, "")
            if "--max-concurrent-tasks" in args:
                agent["max_concurrent_tasks"] = int(flag(args, "--max-concurrent-tasks"))
            if "--permission-mode" in args:
                agent["permission_mode"] = flag(args, "--permission-mode")
                agent["invocation_targets"] = (
                    [{"target_type": "workspace", "target_id": self.workspace_id}]
                    if "--public-to-workspace" in args
                    else []
                )
            return agent
        if command[:3] == ("agent", "skills", "set"):
            agent_id = args[3]
            ids = [item for item in flag(args, "--skill-ids", "").split(",") if item]
            self.agent_skills[agent_id] = [self.skill_details[item] for item in ids]
            return self.agent_skills[agent_id]
        if command[:2] == ("project", "create"):
            if self.fail_project_create:
                raise WorkflowError("injected project create failure")
            project_id = f"project-{len(self.projects) + 1}"
            lead = next(item for item in self.agents if item["name"] == flag(args, "--lead"))
            project = {
                "id": project_id,
                "title": flag(args, "--title"),
                "description": flag(args, "--description", ""),
                "lead_id": lead["id"],
                "lead_type": "agent",
                "status": flag(args, "--status", "in_progress"),
                "icon": flag(args, "--icon", ""),
            }
            self.projects.append(project)
            return project
        if command[:2] == ("project", "update"):
            project = next(item for item in self.projects if item["id"] == args[2])
            if "--lead" in args:
                lead = next(item for item in self.agents if item["name"] == flag(args, "--lead"))
                project["lead_id"] = lead["id"]
            mapping = {
                "--title": "title",
                "--description": "description",
                "--status": "status",
                "--icon": "icon",
            }
            for option, key in mapping.items():
                if option in args:
                    project[key] = flag(args, option, "")
            return project
        if command[:2] == ("autopilot", "create"):
            autopilot_id = f"autopilot-{len(self.autopilots) + 1}"
            autopilot = {
                "id": autopilot_id,
                "title": flag(args, "--title"),
                "description": flag(args, "--description", ""),
                "agent_id": flag(args, "--agent"),
                "mode": flag(args, "--mode"),
                "project_id": flag(args, "--project"),
                "priority": flag(args, "--priority", "none"),
                "status": "active",
                "issue_title_template": flag(args, "--issue-title-template", ""),
                "subscribers": [
                    {"id": args[index + 1]}
                    for index, item in enumerate(args)
                    if item == "--subscriber"
                ],
                "triggers": [],
            }
            self.autopilots.append(autopilot)
            return autopilot
        if command[:2] == ("autopilot", "update"):
            autopilot = next(item for item in self.autopilots if item["id"] == args[2])
            mapping = {
                "--title": "title",
                "--description": "description",
                "--agent": "agent_id",
                "--mode": "mode",
                "--project": "project_id",
                "--priority": "priority",
                "--status": "status",
                "--issue-title-template": "issue_title_template",
            }
            for option, key in mapping.items():
                if option in args:
                    autopilot[key] = flag(args, option, "")
            if "--clear-subscribers" in args:
                autopilot["subscribers"] = []
            autopilot["subscribers"].extend(
                {"id": args[index + 1]}
                for index, item in enumerate(args)
                if item == "--subscriber"
            )
            return autopilot
        if command[:2] == ("autopilot", "trigger-add"):
            autopilot = next(item for item in self.autopilots if item["id"] == args[2])
            trigger = {
                "id": f"trigger-{len(autopilot['triggers']) + 1}",
                "kind": flag(args, "--kind"),
                "label": flag(args, "--label", ""),
                "enabled": True,
                "cron": flag(args, "--cron", ""),
                "timezone": flag(args, "--timezone", "UTC"),
            }
            autopilot["triggers"].append(trigger)
            return trigger
        if command[:2] == ("autopilot", "trigger-update"):
            autopilot = next(item for item in self.autopilots if item["id"] == args[2])
            trigger = next(item for item in autopilot["triggers"] if item["id"] == args[3])
            if "--label" in args:
                trigger["label"] = flag(args, "--label", "")
            enabled_arg = next((item for item in args if item.startswith("--enabled=")), None)
            if enabled_arg:
                trigger["enabled"] = enabled_arg.split("=", 1)[1].lower() == "true"
            if "--cron" in args:
                trigger["cron"] = flag(args, "--cron", "")
            if "--timezone" in args:
                trigger["timezone"] = flag(args, "--timezone", "UTC")
            return trigger
        if command[:2] == ("squad", "create"):
            squad_id = f"squad-{len(self.squads) + 1}"
            squad = {
                "id": squad_id,
                "name": flag(args, "--name"),
                "description": flag(args, "--description", ""),
                "instructions": "",
                "leader_id": flag(args, "--leader"),
            }
            self.squads.append(squad)
            self.members[squad_id] = []
            return squad
        if command[:2] == ("squad", "update"):
            squad = next(item for item in self.squads if item["id"] == args[2])
            mapping = {
                "--name": "name",
                "--description": "description",
                "--instructions": "instructions",
                "--leader": "leader_id",
            }
            for option, key in mapping.items():
                if option in args:
                    squad[key] = flag(args, option, "")
            return squad
        if command[:3] == ("squad", "member", "add"):
            squad_id = args[3]
            item = {
                "member_id": flag(args, "--member-id"),
                "member_type": flag(args, "--type"),
                "role": flag(args, "--role"),
            }
            self.members[squad_id].append(item)
            return item
        if command[:3] == ("squad", "member", "set-role"):
            squad_id = args[3]
            member_id = flag(args, "--member-id")
            member_type = flag(args, "--member-type")
            item = next(
                entry
                for entry in self.members[squad_id]
                if entry["member_id"] == member_id and entry["member_type"] == member_type
            )
            item["role"] = flag(args, "--role")
            return item
        raise AssertionError(f"unexpected mutating command: {command}")


class ReconcileTests(unittest.TestCase):
    def test_marker_round_trip(self):
        body = "hello\n"
        rendered = render_marker("wf", "agent.leader", "abc", body)
        self.assertEqual(parse_marker(rendered)["object_key"], "agent.leader")
        self.assertEqual(strip_marker(rendered), body)

    def test_managed_marker_is_not_confused_by_previous_name_support(self):
        rendered = render_marker("wf", "agent.leader", "abc", "body\n")
        self.assertEqual(parse_marker(rendered)["workflow_id"], "wf")

    def test_runtime_preserves_compatible_existing_binding(self):
        binding = {"provider": "codex", "required_status": "online"}
        runtime = {"id": "old", "provider": "codex", "status": "online"}
        selected, error = _runtime_choice(
            "binding", binding, {"bindings": {}}, [runtime], {"name": "Agent", "runtime_id": "old"}, {"old": runtime}, False
        )
        self.assertEqual(selected, "old")
        self.assertIsNone(error)

    def test_runtime_ambiguity_requires_local_map(self):
        binding = {"provider": "codex", "required_status": "online"}
        runtimes = [
            {"id": "one", "provider": "codex", "status": "online"},
            {"id": "two", "provider": "codex", "status": "online"},
        ]
        selected, error = _runtime_choice("binding", binding, {"bindings": {}}, runtimes, None, {}, False)
        self.assertIsNone(selected)
        self.assertIn("multiple Runtimes", error)

    def test_empty_workspace_plan_rebuilds_all_objects(self):
        cli = FakeCLI()
        workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
        with tempfile.TemporaryDirectory() as temp:
            runtime_map = Path(temp) / "runtime-map.json"
            plan = build_plan(ROOT, cli, workspace, "quality", runtime_map, False, False, write_archives=False)
        self.assertFalse(plan_has_blockers(plan))
        types = [action["type"] for action in plan["actions"]]
        self.assertEqual(types.count("CREATE_AGENT"), 10)
        self.assertEqual(types.count("CREATE_PROJECT"), 1)
        self.assertEqual(types.count("CREATE_AUTOPILOT"), 1)
        self.assertEqual(types.count("ADD_AUTOPILOT_TRIGGER"), 1)
        self.assertEqual(types.count("CREATE_SQUAD"), 1)
        self.assertEqual(types.count("ADD_MEMBER"), 8)
        self.assertEqual(types.count("CREATE_SKILL"), 3)
        self.assertEqual(types.count("ATTACH_SKILL"), 10)

    def test_repository_validation_enforces_complete_workflow_schema(self):
        cases = {
            "invalid autopilot mode": lambda value: value["autopilots"][0].update(
                {"mode": "invalid"}
            ),
            "invalid autopilot status": lambda value: value["autopilots"][0].update(
                {"status": "invalid"}
            ),
            "missing schedule cron": lambda value: value["autopilots"][0][
                "triggers"
            ][0].pop("cron"),
            "missing schedule timezone": lambda value: value["autopilots"][0][
                "triggers"
            ][0].pop("timezone"),
            "missing trigger kind": lambda value: value["autopilots"][0][
                "triggers"
            ][0].pop("kind"),
            "missing trigger label": lambda value: value["autopilots"][0][
                "triggers"
            ][0].pop("label"),
            "unknown project property": lambda value: value["projects"][0].update(
                {"unexpected": True}
            ),
            "unknown autopilot property": lambda value: value["autopilots"][0].update(
                {"unexpected": True}
            ),
        }
        for label, mutate in cases.items():
            with self.subTest(case=label), committed_temp_repo() as temp_root:
                path = temp_root / "workflow.json"
                manifest = json.loads(path.read_text(encoding="utf-8"))
                mutate(manifest)
                write_json(path, manifest)
                with self.assertRaisesRegex(WorkflowError, "workflow.json schema"):
                    validate_repository(temp_root, "quality")

    def test_redaction_handles_nested_secret_objects(self):
        value = {"token": "secret", "mcp_config": {"servers": []}, "nested": [{"password": "p"}]}
        result = redact(value)
        self.assertEqual(result["token"], "<redacted>")
        self.assertEqual(result["mcp_config"], "<redacted>")
        self.assertEqual(result["nested"][0]["password"], "<redacted>")

    def test_install_skills_copy_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            results = install_skills(ROOT, Path(temp), copy_mode=True, replace_existing=False)
            self.assertEqual(
                {item["skill"] for item in results},
                {
                    "multica-requirement-intake",
                    "multica-workflow-manager",
                    "multica-workflow-observer",
                    "multica-workflow-maintainer",
                },
            )
            self.assertTrue((Path(temp) / "multica-workflow-manager/SKILL.md").is_file())

    def test_draft_plan_cannot_be_applied(self):
        plan = {
            "schema_version": 1,
            "draft": True,
            "actions": [],
        }
        plan["plan_digest"] = sha256_value(plan)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "plan.json"
            write_json(path, plan)
            with self.assertRaisesRegex(WorkflowError, "draft plans"):
                apply_plan(ROOT, FakeCLI(), path, plan["plan_digest"][:12])

    def test_empty_workspace_apply_reaches_no_change(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            plan = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            self.assertFalse(plan["draft"])
            self.assertFalse(plan_has_blockers(plan))
            plan_path = temp_root / ".multica/plans/plan.json"
            write_json(plan_path, plan)
            apply_plan(temp_root, cli, plan_path, plan["plan_digest"][:12])

            verification = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False, write_archives=False)
            remaining = [
                action
                for action in verification["actions"]
                if action["type"] not in {"NO_CHANGE", "WARNING"}
            ]
            self.assertEqual(remaining, [])

    def test_disable_operations_pauses_autopilot_and_detaches_reporters(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            initial = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            initial_path = temp_root / ".multica/plans/initial.json"
            write_json(initial_path, initial)
            apply_plan(temp_root, cli, initial_path, initial["plan_digest"][:12])

            local_skill = {
                "id": "skill-local-manager",
                "name": "multica-workflow-manager",
            }
            cli.skills.append(local_skill)
            cli.skill_details[local_skill["id"]] = {
                **local_skill,
                "content": "name: multica-workflow-manager\n",
            }
            leader = next(
                item
                for item in cli.agents
                if parse_marker(item["instructions"])["object_key"] == "agent.leader"
            )
            cli.agent_skills[leader["id"]].append(cli.skill_details[local_skill["id"]])

            disabled = build_plan(
                temp_root,
                cli,
                workspace,
                "quality",
                runtime_map,
                False,
                False,
                disable_operations=True,
                write_archives=False,
            )
            types = [item["type"] for item in disabled["actions"]]
            self.assertEqual(types.count("DETACH_SKILL"), 7)
            self.assertEqual(types.count("UPDATE_AUTOPILOT"), 1)
            desired = next(item["desired"] for item in disabled["actions"] if item["type"] == "UPDATE_AUTOPILOT")
            self.assertEqual(desired["status"], "paused")
            disabled_path = temp_root / ".multica/plans/disabled.json"
            write_json(disabled_path, disabled)
            apply_plan(temp_root, cli, disabled_path, disabled["plan_digest"][:12])

            manifest = json.loads((temp_root / "workflow.json").read_text(encoding="utf-8"))
            observer_skill_id = next(
                item["id"] for item in cli.skills if item["name"] == "multica-workflow-observer"
            )
            for agent_key in manifest["operations"]["reporter_agents"]:
                agent = next(
                    item
                    for item in cli.agents
                    if parse_marker(item["instructions"])["object_key"] == f"agent.{agent_key}"
                )
                assigned_ids = {item["id"] for item in cli.agent_skills[agent["id"]]}
                self.assertNotIn(observer_skill_id, assigned_ids, agent_key)
            observer_agent = next(
                item
                for item in cli.agents
                if parse_marker(item["instructions"])["object_key"] == "agent.workflow-observer"
            )
            self.assertIn(
                observer_skill_id,
                {item["id"] for item in cli.agent_skills[observer_agent["id"]]},
            )
            self.assertEqual(cli.autopilots[0]["status"], "paused")
            self.assertIn(
                local_skill["id"],
                {item["id"] for item in cli.agent_skills[leader["id"]]},
            )

    def test_disable_operations_blocks_active_v3_without_explicit_degraded_approval(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            cli.issues = [
                {
                    "id": "req-1",
                    "identifier": "T-100",
                    "status": "in_progress",
                    "parent_issue_id": None,
                    "metadata": {
                        "workflow_id": "development-delivery",
                        "protocol_revision": "v3",
                    },
                }
            ]
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            blocked = build_plan(
                temp_root,
                cli,
                workspace,
                "quality",
                runtime_map,
                False,
                False,
                disable_operations=True,
                write_archives=False,
            )
            self.assertTrue(
                any(
                    item["type"] == "BLOCKED" and item["key"] == "active-v3-rollback"
                    for item in blocked["actions"]
                )
            )
            degraded = build_plan(
                temp_root,
                cli,
                workspace,
                "quality",
                runtime_map,
                False,
                False,
                disable_operations=True,
                allow_active_v3_degraded=True,
                write_archives=False,
            )
            self.assertFalse(plan_has_blockers(degraded))
            self.assertTrue(
                any(
                    item["type"] == "WARNING" and item["key"] == "active-v3-rollback"
                    for item in degraded["actions"]
                )
            )

    def test_reporter_skills_are_deferred_until_control_plane_exists(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            plan = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            plan_path = temp_root / ".multica/plans/plan.json"
            write_json(plan_path, plan)
            cli.fail_project_create = True
            with self.assertRaisesRegex(WorkflowError, "injected project create failure"):
                apply_plan(temp_root, cli, plan_path, plan["plan_digest"][:12])

            manifest = json.loads((temp_root / "workflow.json").read_text(encoding="utf-8"))
            reporter_object_keys = {
                f"agent.{agent_key}" for agent_key in manifest["operations"]["reporter_agents"]
            }
            created_object_keys = {
                parse_marker(item["instructions"])["object_key"] for item in cli.agents
            }
            self.assertTrue(reporter_object_keys.isdisjoint(created_object_keys))

    def test_project_lead_name_ambiguity_blocks_during_planning(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            initial = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            initial_path = temp_root / ".multica/plans/initial.json"
            write_json(initial_path, initial)
            apply_plan(temp_root, cli, initial_path, initial["plan_digest"][:12])

            lead = next(
                item
                for item in cli.agents
                if parse_marker(item["instructions"])["object_key"] == "agent.workflow-observer"
            )
            duplicate = {**lead, "id": "agent-duplicate-lead", "instructions": "unmanaged"}
            cli.agents.append(duplicate)
            cli.agent_skills[duplicate["id"]] = []
            plan = build_plan(
                temp_root, cli, workspace, "quality", runtime_map, False, False, write_archives=False
            )
            blockers = [item for item in plan["actions"] if item["type"] == "BLOCKED"]
            self.assertTrue(
                any(
                    item["key"] == "workflow-operations"
                    and "project lead name is not unique" in item["reason"]
                    for item in blockers
                )
            )

    def test_repository_audit_and_health_accept_json_output(self):
        self.assertEqual(workflow_cli.parser().parse_args(["audit", "--output", "json"]).output, "json")
        self.assertEqual(workflow_cli.parser().parse_args(["health", "--output", "json"]).output, "json")

    def test_repository_console_output_escapes_unencodable_characters(self):
        class AsciiStream(io.StringIO):
            encoding = "ascii"

        stream = AsciiStream()
        workflow_cli.write_console_safe("value=\ufffd", stream)
        self.assertEqual(stream.getvalue(), "value=\\ufffd")

    def test_unmarked_project_requires_adopt(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            initial = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            initial_path = temp_root / ".multica/plans/initial.json"
            write_json(initial_path, initial)
            apply_plan(temp_root, cli, initial_path, initial["plan_digest"][:12])
            cli.projects[0]["description"] = "unmarked\n"
            plan = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False, write_archives=False)
            blockers = [item for item in plan["actions"] if item["type"] == "BLOCKED"]
            self.assertTrue(any("requires --adopt" in item["reason"] for item in blockers))

    def test_unmarked_same_hash_skill_is_reimported_when_adopted(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            initial = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            initial_path = temp_root / ".multica/plans/initial.json"
            write_json(initial_path, initial)
            apply_plan(temp_root, cli, initial_path, initial["plan_digest"][:12])

            skill = next(item for item in cli.skills if item["name"] == "multica-workflow-observer")
            detail = cli.skill_details[skill["id"]]
            detail["content"] = re.sub(
                r"(?m)^\s*(managed_by|workflow_id):.*\r?\n?", "", detail["content"]
            )

            blocked = build_plan(
                temp_root, cli, workspace, "quality", runtime_map, False, False, write_archives=False
            )
            self.assertTrue(
                any(
                    item["type"] == "BLOCKED" and item["key"] == "workflow-observer"
                    for item in blocked["actions"]
                )
            )

            adopted = build_plan(
                temp_root, cli, workspace, "quality", runtime_map, True, False, write_archives=False
            )
            self.assertTrue(
                any(
                    item["type"] == "ADOPT_SKILL" and item["key"] == "workflow-observer"
                    for item in adopted["actions"]
                )
            )
            adopted_path = temp_root / ".multica/plans/adopted.json"
            write_json(adopted_path, adopted)
            apply_plan(temp_root, cli, adopted_path, adopted["plan_digest"][:12])
            self.assertIn(
                "managed_by: multica-dev-workflow",
                cli.skill_details[skill["id"]]["content"],
            )

    def test_unrelated_autopilot_trigger_is_preserved(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            initial = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            initial_path = temp_root / ".multica/plans/initial.json"
            write_json(initial_path, initial)
            apply_plan(temp_root, cli, initial_path, initial["plan_digest"][:12])
            cli.autopilots[0]["triggers"].append(
                {"id": "trigger-extra", "kind": "schedule", "label": "user-trigger", "enabled": True, "cron": "30 * * * *", "timezone": "UTC"}
            )
            plan = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False, write_archives=False)
            mutations = [item for item in plan["actions"] if item["type"] not in {"NO_CHANGE", "WARNING"}]
            self.assertEqual(mutations, [])

    def test_autopilot_summary_list_uses_get_details_for_plan_and_disable(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            initial = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            initial_path = temp_root / ".multica/plans/initial.json"
            write_json(initial_path, initial)
            apply_plan(temp_root, cli, initial_path, initial["plan_digest"][:12])

            cli.autopilot_list_summaries = True
            verification = build_plan(
                temp_root, cli, workspace, "quality", runtime_map, False, False, write_archives=False
            )
            remaining = [
                item
                for item in verification["actions"]
                if item["type"] not in {"NO_CHANGE", "WARNING"}
            ]
            self.assertEqual(remaining, [])

            disabled = build_plan(
                temp_root,
                cli,
                workspace,
                "quality",
                runtime_map,
                False,
                False,
                disable_operations=True,
                write_archives=False,
            )
            update = next(
                item
                for item in disabled["actions"]
                if item["type"] == "UPDATE_AUTOPILOT"
            )
            self.assertEqual(update["desired"]["status"], "paused")

    def test_changed_autopilot_trigger_without_id_blocks_planning(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            initial = build_plan(
                temp_root, cli, workspace, "quality", runtime_map, False, False
            )
            initial_path = temp_root / ".multica/plans/initial.json"
            write_json(initial_path, initial)
            apply_plan(temp_root, cli, initial_path, initial["plan_digest"][:12])

            trigger = cli.autopilots[0]["triggers"][0]
            trigger.pop("id")
            trigger["cron"] = "30 * * * *"
            plan = build_plan(
                temp_root,
                cli,
                workspace,
                "quality",
                runtime_map,
                False,
                False,
                write_archives=False,
            )
            blocked = [
                item
                for item in plan["actions"]
                if item["type"] == "BLOCKED"
                and item["key"] == "workflow-health-audit.hourly"
            ]
            self.assertEqual(len(blocked), 1)
            self.assertIn("omitted its trigger ID", blocked[0]["reason"])
            self.assertFalse(
                any(
                    item["type"] == "UPDATE_AUTOPILOT_TRIGGER"
                    for item in plan["actions"]
                )
            )

    def test_new_agent_inherits_unique_managed_binding_runtime(self):
        with committed_temp_repo() as temp_root:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            initial = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            initial_path = temp_root / ".multica/plans/initial.json"
            write_json(initial_path, initial)
            apply_plan(temp_root, cli, initial_path, initial["plan_digest"][:12])
            observer_agent = next(item for item in cli.agents if parse_marker(item["instructions"])["object_key"] == "agent.workflow-observer")
            cli.agents.remove(observer_agent)
            cli.agent_skills.pop(observer_agent["id"], None)
            cli.runtimes.append({"id": "runtime-opencode-extra", "provider": "opencode", "status": "online"})
            plan = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False, write_archives=False)
            self.assertFalse(plan_has_blockers(plan))
            create = next(item for item in plan["actions"] if item["type"] == "CREATE_AGENT" and item["key"] == "workflow-observer")
            self.assertEqual(create["desired"]["runtime_id"], "runtime-opencode")

    def test_actual_v1_reconciler_ignores_paused_v11_control_plane(self):
        with committed_temp_repo() as temp_root, tempfile.TemporaryDirectory() as old_temp:
            cli = MutatingCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = temp_root / ".multica/runtime-map.local.json"
            initial = build_plan(temp_root, cli, workspace, "quality", runtime_map, False, False)
            initial_path = temp_root / ".multica/plans/initial.json"
            write_json(initial_path, initial)
            apply_plan(temp_root, cli, initial_path, initial["plan_digest"][:12])

            disabled = build_plan(
                temp_root,
                cli,
                workspace,
                "quality",
                runtime_map,
                False,
                False,
                disable_operations=True,
            )
            disabled_path = temp_root / ".multica/plans/disabled.json"
            write_json(disabled_path, disabled)
            apply_plan(temp_root, cli, disabled_path, disabled["plan_digest"][:12])
            self.assertEqual(cli.autopilots[0]["status"], "paused")

            archive = Path(old_temp) / "v1.zip"
            result = subprocess.run(
                ["git", "archive", "--format=zip", "--output", str(archive), "v1.0.0"],
                cwd=ROOT,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                self.skipTest("v1.0.0 tag unavailable in this checkout")
            old_root = Path(old_temp) / "v1"
            old_root.mkdir()
            with zipfile.ZipFile(archive) as source:
                source.extractall(old_root)
            subprocess.run(["git", "init"], cwd=old_root, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=old_root, check=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=old_root, check=True)
            subprocess.run(["git", "add", "."], cwd=old_root, check=True)
            subprocess.run(["git", "commit", "-m", "v1"], cwd=old_root, check=True, capture_output=True)

            spec = importlib.util.spec_from_file_location("workflow_lib_v1", old_root / "scripts/workflow_lib.py")
            old_lib = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = old_lib
            spec.loader.exec_module(old_lib)
            old_runtime_map = old_root / ".multica/runtime-map.local.json"
            rollback = old_lib.build_plan(old_root, cli, workspace, "quality", old_runtime_map, False, False)
            self.assertFalse(old_lib.plan_has_blockers(rollback))
            rollback_path = old_root / ".multica/plans/rollback.json"
            old_lib.write_json(rollback_path, rollback)
            old_lib.apply_plan(old_root, cli, rollback_path, rollback["plan_digest"][:12])
            verification = old_lib.build_plan(
                old_root, cli, workspace, "quality", old_runtime_map, False, False, write_archives=False
            )
            remaining = [
                item for item in verification["actions"] if item["type"] not in {"NO_CHANGE", "WARNING"}
            ]
            self.assertEqual(remaining, [])
            self.assertEqual(len(cli.projects), 1)
            self.assertEqual(len(cli.autopilots), 1)
            self.assertEqual(cli.autopilots[0]["status"], "paused")


if __name__ == "__main__":
    unittest.main()
