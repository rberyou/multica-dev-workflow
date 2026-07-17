from argparse import Namespace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "skills/multica-workflow-observer/scripts/observer.py"
SPEC = importlib.util.spec_from_file_location("workflow_observer", MODULE_PATH)
observer = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = observer
SPEC.loader.exec_module(observer)


class AuditCLI:
    def __init__(self, metadata, comments=None):
        self.metadata = metadata
        self.comments = comments or []

    def json(self, args, input_text=None):
        if args[:3] == ["issue", "metadata", "list"]:
            return self.metadata
        if args[:3] == ["issue", "comment", "list"]:
            return self.comments
        raise AssertionError(args)


class MaintenanceApprovalCLI(AuditCLI):
    def __init__(self, metadata, comments=None, issues=None, metadata_by_issue=None):
        super().__init__(metadata, comments)
        self.issues = issues or {}
        self.metadata_by_issue = metadata_by_issue or {}
        self.comments_by_issue = comments or {}

    def json(self, args, input_text=None):
        if args[:2] == ["issue", "get"]:
            return self.issues[args[2]]
        if args[:3] == ["issue", "metadata", "list"]:
            return self.metadata_by_issue.get(args[3], {})
        if args[:3] == ["issue", "comment", "list"]:
            return self.comments_by_issue.get(args[3], [])
        raise AssertionError(args)


class MaintenanceAuditCLI(AuditCLI):
    def __init__(self, metadata, comments=None):
        super().__init__(metadata, comments)
        self.agents = [
            {
                "id": "agent-maintainer",
                "instructions": observer_marker("agent.workflow-maintainer"),
            },
            {
                "id": "agent-reviewer",
                "instructions": observer_marker(
                    "agent.workflow-maintenance-reviewer"
                ),
            },
        ]

    def json(self, args, input_text=None):
        if args[:2] == ["agent", "list"]:
            return self.agents
        if args[:2] == ["agent", "get"]:
            return next(item for item in self.agents if item["id"] == args[2])
        return super().json(args, input_text)


class IncidentCLI:
    def __init__(self):
        self.metadata = {"T-100": {"protocol_revision": "v3"}}
        self.comments = []
        self.created = []
        self.incidents = []
        self.fail_incident_metadata_once = False
        self.fail_metadata_keys = set()
        self.issue_details = {}
        self.updates = []
        self.subscribers = []

    def json(self, args, input_text=None):
        if args[:2] == ["project", "list"]:
            return [
                {
                    "id": "project-ops",
                    "title": "工作流运维",
                    "description": observer_marker("project.workflow-operations"),
                }
            ]
        if args[:2] == ["agent", "list"]:
            return [
                {
                    "id": "agent-observer",
                    "name": "工作流观察员",
                    "instructions": observer_marker("agent.workflow-observer"),
                }
            ]
        if args[:2] == ["squad", "list"]:
            return [
                {
                    "id": "squad-dev",
                    "name": "开发交付小队",
                    "instructions": observer_marker("squad.development-delivery"),
                }
            ]
        if args[:3] == ["squad", "member", "list"]:
            return [{"member_type": "member", "member_id": "human-1", "role": "人工审批人"}]
        if args[:2] == ["issue", "get"]:
            return self.issue_details.get(
                args[2], {"id": args[2], "identifier": args[2], "status": "in_progress"}
            )
        if args[:3] == ["issue", "metadata", "list"]:
            return self.metadata.get(args[3], {})
        if args[:3] == ["issue", "metadata", "set"]:
            issue_id = args[3]
            key = args[args.index("--key") + 1]
            if key in self.fail_metadata_keys:
                self.fail_metadata_keys.remove(key)
                raise observer.ObserverError(f"injected metadata failure for {key}")
            if self.fail_incident_metadata_once and issue_id == "T-900":
                self.fail_incident_metadata_once = False
                raise observer.ObserverError("injected Incident metadata failure")
            value = args[args.index("--value") + 1]
            kind = args[args.index("--type") + 1]
            if kind == "bool":
                value = value == "true"
            elif kind == "number":
                value = int(value)
            self.metadata.setdefault(issue_id, {})[key] = value
            return {"key": key, "value": value}
        if args[:2] == ["issue", "list"]:
            limit = int(args[args.index("--limit") + 1])
            offset = int(args[args.index("--offset") + 1])
            items = self.incidents
            if "--metadata" in args:
                filters = [
                    args[index + 1]
                    for index, item in enumerate(args)
                    if item == "--metadata"
                ]
                items = [
                    incident
                    for incident in items
                    if all(
                        str(self.metadata.get(incident.get("identifier"), {}).get(item.split("=", 1)[0]))
                        == item.split("=", 1)[1]
                        for item in filters
                    )
                ]
            return items[offset : offset + limit]
        if args[:2] == ["issue", "create"]:
            item = {
                "id": "incident-1",
                "identifier": "T-900",
                "status": "todo",
                "title": args[args.index("--title") + 1],
            }
            self.created.append({"args": args, "description": input_text})
            self.metadata["T-900"] = {}
            self.incidents.append(item)
            return item
        if args[:3] == ["issue", "comment", "add"]:
            self.comments.append((args[3], input_text))
            return {"id": f"comment-{len(self.comments)}"}
        if args[:3] == ["issue", "comment", "list"]:
            return []
        if args[:3] == ["issue", "subscriber", "add"]:
            self.subscribers.append(args)
            return {"ok": True}
        if args[:2] == ["issue", "update"]:
            self.updates.append(args)
            result = {"id": args[2]}
            if "--status" in args:
                result["status"] = args[args.index("--status") + 1]
            if "--priority" in args:
                result["priority"] = args[args.index("--priority") + 1]
            return result
        raise AssertionError(args)


class ControlPlaneCLI:
    def __init__(self):
        manifest = json.loads((ROOT / "workflow.json").read_text(encoding="utf-8"))
        contract = observer.control_contract()
        quality = contract["profiles"]["quality"]
        self.workspace_id = "workspace-test"
        self.runtimes = [
            {"id": "runtime-codex", "provider": "codex", "status": "online"},
            {"id": "runtime-opencode", "provider": "opencode", "status": "online"},
        ]
        self.agents = []
        self.agent_by_key = {}
        for index, spec in enumerate(manifest["agents"]):
            body = "\n\n".join(
                (ROOT / path).read_text(encoding="utf-8").strip()
                for path in spec["instruction_files"]
            ).strip() + "\n"
            binding = quality[spec["runtime_binding"]]
            agent = {
                "id": f"agent-{index}",
                "name": spec["name"],
                "description": spec["description"],
                "instructions": observer_marker(
                    f"agent.{spec['key']}",
                    body,
                    contract["agents"][spec["key"]]["spec_hashes"]["quality"],
                ),
                "runtime_id": f"runtime-{binding['provider']}",
                "model": binding["model"],
                "thinking_level": binding["thinking_level"],
                "max_concurrent_tasks": spec["max_concurrent_tasks"],
                "permission_mode": spec["permission_mode"],
            }
            self.agents.append(agent)
            self.agent_by_key[spec["key"]] = agent
        self.skills = []
        self.skill_details = {}
        for index, (name, spec) in enumerate(contract["skills"].items()):
            skill = {"id": f"skill-{index}", "name": name}
            package_hash = spec["package_hash"] or "observer-self-hash"
            detail = {
                **skill,
                "content": (
                    f"name: {name}\nmetadata:\n"
                    f"  managed_by: {observer.MANAGED_BY}\n"
                    f"  workflow_id: {observer.WORKFLOW_ID}\n"
                    f"  version: {observer.skill_version()}\n"
                    f"  package_hash: {package_hash}\n"
                ),
            }
            self.skills.append(skill)
            self.skill_details[skill["id"]] = detail
        skill_by_name = {item["name"]: item for item in self.skills}
        self.agent_skills = {
            self.agent_by_key[key]["id"]: [
                self.skill_details[skill_by_name[name]["id"]]
                for name, spec in contract["skills"].items()
                if key in spec["attach_to"]
            ]
            for key in self.agent_by_key
        }
        squad_spec = manifest["squad"]
        squad_body = "\n\n".join(
            (ROOT / path).read_text(encoding="utf-8").strip()
            for path in squad_spec["instruction_files"]
        ).strip() + "\n"
        self.squads = [
            {
                "id": "squad-1",
                "name": squad_spec["name"],
                "description": squad_spec["description"],
                "instructions": observer_marker(
                    observer.SQUAD_KEY, squad_body, contract["squad"]["spec_hash"]
                ),
                "leader_id": self.agent_by_key[squad_spec["leader"]]["id"],
            }
        ]
        self.members = [
            {
                "member_type": "agent",
                "member_id": self.agent_by_key[item["agent"]]["id"],
                "role": item["role"],
            }
            for item in squad_spec["agent_members"]
        ] + [{"member_type": "member", "member_id": "human-1", "role": "人工审批人"}]
        project_spec = manifest["projects"][0]
        self.projects = [
            {
                "id": "project-1",
                "title": project_spec["title"],
                "description": observer_marker(
                    observer.OPERATIONS_PROJECT_KEY,
                    project_spec["description"].strip() + "\n",
                    contract["projects"][project_spec["key"]]["spec_hash"],
                ),
                "lead_id": self.agent_by_key[project_spec["lead"]]["id"],
                "status": project_spec["status"],
                "icon": project_spec.get("icon", ""),
            }
        ]
        autopilot_spec = manifest["autopilots"][0]
        autopilot_hash = observer.sha256_value(
            {
                "key": autopilot_spec["key"],
                "title": autopilot_spec["title"],
                "description": autopilot_spec["description"].strip() + "\n",
                "agent": autopilot_spec["agent"],
                "mode": autopilot_spec["mode"],
                "project": autopilot_spec["project"],
                "status": autopilot_spec["status"],
                "issue_title_template": autopilot_spec.get("issue_title_template", ""),
                "subscriber_ids": ["human-1"],
            }
        )
        self.autopilots = [
            {
                "id": "autopilot-1",
                "title": autopilot_spec["title"],
                "description": observer_marker(
                    "autopilot.workflow-health-audit",
                    autopilot_spec["description"].strip() + "\n",
                    autopilot_hash,
                ),
                "agent_id": self.agent_by_key[autopilot_spec["agent"]]["id"],
                "mode": autopilot_spec["mode"],
                "project_id": "project-1",
                "status": autopilot_spec["status"],
                "issue_title_template": autopilot_spec.get("issue_title_template", ""),
                "subscribers": [{"user_id": "human-1", "user_type": "member"}],
                "triggers": [
                    {
                        "id": "trigger-1",
                        **{
                            key: autopilot_spec["triggers"][0][key]
                            for key in ["kind", "label", "enabled", "cron", "timezone"]
                        },
                    }
                ],
            }
        ]

    def json(self, args, input_text=None):
        if args[:2] == ["agent", "get"]:
            return next(item for item in self.agents if item["id"] == args[2])
        if args[:2] == ["agent", "list"]:
            return self.agents
        if args[:2] == ["squad", "get"]:
            return next(item for item in self.squads if item["id"] == args[2])
        if args[:2] == ["squad", "list"]:
            return self.squads
        if args[:2] == ["project", "get"]:
            return next(item for item in self.projects if item["id"] == args[2])
        if args[:2] == ["project", "list"]:
            return self.projects
        if args[:2] == ["autopilot", "list"]:
            return self.autopilots
        if args[:2] == ["autopilot", "get"]:
            item = next(item for item in self.autopilots if item["id"] == args[2])
            return {
                "autopilot": {
                    **{
                        key: value
                        for key, value in item.items()
                        if key not in {"agent_id", "mode", "triggers"}
                    },
                    "assignee_id": item["agent_id"],
                    "execution_mode": item["mode"],
                },
                "triggers": item["triggers"],
            }
        if args[:2] == ["skill", "list"]:
            return self.skills
        if args[:2] == ["skill", "get"]:
            return self.skill_details[args[2]]
        if args[:2] == ["runtime", "list"]:
            return self.runtimes
        if args[:3] == ["agent", "skills", "list"]:
            return self.agent_skills[args[3]]
        if args[:3] == ["squad", "member", "list"]:
            return self.members
        raise AssertionError(args)


class ParentCLI:
    def json(self, args, input_text=None):
        if args[:2] == ["issue", "children"]:
            return [{"identifier": "T-2", "status": "in_progress"}]
        raise AssertionError(args)


class HealthAuditCLI:
    def json(self, args, input_text=None):
        if args[:2] == ["issue", "list"]:
            return []
        if args[:2] == ["autopilot", "list"]:
            return [
                {
                    "id": "autopilot-1",
                    "description": observer_marker("autopilot.workflow-health-audit"),
                }
            ]
        if args[:2] == ["autopilot", "get"]:
            return {
                "id": "autopilot-1",
                "description": observer_marker(
                    "autopilot.workflow-health-audit"
                ),
            }
        if args[:2] == ["autopilot", "runs"]:
            return []
        raise AssertionError(args)


class WorkflowListCLI:
    def __init__(self):
        self.issues = [
            {
                "identifier": "T-active",
                "status": "in_progress",
                "parent_issue_id": "parent-id",
                "metadata": {"workflow_id": observer.WORKFLOW_ID},
            },
            {
                "identifier": "T-done",
                "status": "done",
                "metadata": {"workflow_id": observer.WORKFLOW_ID},
            },
            {
                "identifier": "T-pending",
                "status": "done",
                "metadata": {
                    "workflow_id": observer.WORKFLOW_ID,
                    "workflow_incident_pending": True,
                },
            },
            {
                "identifier": "T-payload-only",
                "status": "done",
                "metadata": {
                    "workflow_incident_pending_index": observer.WORKFLOW_ID,
                },
            },
        ]

    def json(self, args, input_text=None):
        if args[:2] == ["issue", "get"]:
            return {
                "id": "parent-id",
                "identifier": "T-parent",
                "status": "done",
                "parent_issue_id": None,
                "metadata": {"workflow_id": observer.WORKFLOW_ID},
            }
        if args[:2] != ["issue", "list"]:
            raise AssertionError(args)
        items = list(self.issues)
        if "--status" in args:
            status = args[args.index("--status") + 1]
            items = [item for item in items if item["status"] == status]
        filters = [args[index + 1] for index, item in enumerate(args) if item == "--metadata"]
        for item in filters:
            key, value = item.split("=", 1)
            parsed = value == "true" if value in {"true", "false"} else value
            items = [entry for entry in items if entry["metadata"].get(key) == parsed]
        limit = int(args[args.index("--limit") + 1])
        offset = int(args[args.index("--offset") + 1])
        return items[offset : offset + limit]


def observer_marker(object_key, body="body\n", spec_hash="test"):
    return (
        "<!-- multica-workflow\n"
        "managed_by=multica-dev-workflow\n"
        "workflow_id=development-delivery\n"
        f"object_key={object_key}\n"
        f"spec_hash={spec_hash}\n"
        f"-->\n{body}"
    )


class ObserverTests(unittest.TestCase):
    def test_redact_nested_secrets_and_credentials(self):
        value = {
            "token": "secret",
            "nested": {"authorization": "Bearer abc.def", "safe": "Basic Zm9vOmJhcg=="},
            "key": "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
        }
        result = observer.redact(value)
        self.assertEqual(result["token"], "<redacted>")
        self.assertEqual(result["nested"]["authorization"], "<redacted>")
        self.assertEqual(result["nested"]["safe"], "<redacted-credential>")
        self.assertIn("redacted-private-key", result["key"])

    def test_redact_mixed_headers_cookies_and_environment_assignments(self):
        text = (
            "Authorization: Token abc123\n"
            "Cookie: session=top-secret\n"
            "OPENAI_API_KEY=sk-secret PASSWORD='plain-secret' safe=value"
        )
        result = observer.redact(text)
        self.assertNotIn("abc123", result)
        self.assertNotIn("top-secret", result)
        self.assertNotIn("sk-secret", result)
        self.assertNotIn("plain-secret", result)
        self.assertIn("safe=value", result)

    def test_audit_issue_emits_deterministic_findings(self):
        cli = AuditCLI(
            {
                "blocked_reason": "",
                "waiting_on": "",
                "review_commit_sha": "old",
                "pr_head_sha": "new",
                "original_owner_id": "agent-1",
                "reviewer_id": "agent-1",
                "implementation_started": True,
                "plan_approved": False,
            }
        )
        findings = observer.audit_issue(cli, {"id": "T-1", "status": "blocked"}, 24)
        self.assertEqual(
            {item["rule_id"] for item in findings},
            {"WF-BLOCKED-001", "WF-REVIEW-001", "WF-REVIEW-002", "WF-PLAN-001"},
        )

    def test_audit_health_error_is_a_failed_run(self):
        self.assertTrue(observer.audit_failed({"coverage_complete": True, "health_error": "run history unavailable"}))
        self.assertFalse(observer.audit_failed({"coverage_complete": True, "health_error": None}))

    def test_audit_and_health_accept_json_output(self):
        self.assertEqual(observer.parser().parse_args(["audit", "--output", "json"]).output, "json")
        self.assertEqual(observer.parser().parse_args(["health", "--output", "json"]).output, "json")

    def test_scheduled_scan_includes_only_active_or_pending_workflow_issues(self):
        issues, complete = observer.list_workflow_issues(WorkflowListCLI(), 100)
        self.assertTrue(complete)
        self.assertEqual(
            {item["identifier"] for item in issues},
            {"T-active", "T-pending", "T-payload-only", "T-parent"},
        )

    def test_observer_cli_discovery_includes_standard_macos_and_linux_paths(self):
        mac = {
            path.as_posix()
            for path in observer.multica_candidates(
                home=Path("/Users/tester"), system="darwin"
            )
        }
        self.assertIn(
            "/Users/tester/Library/Application Support/Multica/bin/multica", mac
        )
        self.assertIn("/opt/homebrew/bin/multica", mac)
        linux = {
            path.as_posix()
            for path in observer.multica_candidates(
                home=Path("/home/tester"), system="linux"
            )
        }
        self.assertIn("/opt/Multica/resources/app.asar.unpacked/resources/bin/multica", linux)
        self.assertIn(
            "/usr/lib/multica/resources/app.asar.unpacked/resources/bin/multica", linux
        )

    def test_non_approver_approval_comment_is_detected(self):
        cli = AuditCLI(
            {"human_approver_id": "human-1"},
            comments=[
                {
                    "id": "comment-1",
                    "author_type": "agent",
                    "author_id": "agent-1",
                    "content": "APPROVE PLAN v3",
                }
            ],
        )
        findings = observer.audit_issue(cli, {"identifier": "T-1", "status": "in_review"}, 24)
        self.assertIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

    def test_stale_plan_approval_comment_is_detected(self):
        cli = AuditCLI(
            {
                "human_approver_id": "human-1",
                "plan_approved": True,
                "approved_plan_revision": "v2",
                "plan_revision": "v2",
                "approval_comment_id": "comment-v1",
            },
            comments=[
                {
                    "id": "comment-v1",
                    "author_type": "member",
                    "author_id": "human-1",
                    "content": "APPROVE PLAN v1",
                }
            ],
        )
        findings = observer.audit_issue(cli, {"identifier": "T-stale", "status": "in_review"}, 24)
        self.assertIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

    def test_recorded_approval_requires_durable_author_metadata_and_current_revision(self):
        base = {
            "human_approver_id": "human-1",
            "plan_approved": True,
            "plan_revision": "v3",
            "approved_plan_revision": "v3",
            "approval_author_type": "member",
            "approval_author_id": "human-1",
            "approval_comment_id": "comment-v3",
        }
        comment = {
            "id": "comment-v3",
            "author_type": "member",
            "author_id": "human-1",
            "content": "APPROVE PLAN v3",
        }
        cases = [
            ({**base, "approval_author_id": ""}, "approval_author_id"),
            ({**base, "approval_author_type": "agent"}, "approval_author_type"),
            ({**base, "approved_plan_revision": "v2"}, "approved_plan_revision"),
        ]
        for metadata, label in cases:
            with self.subTest(case=label):
                findings = observer.audit_issue(
                    AuditCLI(metadata, [comment]),
                    {"identifier": "T-approval", "status": "in_review"},
                    24,
                )
                self.assertIn(
                    "WF-APPROVAL-001", {item["rule_id"] for item in findings}
                )

    def test_workflow_maintenance_plan_approval_requires_workflow_phrase(self):
        metadata = {
            "workflow_id": observer.WORKFLOW_ID,
            "workflow_object_type": "maintenance_change",
            "human_approver_id": "human-1",
            "plan_approved": True,
            "plan_revision": "v1",
            "approved_plan_revision": "v1",
            "approval_author_type": "member",
            "approval_author_id": "human-1",
            "approval_comment_id": "approval-1",
        }
        valid_comment = {
            "id": "approval-1",
            "author_type": "member",
            "author_id": "human-1",
            "content": "APPROVE WORKFLOW PLAN v1",
        }
        findings = observer.approval_findings(
            AuditCLI(metadata, [valid_comment]),
            {"identifier": "WOR-10", "status": "in_progress"},
            metadata,
        )
        self.assertNotIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

        invalid_comment = {**valid_comment, "content": "APPROVE PLAN v1"}
        findings = observer.approval_findings(
            AuditCLI(metadata, [invalid_comment]),
            {"identifier": "WOR-10", "status": "in_progress"},
            metadata,
        )
        self.assertIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

    def test_maintenance_child_reads_only_associated_change_approval(self):
        child_metadata = {
            "workflow_id": observer.WORKFLOW_ID,
            "workflow_object_type": "maintenance_implementation",
            "maintenance_change_id": "WOR-10",
            "source_incident_id": "WOR-9",
            "human_approver_id": "human-1",
            "plan_approved": True,
            "plan_revision": "v1",
            "approved_plan_revision": "v1",
            "approval_author_type": "member",
            "approval_author_id": "human-1",
            "approval_comment_id": "approval-1",
        }
        parent_metadata = {
            "workflow_id": observer.WORKFLOW_ID,
            "workflow_object_type": "maintenance_change",
            "source_incident_id": "WOR-9",
        }
        approval = {
            "id": "approval-1",
            "author_type": "member",
            "author_id": "human-1",
            "content": "APPROVE WORKFLOW PLAN v1",
        }
        cli = MaintenanceApprovalCLI(
            child_metadata,
            comments={"WOR-14": [], "WOR-10": [approval], "WOR-99": [approval]},
            issues={"WOR-10": {"identifier": "WOR-10", "status": "in_progress"}},
            metadata_by_issue={"WOR-10": parent_metadata},
        )
        findings = observer.approval_findings(
            cli,
            {"identifier": "WOR-14", "status": "in_progress"},
            child_metadata,
        )
        self.assertNotIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

        cli.metadata_by_issue["WOR-10"]["source_incident_id"] = "WOR-other"
        findings = observer.approval_findings(
            cli,
            {"identifier": "WOR-14", "status": "in_progress"},
            child_metadata,
        )
        self.assertIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

        cli.metadata_by_issue["WOR-10"]["source_incident_id"] = "WOR-9"
        cli.comments_by_issue["WOR-10"] = [
            {**approval, "author_type": "agent", "author_id": "agent-1"}
        ]
        findings = observer.approval_findings(
            cli,
            {"identifier": "WOR-14", "status": "in_progress"},
            child_metadata,
        )
        self.assertIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

        cli.comments_by_issue["WOR-10"] = []
        findings = observer.approval_findings(
            cli,
            {"identifier": "WOR-14", "status": "in_progress"},
            child_metadata,
        )
        self.assertIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

    def test_maintenance_child_can_resolve_bounded_parent_chain(self):
        child_metadata = {
            "workflow_id": observer.WORKFLOW_ID,
            "workflow_object_type": "maintenance_implementation",
            "source_incident_id": "WOR-15",
            "human_approver_id": "human-1",
            "plan_approved": True,
            "plan_revision": "v1",
            "approved_plan_revision": "v1",
            "approval_author_type": "member",
            "approval_author_id": "human-1",
            "approval_comment_id": "approval-1",
        }
        approval = {
            "id": "approval-1",
            "author_type": "member",
            "author_id": "human-1",
            "content": "APPROVE WORKFLOW PLAN v1",
        }
        cli = MaintenanceApprovalCLI(
            child_metadata,
            comments={"WOR-24": [], "WOR-25": [], "WOR-21": [approval]},
            issues={
                "plan-id": {
                    "identifier": "WOR-25",
                    "status": "done",
                    "parent_issue_id": "change-id",
                },
                "change-id": {
                    "identifier": "WOR-21",
                    "status": "in_progress",
                },
            },
            metadata_by_issue={
                "WOR-25": {
                    "workflow_id": observer.WORKFLOW_ID,
                    "workflow_object_type": "change_plan",
                    "source_incident_id": "WOR-15",
                },
                "WOR-21": {
                    "workflow_id": observer.WORKFLOW_ID,
                    "workflow_object_type": "maintenance_change",
                    "source_incident_id": "WOR-15",
                },
            },
        )
        findings = observer.approval_findings(
            cli,
            {
                "identifier": "WOR-24",
                "status": "in_progress",
                "parent_issue_id": "plan-id",
            },
            child_metadata,
        )
        self.assertNotIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

    def test_ordinary_plan_approval_keeps_existing_phrase(self):
        metadata = {
            "human_approver_id": "human-1",
            "plan_approved": True,
            "plan_revision": "v1",
            "approved_plan_revision": "v1",
            "approval_author_type": "member",
            "approval_author_id": "human-1",
            "approval_comment_id": "approval-1",
        }
        comment = {
            "id": "approval-1",
            "author_type": "member",
            "author_id": "human-1",
            "content": "APPROVE PLAN v1",
        }
        findings = observer.approval_findings(
            AuditCLI(metadata, [comment]),
            {"identifier": "T-ordinary", "status": "in_progress"},
            metadata,
        )
        self.assertNotIn("WF-APPROVAL-001", {item["rule_id"] for item in findings})

    def test_review_identity_contract_is_scoped_to_maintenance_objects(self):
        maintenance = {
            "workflow_id": observer.WORKFLOW_ID,
            "workflow_version": "1.1.0-rc.3",
            "protocol_revision": "v3",
            "top_protocol_revision": "v3",
            "human_approver_id": "human-1",
            "workflow_object_type": "change_plan",
            "workflow_stage": "change_plan",
            "plan_revision": "v1",
            "maintainer_id": "agent-maintainer",
            "maintenance_reviewer_id": "agent-reviewer",
        }
        issue = {"identifier": "WOR-32", "status": "in_review"}
        findings = observer.audit_issue(AuditCLI(maintenance), issue, 24)
        self.assertNotIn("WF-REVIEW-002", {item["rule_id"] for item in findings})

        cases = [
            ({key: value for key, value in maintenance.items() if key != "maintainer_id"}, "maintainer_id"),
            (
                {
                    **maintenance,
                    "maintenance_reviewer_id": "agent-maintainer",
                },
                "maintenance_reviewer_id equals maintainer_id",
            ),
        ]
        for case_metadata, expected in cases:
            with self.subTest(expected=expected):
                findings = observer.audit_issue(AuditCLI(case_metadata), issue, 24)
                review_findings = [
                    item for item in findings if item["rule_id"] == "WF-REVIEW-002"
                ]
                self.assertTrue(review_findings)
                self.assertTrue(any(expected in item["actual"] for item in review_findings))

        unknown = {
            **maintenance,
            "workflow_object_type": "unknown_review_object",
            "workflow_stage": "unknown_review_stage",
        }
        findings = observer.audit_issue(AuditCLI(unknown), issue, 24)
        self.assertIn("WF-REVIEW-002", {item["rule_id"] for item in findings})

    def test_maintenance_review_audit_binds_managed_identity_comment_and_sha(self):
        metadata = {
            "workflow_object_type": "maintenance_change",
            "maintainer_id": "agent-maintainer",
            "maintenance_reviewer_id": "agent-reviewer",
            "review_comment_id": "review-1",
            "plan_revision": "v6",
            "reviewed_commit_sha": "a" * 40,
            "pr_head_sha": "a" * 40,
        }
        comment = {
            "id": "review-1",
            "author_type": "agent",
            "author_id": "agent-reviewer",
            "content": f"APPROVED\nplan_revision=v6\nreviewed_commit_sha={'a' * 40}",
        }
        valid = observer.audit_issue(
            MaintenanceAuditCLI(metadata, [comment]),
            {"identifier": "T-maint", "status": "in_review"},
            24,
        )
        self.assertNotIn(
            "WF-MAINT-REVIEW-001", {item["rule_id"] for item in valid}
        )
        cases = [
            ({**metadata, "maintenance_reviewer_id": "agent-maintainer"}, comment),
            ({**metadata, "maintenance_reviewer_id": "agent-unknown"}, {**comment, "author_id": "agent-unknown"}),
            ({**metadata, "pr_head_sha": "b" * 40}, comment),
            ({key: value for key, value in metadata.items() if key != "review_comment_id"}, comment),
        ]
        for case_metadata, case_comment in cases:
            with self.subTest(metadata=case_metadata):
                findings = observer.audit_issue(
                    MaintenanceAuditCLI(case_metadata, [case_comment]),
                    {"identifier": "T-maint", "status": "in_review"},
                    24,
                )
                self.assertIn(
                    "WF-MAINT-REVIEW-001",
                    {item["rule_id"] for item in findings},
                )

    def test_missing_v3_gate_metadata_is_reported(self):
        findings = observer.audit_issue(
            AuditCLI(
                {
                    "workflow_id": "development-delivery",
                    "protocol_revision": "v3",
                    "workflow_stage": "development_task",
                }
            ),
            {"identifier": "T-20", "status": "in_review"},
            24,
        )
        rules = {item["rule_id"] for item in findings}
        self.assertTrue(
            {"WF-PROTOCOL-001", "WF-PLANREV-001", "WF-REVIEW-001", "WF-REVIEW-002"}
            <= rules
        )

    def test_explicit_child_protocol_conflict_is_reported(self):
        findings = observer.audit_issue(
            AuditCLI(
                {
                    "workflow_id": "development-delivery",
                    "workflow_version": "1.1.0-rc.1",
                    "protocol_revision": "v2",
                    "top_protocol_revision": "v3",
                    "human_approver_id": "human-1",
                    "workflow_stage": "development_task",
                    "plan_revision": "v3",
                    "dependencies_satisfied": True,
                }
            ),
            {"identifier": "T-21", "status": "todo"},
            24,
        )
        self.assertIn("WF-PROTOCOL-001", {item["rule_id"] for item in findings})

    def test_audit_inherits_protocol_from_live_parent_chain_when_child_metadata_is_missing(self):
        cli = IncidentCLI()
        child = {
            "id": "child-id",
            "identifier": "T-30",
            "status": "todo",
            "parent_issue_id": "parent-id",
        }
        cli.issue_details["parent-id"] = {
            "id": "parent-id",
            "identifier": "T-29",
            "status": "in_progress",
            "parent_issue_id": None,
        }
        cli.metadata["T-30"] = {
            "human_approver_id": "human-1",
            "workflow_stage": "development_task",
            "plan_revision": "v3",
        }
        cli.metadata["T-29"] = {
            "workflow_id": "development-delivery",
            "workflow_version": "1.1.0-rc.1",
            "protocol_revision": "v3",
            "top_protocol_revision": "v3",
            "human_approver_id": "human-1",
            "workflow_stage": "requirement",
        }
        findings = observer.audit_issue(cli, child, 24)
        self.assertNotIn("WF-PROTOCOL-001", {item["rule_id"] for item in findings})

    def test_health_rejects_completed_run_with_failed_conclusion(self):
        class FailedRunCLI:
            def json(self, args, input_text=None):
                if args[:2] == ["autopilot", "list"]:
                    return [
                        {
                            "id": "autopilot-1",
                            "description": observer_marker(
                                "autopilot.workflow-health-audit"
                            ),
                        }
                    ]
                if args[:2] == ["autopilot", "get"]:
                    return {
                        "id": "autopilot-1",
                        "description": observer_marker(
                            "autopilot.workflow-health-audit"
                        ),
                    }
                if args[:2] == ["autopilot", "runs"]:
                    return [
                        {
                            "id": "run-1",
                            "status": "completed",
                            "conclusion": "failure",
                            "finished_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ]
                raise AssertionError(args)

        with self.assertRaisesRegex(observer.ObserverError, "conclusion is failure"):
            observer.health(FailedRunCLI(), 135)

    def test_health_uses_autopilot_detail_marker_when_list_is_summary(self):
        class SummaryHealthCLI:
            def json(self, args, input_text=None):
                if args[:2] == ["autopilot", "list"]:
                    return [{"id": "autopilot-1", "title": "工作流健康巡检"}]
                if args[:2] == ["autopilot", "get"]:
                    return {
                        "id": "autopilot-1",
                        "title": "工作流健康巡检",
                        "description": observer_marker(
                            "autopilot.workflow-health-audit"
                        ),
                    }
                if args[:2] == ["autopilot", "runs"]:
                    return [
                        {
                            "id": "run-1",
                            "status": "completed",
                            "conclusion": "success",
                            "finished_at": datetime.now(timezone.utc).isoformat(),
                        }
                    ]
                raise AssertionError(args)

        result = observer.health(SummaryHealthCLI(), 135)
        self.assertEqual(result["autopilot_id"], "autopilot-1")

    def test_initial_rule_set_has_deterministic_fixtures(self):
        old = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
        fixtures = [
            ({"blocked_reason": "", "waiting_on": ""}, {"identifier": "T-1", "status": "blocked"}, "WF-BLOCKED-001"),
            ({"workflow_incident_pending": True}, {"identifier": "T-2", "status": "todo"}, "WF-INCIDENT-001"),
            ({"review_commit_sha": "old", "pr_head_sha": "new"}, {"identifier": "T-3", "status": "in_review"}, "WF-REVIEW-001"),
            ({"original_owner_id": "a", "reviewer_id": "a"}, {"identifier": "T-4", "status": "in_review"}, "WF-REVIEW-002"),
            ({"implementation_started": True, "plan_approved": False}, {"identifier": "T-5", "status": "todo"}, "WF-PLAN-001"),
            ({"dependencies_satisfied": False}, {"identifier": "T-6", "status": "in_progress"}, "WF-DEPENDENCY-001"),
            ({"approved_plan_revision": "v2", "plan_revision": "v1"}, {"identifier": "T-7", "status": "todo"}, "WF-PLANREV-001"),
            ({"dependencies_satisfied": True}, {"identifier": "T-8", "status": "backlog", "updated_at": old}, "WF-BACKLOG-001"),
            ({"runtime_failure_count": 2, "waiting_on": "maintainer"}, {"identifier": "T-9", "status": "blocked"}, "WF-RUNTIME-001"),
            ({"workflow_object_type": "maintenance_change", "incident_severity": "high"}, {"identifier": "T-10", "status": "todo", "updated_at": old}, "WF-MAINT-001"),
        ]
        observed = set()
        for metadata, issue, expected in fixtures:
            with self.subTest(rule=expected):
                findings = observer.audit_issue(AuditCLI(metadata), issue, 24)
                rules = {item["rule_id"] for item in findings}
                self.assertIn(expected, rules)
                observed.add(expected)
        parent = observer.parent_findings(ParentCLI(), {"identifier": "T-11", "status": "done"})
        self.assertEqual(parent[0]["rule_id"], "WF-PARENT-001")
        observed.add("WF-PARENT-001")
        control = ControlPlaneCLI()
        control.agents.pop()
        drift = observer.audit_control_plane(control, "T-12")
        self.assertEqual(drift[0]["rule_id"], "WF-DRIFT-001")
        observed.add("WF-DRIFT-001")
        health_result = observer.audit(
            HealthAuditCLI(),
            Namespace(
                max_issues=10,
                backlog_hours=24,
                scope="health",
                health_max_age_minutes=135,
                coverage_issue="T-13",
                report=False,
            ),
        )
        self.assertIn("WF-OBSERVER-001", {item["rule_id"] for item in health_result["findings"]})
        observed.add("WF-OBSERVER-001")
        approval = observer.audit_issue(
            AuditCLI(
                {"human_approver_id": "human-1"},
                comments=[
                    {
                        "id": "bad-approval",
                        "author_type": "agent",
                        "author_id": "agent-1",
                        "content": "APPROVE REQUIREMENT v3",
                    }
                ],
            ),
            {"identifier": "T-14", "status": "in_review"},
            24,
        )
        self.assertIn("WF-APPROVAL-001", {item["rule_id"] for item in approval})
        observed.add("WF-APPROVAL-001")
        self.assertEqual(
            observed,
            {
                "WF-APPROVAL-001",
                "WF-PLAN-001",
                "WF-REVIEW-001",
                "WF-REVIEW-002",
                "WF-DEPENDENCY-001",
                "WF-PARENT-001",
                "WF-BLOCKED-001",
                "WF-BACKLOG-001",
                "WF-PLANREV-001",
                "WF-INCIDENT-001",
                "WF-RUNTIME-001",
                "WF-DRIFT-001",
                "WF-OBSERVER-001",
                "WF-MAINT-001",
            },
        )

    def test_control_plane_audit_verifies_full_desired_state(self):
        cli = ControlPlaneCLI()
        self.assertEqual(observer.audit_control_plane(cli, "T-audit"), [])

    def test_control_plane_audit_accepts_schedule_cron_aliases(self):
        for alias in ["cron", "cron_expression", "schedule"]:
            with self.subTest(alias=alias):
                cli = ControlPlaneCLI()
                trigger = cli.autopilots[0]["triggers"][0]
                cron = trigger.pop("cron")
                trigger[alias] = cron
                self.assertEqual(observer.audit_control_plane(cli, "T-audit"), [])

    def test_control_plane_audit_still_reports_trigger_drift(self):
        cases = [
            "wrong cron_expression",
            "disabled trigger",
            "wrong timezone",
            "missing trigger",
            "duplicate label",
        ]
        for case in cases:
            with self.subTest(case=case):
                cli = ControlPlaneCLI()
                trigger = cli.autopilots[0]["triggers"][0]
                if case == "wrong cron_expression":
                    trigger.pop("cron")
                    trigger["cron_expression"] = "30 * * * *"
                elif case == "disabled trigger":
                    trigger["enabled"] = False
                elif case == "wrong timezone":
                    trigger["timezone"] = "UTC"
                elif case == "missing trigger":
                    cli.autopilots[0]["triggers"] = []
                elif case == "duplicate label":
                    cli.autopilots[0]["triggers"].append(
                        {**trigger, "id": "trigger-duplicate"}
                    )
                findings = observer.audit_control_plane(cli, "T-audit")
                self.assertEqual(findings[0]["rule_id"], "WF-DRIFT-001")
                self.assertIn("autopilot_specs", findings[0]["actual"])

    def test_control_plane_audit_accepts_reviewed_disabled_operations_mode(self):
        cli = ControlPlaneCLI()
        manifest = json.loads((ROOT / "workflow.json").read_text(encoding="utf-8"))
        operations = observer.control_contract()["operations"]
        autopilot_spec = manifest["autopilots"][0]
        cli.autopilots[0]["status"] = "paused"
        disabled_hash = observer.sha256_value(
            {
                "key": autopilot_spec["key"],
                "title": autopilot_spec["title"],
                "description": autopilot_spec["description"].strip() + "\n",
                "agent": autopilot_spec["agent"],
                "mode": autopilot_spec["mode"],
                "project": autopilot_spec["project"],
                "status": "paused",
                "issue_title_template": autopilot_spec.get(
                    "issue_title_template", ""
                ),
                "subscriber_ids": ["human-1"],
            }
        )
        cli.autopilots[0]["description"] = observer_marker(
            "autopilot.workflow-health-audit",
            autopilot_spec["description"].strip() + "\n",
            disabled_hash,
        )
        observer_skill = operations["observer_skill_name"]
        observer_agent = operations["observer_agent"]
        for key, agent in cli.agent_by_key.items():
            if key == observer_agent:
                continue
            cli.agent_skills[agent["id"]] = [
                skill
                for skill in cli.agent_skills[agent["id"]]
                if skill.get("name") != observer_skill
            ]
        self.assertEqual(observer.audit_control_plane(cli, "T-audit"), [])
        cli.agents[0]["description"] = "drifted instructions contract"
        cli.autopilots[0]["triggers"][0]["timezone"] = "UTC"
        findings = observer.audit_control_plane(cli, "T-audit")
        self.assertEqual(findings[0]["rule_id"], "WF-DRIFT-001")
        self.assertIn("agent_specs", findings[0]["actual"])
        self.assertIn("autopilot_specs", findings[0]["actual"])
        marker_only = ControlPlaneCLI()
        marker_only.agents[0]["instructions"] = marker_only.agents[0]["instructions"].replace(
            "spec_hash=", "spec_hash=stale-", 1
        )
        marker_findings = observer.audit_control_plane(marker_only, "T-audit")
        self.assertIn("marker_spec_hash", marker_findings[0]["actual"])
        with patch.object(observer, "portable_source_hash", return_value="tampered"):
            source_findings = observer.audit_control_plane(ControlPlaneCLI(), "T-audit")
        self.assertIn("observer_source_hash", source_findings[0]["actual"])

    def test_control_plane_audit_rejects_non_member_subscriber_shapes(self):
        for subscriber in [
            {"user_id": "human-1", "user_type": "agent"},
            {"user_id": "human-1", "user_type": "unknown"},
        ]:
            with self.subTest(subscriber=subscriber):
                cli = ControlPlaneCLI()
                cli.autopilots[0]["subscribers"] = [subscriber]
                findings = observer.audit_control_plane(cli, "T-audit")
                self.assertEqual(findings[0]["rule_id"], "WF-DRIFT-001")
                self.assertIn("autopilot_specs", findings[0]["actual"])

    def test_control_plane_audit_detects_unmarked_managed_name_collision(self):
        cli = ControlPlaneCLI()
        cli.projects.append(
            {
                "id": "project-unmarked",
                "title": cli.projects[0]["title"],
                "description": "user project",
            }
        )
        findings = observer.audit_control_plane(cli, "T-audit")
        self.assertEqual(findings[0]["rule_id"], "WF-DRIFT-001")
        self.assertIn("name_collisions", findings[0]["actual"])

    def test_control_plane_audit_uses_detail_markers_when_lists_are_summaries(self):
        class SummaryOnlyCLI(ControlPlaneCLI):
            def json(self, args, input_text=None):
                value = super().json(args, input_text)
                fields = {
                    ("agent", "list"): "instructions",
                    ("squad", "list"): "instructions",
                    ("project", "list"): "description",
                    ("autopilot", "list"): "description",
                }
                field = fields.get(tuple(args[:2]))
                if field:
                    return [
                        {key: child for key, child in item.items() if key != field}
                        for item in value
                    ]
                return value

        self.assertEqual(observer.audit_control_plane(SummaryOnlyCLI(), "T-audit"), [])

    def test_report_incident_creates_and_links_source(self):
        cli = IncidentCLI()
        args = Namespace(
            source_issue="T-100",
            source_requirement=None,
            rule_id="WF-REVIEW-001",
            severity="high",
            summary="stale review",
            expected="Review matches PR head",
            actual="Review is old",
            evidence="commit changed",
            entity=None,
            dedupe_key=None,
            protocol_revision=None,
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=True,
        )
        result = observer.report_incident(cli, args)
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["incident_id"], "T-900")
        self.assertEqual(cli.metadata["T-100"]["workflow_incident_id"], "T-900")
        self.assertEqual(cli.metadata["T-100"]["workflow_incident_pending"], False)
        self.assertEqual(
            cli.metadata["T-100"]["workflow_incident_pending_payload"], ""
        )
        self.assertEqual(cli.metadata["T-900"]["incident_rule_id"], "WF-REVIEW-001")
        self.assertEqual(cli.metadata["T-900"]["human_approver_id"], "human-1")
        self.assertTrue(cli.created)
        self.assertIn("stale review", cli.created[0]["description"])
        cli.metadata["T-100"].pop("workflow_incident_id")
        self.assertIsNone(
            observer.pending_recovery_args(
                {"identifier": "T-100"}, cli.metadata["T-100"], []
            )
        )

    def test_child_incident_inherits_top_level_protocol_and_requirement(self):
        cli = IncidentCLI()
        cli.issue_details["T-100"] = {
            "id": "child-id",
            "identifier": "T-100",
            "status": "in_progress",
            "parent_issue_id": "parent-id",
        }
        cli.issue_details["parent-id"] = {
            "id": "parent-id",
            "identifier": "T-50",
            "status": "in_progress",
            "parent_issue_id": None,
        }
        cli.metadata["T-100"] = {"top_protocol_revision": "v3"}
        cli.metadata["T-50"] = {"protocol_revision": "v3"}
        args = Namespace(
            source_issue="T-100",
            source_requirement=None,
            rule_id="WF-PROTOCOL-TEST",
            severity="medium",
            summary="child metadata is incomplete",
            expected="top-level protocol authority",
            actual="child omitted protocol_revision",
            evidence=None,
            entity=None,
            dedupe_key=None,
            protocol_revision=None,
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=False,
        )
        result = observer.report_incident(cli, args)
        self.assertIn(":v3:", result["dedupe_key"])
        self.assertEqual(cli.metadata["T-900"]["source_requirement_id"], "T-50")
        self.assertEqual(cli.metadata["T-900"]["protocol_revision"], "v3")

    def test_report_incident_redacts_summary_from_title_and_block_reason(self):
        cli = IncidentCLI()
        args = Namespace(
            source_issue="T-100",
            source_requirement=None,
            rule_id="WF-SECRET-001",
            severity="high",
            summary="Bearer top.secret",
            expected="No credential is persisted",
            actual="A credential was observed",
            evidence=None,
            entity=None,
            dedupe_key=None,
            protocol_revision=None,
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=True,
        )
        observer.report_incident(cli, args)
        title = cli.created[0]["args"][cli.created[0]["args"].index("--title") + 1]
        self.assertNotIn("top.secret", title)
        self.assertNotIn("top.secret", cli.created[0]["description"])
        self.assertNotIn("top.secret", cli.metadata["T-100"]["blocked_reason"])

    def test_partial_incident_metadata_failure_is_recovered_without_duplicate(self):
        cli = IncidentCLI()
        cli.fail_incident_metadata_once = True
        args = Namespace(
            source_issue="T-100",
            source_requirement=None,
            rule_id="WF-RECOVERY-001",
            severity="high",
            summary="metadata persistence failed",
            expected="one recoverable Incident",
            actual="first write fails",
            evidence=None,
            entity=None,
            dedupe_key=None,
            protocol_revision=None,
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=False,
        )
        with self.assertRaisesRegex(observer.ObserverError, "injected"):
            observer.report_incident(cli, args)
        self.assertTrue(cli.metadata["T-100"]["workflow_incident_pending"])
        self.assertEqual(len(cli.created), 1)
        recovery_args = observer.pending_recovery_args(
            {"identifier": "T-100"}, cli.metadata["T-100"]
        )
        self.assertIsNotNone(recovery_args)
        result = observer.report_incident(cli, recovery_args)
        self.assertEqual(result["incident_id"], "T-900")
        self.assertEqual(len(cli.created), 1)
        self.assertFalse(cli.metadata["T-100"]["workflow_incident_pending"])
        self.assertEqual(
            cli.metadata["T-900"]["reporter_agent_id"], args.reporter_agent_id
        )
        self.assertEqual(cli.metadata["T-900"]["reporter_role"], args.reporter_role)

    def test_pending_payload_write_failure_records_compact_failure_before_creation(self):
        cli = IncidentCLI()
        cli.fail_metadata_keys.add("workflow_incident_pending_payload")
        args = Namespace(
            source_issue="T-100",
            source_requirement=None,
            rule_id="WF-PENDING-001",
            severity="medium",
            summary="payload failed",
            expected="recoverable pending report",
            actual="metadata write failed",
            evidence=None,
            entity=None,
            dedupe_key=None,
            protocol_revision=None,
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=False,
        )
        with self.assertRaisesRegex(observer.ObserverError, "pending_payload"):
            observer.report_incident(cli, args)
        self.assertEqual(cli.created, [])
        self.assertTrue(any("REPORT PENDING" in content for _, content in cli.comments))
        recovery = observer.pending_recovery_args(
            {"identifier": "T-100"},
            cli.metadata["T-100"],
            [{"content": content} for _, content in cli.comments],
        )
        self.assertIsNotNone(recovery)
        self.assertEqual(recovery.rule_id, "WF-PENDING-001")

    def test_pending_marker_failure_leaves_recoverable_single_payload(self):
        cli = IncidentCLI()
        cli.fail_metadata_keys.add("workflow_incident_pending")
        args = Namespace(
            source_issue="T-100",
            source_requirement=None,
            rule_id="WF-PENDING-002",
            severity="medium",
            summary="marker failed",
            expected="recoverable pending report",
            actual="pending bool write failed",
            evidence=None,
            entity=None,
            dedupe_key=None,
            protocol_revision=None,
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=False,
        )
        with self.assertRaisesRegex(observer.ObserverError, "workflow_incident_pending"):
            observer.report_incident(cli, args)
        recovery = observer.pending_recovery_args(
            {"identifier": "T-100"}, cli.metadata["T-100"]
        )
        self.assertIsNotNone(recovery)
        self.assertEqual(recovery.rule_id, "WF-PENDING-002")
        self.assertEqual(recovery.reporter_agent_id, args.reporter_agent_id)
        self.assertEqual(recovery.reporter_role, args.reporter_role)
        self.assertEqual(
            cli.metadata["T-100"]["workflow_incident_pending_index"],
            observer.WORKFLOW_ID,
        )
        self.assertEqual(cli.created, [])

    def test_duplicate_incident_respects_notification_cooldown(self):
        cli = IncidentCLI()
        dedupe = "development-delivery:v3:WF-REVIEW-001:T-100"
        cli.incidents = [
            {
                "id": "active",
                "identifier": "T-801",
                "status": "in_progress",
                "title": f"[Workflow Incident][high][wf:{observer.incident_fingerprint(dedupe)}] test",
            }
        ]
        cli.metadata["T-801"] = {
            "workflow_object_type": "incident",
            "incident_dedupe_key": dedupe,
            "incident_status": "confirmed",
            "incident_severity": "high",
            "incident_evidence_count": 2,
            "incident_source_requirements": '["T-100"]',
            "incident_last_notified_at": observer.utc_now(),
        }
        args = Namespace(
            source_issue="T-100",
            source_requirement="T-100",
            rule_id="WF-REVIEW-001",
            severity="high",
            summary="stale review",
            expected="Review matches PR head",
            actual="Review is still old",
            evidence="same evidence",
            entity=None,
            dedupe_key=dedupe,
            protocol_revision="v3",
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=False,
        )
        result = observer.report_incident(cli, args)
        self.assertFalse(result["notified"])
        self.assertEqual(cli.comments, [])
        self.assertEqual(cli.metadata["T-801"]["incident_evidence_count"], 3)
        evidence_log = json.loads(cli.metadata["T-801"]["incident_evidence_log"])
        self.assertEqual(evidence_log[-1]["source_issue_id"], "T-100")
        self.assertEqual(evidence_log[-1]["evidence"], "same evidence")

        cli.metadata["T-200"] = {"protocol_revision": "v3"}
        args.source_issue = "T-200"
        expanded = observer.report_incident(cli, args)
        self.assertTrue(expanded["notified"])

    def test_duplicate_incident_preserves_maximum_severity_and_updates_priority(self):
        cli = IncidentCLI()
        dedupe = "development-delivery:v3:WF-REVIEW-001:T-100"
        cli.incidents = [
            {"id": "active", "identifier": "T-801", "status": "in_progress"}
        ]
        cli.metadata["T-801"] = {
            "workflow_object_type": "incident",
            "incident_dedupe_key": dedupe,
            "incident_status": "confirmed",
            "incident_severity": "high",
            "incident_source_requirements": '["T-100"]',
            "incident_last_notified_at": observer.utc_now(),
        }
        args = Namespace(
            source_issue="T-100",
            source_requirement="T-100",
            rule_id="WF-REVIEW-001",
            severity="medium",
            summary="duplicate",
            expected="review matches",
            actual="still stale",
            evidence="same",
            entity=None,
            dedupe_key=dedupe,
            protocol_revision="v3",
            reporter_agent_id="agent-reviewer",
            reporter_role="reviewer",
            block_source=False,
        )
        observer.report_incident(cli, args)
        self.assertEqual(cli.metadata["T-801"]["incident_severity"], "high")
        self.assertFalse(any("--priority" in update for update in cli.updates))

        args.severity = "urgent"
        observer.report_incident(cli, args)
        self.assertEqual(cli.metadata["T-801"]["incident_severity"], "urgent")
        self.assertTrue(
            any(
                "--priority" in update
                and update[update.index("--priority") + 1] == "urgent"
                for update in cli.updates
            )
        )
        self.assertTrue(cli.subscribers)

    def test_incident_lookup_paginates_before_deduplication(self):
        cli = IncidentCLI()
        dedupe = "development-delivery:v3:WF-REVIEW-001:T-100"
        cli.incidents = [
            {"id": f"old-{index}", "identifier": f"T-{1000 + index}", "status": "done"}
            for index in range(120)
        ]
        cli.incidents.append({"id": "active", "identifier": "T-801", "status": "in_progress"})
        cli.metadata["T-801"] = {
            "workflow_object_type": "incident",
            "incident_dedupe_key": dedupe,
            "incident_status": "confirmed",
            "incident_evidence_count": 1,
        }
        args = Namespace(
            source_issue="T-100",
            source_requirement=None,
            rule_id="WF-REVIEW-001",
            severity="high",
            summary="stale review",
            expected="Review matches PR head",
            actual="Review is old again",
            evidence="new evidence",
            entity=None,
            dedupe_key=dedupe,
            protocol_revision=None,
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=False,
        )
        result = observer.report_incident(cli, args)
        self.assertEqual(result["incident_id"], "T-801")
        self.assertEqual(result["action"], "updated")
        self.assertEqual(cli.created, [])

    def test_report_incident_reuses_unique_active_after_closed_history(self):
        cli = IncidentCLI()
        dedupe = "development-delivery:v3:WF-REVIEW-001:T-100"
        cli.incidents = [
            {"id": "old", "identifier": "T-800", "status": "done", "updated_at": "2026-01-01T00:00:00Z"},
            {"id": "active", "identifier": "T-801", "status": "in_progress", "updated_at": "2026-01-02T00:00:00Z"},
        ]
        cli.metadata["T-800"] = {
            "workflow_object_type": "incident",
            "incident_dedupe_key": dedupe,
            "incident_fixed_release": "1.0.0",
        }
        cli.metadata["T-801"] = {
            "workflow_object_type": "incident",
            "incident_dedupe_key": dedupe,
            "incident_status": "confirmed",
            "incident_evidence_count": 2,
        }
        args = Namespace(
            source_issue="T-100",
            source_requirement=None,
            rule_id="WF-REVIEW-001",
            severity="high",
            summary="stale review",
            expected="Review matches PR head",
            actual="Review is old again",
            evidence="new evidence",
            entity=None,
            dedupe_key=dedupe,
            protocol_revision=None,
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=False,
        )
        result = observer.report_incident(cli, args)
        self.assertEqual(result["incident_id"], "T-801")
        self.assertEqual(result["action"], "updated")
        self.assertEqual(cli.metadata["T-801"]["incident_status"], "confirmed")
        self.assertEqual(cli.metadata["T-801"]["incident_evidence_count"], 3)
        self.assertEqual(cli.created, [])

    def test_closed_incident_from_prior_workflow_release_creates_recurrence(self):
        cli = IncidentCLI()
        dedupe = "development-delivery:v3:WF-REVIEW-001:T-100"
        cli.incidents = [
            {
                "id": "old",
                "identifier": "T-800",
                "status": "done",
                "updated_at": "2026-01-01T00:00:00Z",
            }
        ]
        cli.metadata["T-800"] = {
            "workflow_object_type": "incident",
            "incident_dedupe_key": dedupe,
            "workflow_version": "1.0.0",
        }
        args = Namespace(
            source_issue="T-100",
            source_requirement=None,
            rule_id="WF-REVIEW-001",
            severity="high",
            summary="stale review recurred",
            expected="Review matches PR head",
            actual="Review is old in a later release",
            evidence=None,
            entity=None,
            dedupe_key=dedupe,
            protocol_revision=None,
            reporter_agent_id="agent-reviewer",
            reporter_role="代码审查员",
            block_source=False,
        )
        result = observer.report_incident(cli, args)
        self.assertEqual(result["action"], "created")
        self.assertEqual(result["incident_id"], "T-900")
        self.assertEqual(cli.metadata["T-900"]["recurrence_of"], "T-800")


if __name__ == "__main__":
    unittest.main()
