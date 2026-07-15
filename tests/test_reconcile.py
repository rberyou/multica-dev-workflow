from pathlib import Path
import json
import re
import shutil
import subprocess
import tempfile
import sys
import unittest


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
    write_json,
)


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


class MutatingCLI:
    def __init__(self):
        self.profile = "test"
        self.workspace_id = "workspace-test"
        self.agents = []
        self.squads = []
        self.skills = []
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
        self.assertEqual(types.count("CREATE_AGENT"), 7)
        self.assertEqual(types.count("CREATE_SQUAD"), 1)
        self.assertEqual(types.count("ADD_MEMBER"), 8)
        self.assertEqual(types.count("CREATE_SKILL"), 1)

    def test_redaction_handles_nested_secret_objects(self):
        value = {"token": "secret", "mcp_config": {"servers": []}, "nested": [{"password": "p"}]}
        result = redact(value)
        self.assertEqual(result["token"], "<redacted>")
        self.assertEqual(result["mcp_config"], "<redacted>")
        self.assertEqual(result["nested"][0]["password"], "<redacted>")

    def test_install_skills_copy_mode(self):
        with tempfile.TemporaryDirectory() as temp:
            results = install_skills(ROOT, Path(temp), copy_mode=True, replace_existing=False)
            self.assertEqual({item["skill"] for item in results}, {"multica-requirement-intake", "multica-workflow-manager"})
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


if __name__ == "__main__":
    unittest.main()
