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
    _replace_local_skill_copy,
    _runtime_choice,
    apply_local_skill_action,
    apply_plan,
    build_local_skill_state,
    default_local_skill_root,
    build_plan,
    deployment_evidence_record_path,
    deployment_record_path,
    install_skills,
    load_deployment_record,
    mutation_actions,
    parse_marker,
    plan_has_blockers,
    redact,
    render_marker,
    save_plan,
    source_identity,
    strip_marker,
    validate_release_bundle,
    resolve_local_skill_root,
    validate_repository,
)
import release  # noqa: E402
import workflow_lib  # noqa: E402


_build_plan = build_plan


def build_plan(*args, **kwargs):
    root = Path(args[0] if args else kwargs["root"])
    kwargs.setdefault("local_skill_root", root.parent / "home/.agents/skills")
    return _build_plan(*args, **kwargs)


CONSOLE_PATH = ROOT / "skills/multica-workflow-console/scripts/workflow_console.py"
CONSOLE_SPEC = importlib.util.spec_from_file_location("workflow_console", CONSOLE_PATH)
workflow_console = importlib.util.module_from_spec(CONSOLE_SPEC)
CONSOLE_SPEC.loader.exec_module(workflow_console)


def flag(args, name, default=None):
    try:
        return args[args.index(name) + 1]
    except ValueError:
        return default


def runtime_map_for(root: Path, workspace_id: str = "workspace-test") -> Path:
    return (
        root.parent
        / "home"
        / ".multica"
        / "workflows"
        / "development-delivery"
        / "runtime-maps"
        / f"{workspace_id}.json"
    )


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


@contextmanager
def released_temp_bundle():
    with committed_temp_repo() as repository:
        source_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repository,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        version = (repository / "VERSION").read_text(encoding="utf-8").strip()
        archive = repository.parent / "workflow-release.zip"
        release.build_repository_archive(
            repository,
            {
                "version": version,
                "tag": f"v{version}",
                "source_commit": source_commit,
            },
            archive,
        )
        root = repository.parent / "released"
        with zipfile.ZipFile(archive) as package:
            package.extractall(root)
        yield root


class FakeCLI:
    def __init__(self):
        self.profile = "test"
        self.workspace_id = "workspace-test"
        self.calls = []
        self.text_calls = []
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

    def text(self, args, include_workspace=True, check=True):
        args = list(args)
        command = tuple(args)
        self.calls.append(command)
        self.text_calls.append(command)
        if command[:2] == ("autopilot", "delete"):
            autopilot = next(item for item in self.autopilots if item["id"] == args[2])
            self.autopilots.remove(autopilot)
            return f"Autopilot {args[2]} deleted."
        if command[:2] == ("skill", "delete"):
            skill_id = args[2]
            self.skills = [item for item in self.skills if item["id"] != skill_id]
            self.skill_details.pop(skill_id, None)
            return f"Skill {skill_id} deleted."
        raise AssertionError(f"unexpected fake text command: {command}")

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
    def test_project_execution_state_stays_in_checkout(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "repo"
            root.mkdir()
            plan_path = save_plan(root, {"plan_digest": "a" * 64})
            self.assertEqual(plan_path.parent, root / ".multica/plans")
            self.assertEqual(
                deployment_record_path(root, "workspace-test").parent,
                root / ".multica/deployments",
            )
            self.assertEqual(
                deployment_evidence_record_path(
                    root, "workspace-test", "b" * 64
                ).parent.parent,
                root / ".multica/deployments",
            )

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
            runtime_map = runtime_map_for(root)
            plan = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False
            )
            types = [item["type"] for item in plan["actions"]]
            self.assertFalse(plan_has_blockers(plan))
            self.assertIn("CREATE_AGENT", types)
            self.assertIn("CREATE_PROJECT", types)
            self.assertIn("CREATE_SQUAD", types)
            self.assertNotIn("CREATE_AUTOPILOT", types)
            local_actions = [
                item for item in plan["actions"] if item.get("scope") == "local_skill"
            ]
            self.assertEqual(
                {item["skill"] for item in local_actions},
                {
                    "multica-delivery-policy",
                    "multica-requirement-intake",
                    "multica-workflow-manager",
                    "multica-workflow-incidents",
                    "multica-workflow-console",
                },
            )
            self.assertEqual(
                {item["type"] for item in local_actions}, {"CREATE_LOCAL_SKILL"}
            )
            plan_path = save_plan(root, plan)
            journal = apply_plan(root, cli, plan_path, plan["plan_digest"][:12])
            self.assertTrue(journal.get("finished_at"))
            self.assertEqual(len(cli.agents), 7)
            self.assertEqual(len(cli.projects), 1)
            self.assertEqual(len(cli.squads), 1)
            self.assertEqual(len(cli.skills), 3)
            local_root = Path(plan["local_skills"]["root"])
            self.assertTrue(
                all(
                    (local_root / item["skill"]).is_dir()
                    and not (local_root / item["skill"]).is_symlink()
                    for item in local_actions
                )
            )
            deployment_record = load_deployment_record(root, cli.workspace_id)
            self.assertIsNotNone(deployment_record)
            self.assertEqual(
                Path(deployment_record["journal"]).parent,
                root / ".multica/journals",
            )
            self.assertEqual(deployment_record["local_skills"]["mode"], "copy")
            self.assertEqual(len(deployment_record["local_skills"]["results"]), 5)
            self.assertNotIn(
                str(local_root),
                json.dumps(deployment_record["local_skills"]["results"]),
            )
            second = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False, False
            )
            self.assertFalse(plan_has_blockers(second))
            self.assertEqual(mutation_actions(second), [])

    def test_release_bundle_plans_and_applies_without_git(self):
        with released_temp_bundle() as root:
            self.assertFalse((root / ".git").exists())
            extracted_check = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import json,sys; from pathlib import Path; "
                        "sys.path.insert(0, 'scripts'); "
                        "from workflow_lib import source_identity; "
                        "print(json.dumps(source_identity(Path('.'))[0]))"
                    ),
                ],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                json.loads(extracted_check.stdout)["type"], "release_bundle"
            )
            release_manifest = validate_release_bundle(root)
            source, dirty = source_identity(root)
            self.assertEqual(source["type"], "release_bundle")
            self.assertEqual(source["id"], release_manifest["bundle_digest"])
            self.assertFalse(dirty)

            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = runtime_map_for(root)
            plan = build_plan(
                root, cli, workspace, "quality", runtime_map, False, False
            )
            self.assertFalse(plan["draft"])
            self.assertEqual(plan["source"], source)
            apply_plan(root, cli, save_plan(root, plan), plan["plan_digest"][:12])
            deployment_record = load_deployment_record(root, cli.workspace_id)
            self.assertEqual(deployment_record["source"], source)
            self.assertTrue(
                (Path(plan["local_skills"]["root"]) / "multica-workflow-manager").is_dir()
            )

            standalone_target = root.parent / "standalone-local-skills"
            standalone = install_skills(root, standalone_target)
            self.assertEqual(
                {item["action"] for item in standalone}, {"CREATE_LOCAL_SKILL"}
            )
            self.assertTrue(
                (standalone_target / "multica-workflow-manager").is_dir()
            )

    def test_release_bundle_rejects_changed_or_undeclared_source(self):
        with released_temp_bundle() as root:
            workflow_path = root / "workflow.json"
            workflow_path.write_text(
                workflow_path.read_text(encoding="utf-8") + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "file changed"):
                source_identity(root)

        with released_temp_bundle() as root:
            extra_profile = root / "deployment-profiles/extra.json"
            extra_profile.write_text("{}\n", encoding="utf-8")
            with self.assertRaisesRegex(WorkflowError, "undeclared workflow file"):
                source_identity(root)

        with released_temp_bundle() as root:
            manifest_path = root / "release-manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["workflow_version"] = "9.9.9"
            manifest["bundle_digest"] = release.digest(
                {
                    key: value
                    for key, value in manifest.items()
                    if key != "bundle_digest"
                }
            )
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "workflow_version"):
                source_identity(root)

    def test_release_bundle_change_invalidates_an_approved_plan(self):
        with released_temp_bundle() as root:
            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            plan = build_plan(
                root,
                cli,
                workspace,
                "quality",
                runtime_map_for(root),
                False,
                False,
            )
            plan_path = save_plan(root, plan)
            instructions = root / "instructions/common.md"
            instructions.write_text(
                instructions.read_text(encoding="utf-8") + "\nchanged\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "file changed"):
                apply_plan(root, cli, plan_path, plan["plan_digest"][:12])

    def test_apply_rejects_changed_multica_state(self):
        with committed_temp_repo() as root:
            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = runtime_map_for(root)
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
                runtime_map_for(root),
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
            runtime_map = runtime_map_for(root)
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
            self.assertIn(
                ("autopilot", "delete", "autopilot-retired"), cli.text_calls
            )
            self.assertIn(
                ("skill", "delete", "skill-retired", "--yes"), cli.text_calls
            )
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
            results = install_skills(ROOT, Path(temp))
            self.assertEqual(
                {item["skill"] for item in results},
                {
                    "multica-delivery-policy",
                    "multica-requirement-intake",
                    "multica-workflow-manager",
                    "multica-workflow-incidents",
                    "multica-workflow-console",
                },
            )
            self.assertFalse(
                any(Path(temp).rglob("__pycache__"))
            )
            self.assertEqual({item["action"] for item in results}, {"CREATE_LOCAL_SKILL"})
            second = install_skills(ROOT, Path(temp))
            self.assertEqual({item["action"] for item in second}, {"NO_CHANGE"})
            self.assertFalse(any(path.is_symlink() for path in Path(temp).iterdir()))

            manifest, _ = validate_repository(ROOT, "quality")
            workspace_only = json.loads(json.dumps(manifest))
            next(
                item
                for item in workspace_only["skills"]
                if item["name"] == "multica-workflow-console"
            )["targets"] = ["workspace"]
            _, actions = build_local_skill_state(ROOT, workspace_only, Path(temp))
            self.assertNotIn(
                "multica-workflow-console",
                {
                    item["skill"]
                    for item in actions
                    if item.get("type") != "REMOVE_RETIRED_LOCAL_SKILL"
                },
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
            results = install_skills(ROOT, target)
            self.assertFalse(retired.exists())
            self.assertIn(
                {
                    "skill": "multica-workflow-observer",
                    "action": "REMOVE_RETIRED_LOCAL_SKILL",
                    "path": str(retired.resolve()),
                    "digest": None,
                },
                results,
            )

            foreign = target / "multica-workflow-maintainer"
            foreign.mkdir()
            (foreign / "SKILL.md").write_text(
                "---\nname: multica-workflow-maintainer\n---\n",
                encoding="utf-8",
            )
            results = install_skills(ROOT, target, True)
            self.assertTrue(foreign.exists())
            self.assertIn(
                "PRESERVE_FOREIGN_LOCAL_SKILL",
                {item["action"] for item in results},
            )

    def test_local_skill_plan_updates_owned_copy_and_blocks_foreign_target(self):
        with committed_temp_repo() as root:
            manifest, _ = validate_repository(root, "quality")
            target = root.parent / "local-root"
            install_skills(root, target)
            managed = target / "multica-workflow-manager"
            (managed / "SKILL.md").write_text(
                (managed / "SKILL.md").read_text(encoding="utf-8") + "\nstale\n",
                encoding="utf-8",
            )
            foreign = target / "multica-workflow-console"
            shutil.rmtree(foreign)
            foreign.mkdir()
            (foreign / "SKILL.md").write_text(
                "---\nname: multica-workflow-console\n---\n",
                encoding="utf-8",
            )
            _, actions = build_local_skill_state(root, manifest, target)
            by_skill = {item["skill"]: item for item in actions}
            self.assertEqual(
                by_skill["multica-workflow-manager"]["type"], "UPDATE_LOCAL_SKILL"
            )
            self.assertEqual(
                by_skill["multica-workflow-console"]["type"], "BLOCKED"
            )
            self.assertEqual(
                by_skill["multica-workflow-console"]["current_type"], "foreign"
            )
            with self.assertRaisesRegex(WorkflowError, "blocked"):
                install_skills(root, target, True)
            self.assertEqual(
                (foreign / "SKILL.md").read_text(encoding="utf-8"),
                "---\nname: multica-workflow-console\n---\n",
            )

    def test_owned_symlink_is_planned_and_migrated_to_copy(self):
        with committed_temp_repo() as root:
            manifest, _ = validate_repository(root, "quality")
            target = root.parent / "local-root"
            target.mkdir()
            source = root / "skills/multica-workflow-manager"
            destination = target / "multica-workflow-manager"
            shutil.copytree(source, destination)

            real_is_symlink = Path.is_symlink

            def classify_destination_as_symlink(path):
                if (
                    path.name == "multica-workflow-manager"
                    and path.parent.resolve() == target.resolve()
                ):
                    return True
                return real_is_symlink(path)

            with patch.object(
                Path, "is_symlink", autospec=True, side_effect=classify_destination_as_symlink
            ):
                _, actions = build_local_skill_state(root, manifest, target)
            action = next(
                item
                for item in actions
                if item.get("skill") == "multica-workflow-manager"
            )
            self.assertEqual(action["type"], "MIGRATE_LOCAL_SKILL_LINK_TO_COPY")
            observed = {
                "name": action["skill"],
                "destination": action["destination"],
                "current_type": "symlink",
                "target_type": "symlink",
                "owned": True,
                "digest": action["current_digest"],
            }
            with patch("workflow_lib.observe_local_skill", return_value=observed):
                apply_local_skill_action(root, manifest, action)
            self.assertTrue(destination.is_dir())
            self.assertFalse(destination.is_symlink())

    def test_junction_classification_is_deterministic_without_creating_one(self):
        with committed_temp_repo() as root:
            manifest, _ = validate_repository(root, "quality")
            target = root.parent / "local-root"
            target.mkdir()
            destination = target / "multica-workflow-manager"
            shutil.copytree(root / "skills/multica-workflow-manager", destination)

            def fake_junction(path):
                return path.name == "multica-workflow-manager"

            with patch("workflow_lib._path_is_junction", side_effect=fake_junction):
                _, actions = build_local_skill_state(root, manifest, target)
            action = next(
                item
                for item in actions
                if item.get("skill") == "multica-workflow-manager"
            )
            self.assertEqual(action["current_type"], "junction")
            self.assertEqual(action["type"], "MIGRATE_LOCAL_SKILL_LINK_TO_COPY")

    def test_changed_local_target_invalidates_approved_plan(self):
        with committed_temp_repo() as root:
            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            plan = build_plan(
                root, cli, workspace, "quality", runtime_map_for(root), False, False
            )
            plan_path = save_plan(root, plan)
            local_root = Path(plan["local_skills"]["root"])
            foreign = local_root / "multica-workflow-manager"
            foreign.mkdir(parents=True)
            (foreign / "SKILL.md").write_text(
                "---\nname: multica-workflow-manager\n---\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(WorkflowError, "Local Skill state changed"):
                apply_plan(root, cli, plan_path, plan["plan_digest"][:12])

    def test_staged_copy_failure_restores_owned_destination(self):
        with committed_temp_repo() as root:
            manifest, _ = validate_repository(root, "quality")
            target = root.parent / "local-root"
            install_skills(root, target)
            destination = target / "multica-workflow-manager"
            original = (destination / "SKILL.md").read_bytes()
            source = root / "skills/multica-workflow-manager"
            desired_digest = next(
                item["digest"]
                for item in build_local_skill_state(root, manifest, target)[0]["desired"]
                if item["name"] == "multica-workflow-manager"
            )
            real_hash = workflow_lib._skill_directory_hash

            def fail_installed_hash(path):
                if path == destination:
                    raise WorkflowError("simulated installed verification failure")
                return real_hash(path)

            with patch("workflow_lib._skill_directory_hash", side_effect=fail_installed_hash):
                with self.assertRaisesRegex(WorkflowError, "simulated"):
                    _replace_local_skill_copy(
                        source, destination, target.resolve(), desired_digest
                    )
            self.assertEqual((destination / "SKILL.md").read_bytes(), original)

    def test_apply_resumes_local_phase_from_partial_journal(self):
        with committed_temp_repo() as root:
            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            plan = build_plan(
                root, cli, workspace, "quality", runtime_map_for(root), False, False
            )
            plan_path = save_plan(root, plan)
            real_apply = workflow_lib.apply_local_skill_action
            calls = 0

            def fail_second(source_root, manifest, action):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("simulated local install failure")
                return real_apply(source_root, manifest, action)

            with patch("workflow_lib.apply_local_skill_action", side_effect=fail_second):
                with self.assertRaisesRegex(RuntimeError, "simulated"):
                    apply_plan(root, cli, plan_path, plan["plan_digest"][:12])
            journal_path = root / f".multica/journals/{plan['plan_digest'][:12]}.json"
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
            self.assertTrue(journal.get("workspace_completed_at"))
            self.assertFalse(journal.get("finished_at"))
            calls_before = len(cli.calls)
            resumed = apply_plan(root, cli, plan_path, plan["plan_digest"][:12])
            self.assertTrue(resumed.get("finished_at"))
            mutation_prefixes = {
                ("agent", "create"),
                ("agent", "update"),
                ("agent", "archive"),
                ("skill", "import"),
                ("skill", "delete"),
                ("project", "create"),
                ("project", "update"),
                ("squad", "create"),
                ("squad", "update"),
            }
            self.assertFalse(
                any(
                    call[:2] in mutation_prefixes
                    for call in cli.calls[calls_before:]
                )
            )
            self.assertEqual(len(resumed["local_skills"]), 5)

    def test_local_skill_root_defaults_and_rejects_dangerous_targets(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            self.assertEqual(
                default_local_skill_root(home),
                (home / ".agents/skills").resolve(),
            )
        with self.assertRaisesRegex(WorkflowError, "unsafe Local Skill root"):
            resolve_local_skill_root(Path(Path.cwd().anchor))

    def test_retirement_recovers_with_a_fresh_plan_after_partial_failure(self):
        with committed_temp_repo() as root:
            cli = FakeCLI()
            workspace = {"id": cli.workspace_id, "name": "Test", "slug": "test"}
            runtime_map = runtime_map_for(root)
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
