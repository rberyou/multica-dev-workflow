from argparse import Namespace
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
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
        self.fail_comment_add_once = False
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
            if self.fail_comment_add_once:
                self.fail_comment_add_once = False
                raise observer.ObserverError("injected comment failure")
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


class Phase1CLI(IncidentCLI):
    def __init__(self):
        super().__init__()
        self.workspace_id = "workspace-test"
        self.items = []
        self.next_number = 900
        self.triggered = []
        self.comment_records = {}
        self.issue_details["T-100"] = {
            "id": "source-100",
            "identifier": "T-100",
            "status": "in_progress",
            "project_id": "project-dev",
            "assignee_id": "agent-dev",
            "updated_at": "2026-07-21T00:00:00Z",
        }
        self.metadata["T-100"] = {
            "workflow_id": observer.WORKFLOW_ID,
            "workflow_instance_id": "instance-1",
            "protocol_revision": "v3",
        }

    def add_item(self, identifier, project_id, metadata, **values):
        item = {
            "id": f"id-{identifier}",
            "identifier": identifier,
            "project_id": project_id,
            "status": values.pop("status", "in_progress"),
            "updated_at": values.pop("updated_at", "2026-07-21T00:00:00Z"),
            **values,
        }
        self.items.append(item)
        self.issue_details[identifier] = item
        self.metadata[identifier] = dict(metadata)
        return item

    def add_registration(self):
        return self.add_item(
            "WOR-REG",
            "project-ops",
            {
                "workflow_object_type": "project_registration",
                "workflow_id": observer.WORKFLOW_ID,
                "workflow_instance_id": "instance-1",
                "workspace_id": self.workspace_id,
                "project_id": "project-dev",
                "project_name": "Development",
                "protocol_revision": "v3",
                "enabled": True,
                "managed_agent_ids": '["agent-dev"]',
                "committed_cursor": observer.cursor_value(
                    datetime.fromtimestamp(0, timezone.utc)
                ),
                "checkpoint_cursor": observer.cursor_value(
                    datetime.fromtimestamp(0, timezone.utc)
                ),
            },
        )

    def json(self, args, input_text=None):
        if args[:2] == ["project", "get"]:
            return {"id": args[2], "title": "Development"}
        if args[:2] == ["autopilot", "list"]:
            return [
                {
                    "id": "autopilot-observer",
                    "description": observer_marker("autopilot.workflow-health-audit"),
                }
            ]
        if args[:2] == ["autopilot", "get"]:
            return {
                "id": args[2],
                "description": observer_marker("autopilot.workflow-health-audit"),
            }
        if args[:2] == ["autopilot", "trigger"]:
            self.triggered.append(args[2])
            return {"id": "run-1"}
        if args[:2] == ["issue", "list"]:
            limit = int(args[args.index("--limit") + 1])
            offset = int(args[args.index("--offset") + 1])
            items = list(self.items)
            if "--project" in args:
                project_id = args[args.index("--project") + 1]
                items = [item for item in items if item.get("project_id") == project_id]
            if "--status" in args:
                status = args[args.index("--status") + 1]
                items = [item for item in items if item.get("status") == status]
            filters = [
                args[index + 1]
                for index, item in enumerate(args)
                if item == "--metadata"
            ]
            for value in filters:
                key, expected = value.split("=", 1)
                items = [
                    item
                    for item in items
                    if str(self.metadata.get(item["identifier"], {}).get(key)).lower()
                    == expected.lower()
                ]
            return items[offset : offset + limit]
        if args[:2] == ["issue", "create"]:
            identifier = f"WOR-{self.next_number}"
            self.next_number += 1
            item = {
                "id": f"id-{identifier}",
                "identifier": identifier,
                "project_id": args[args.index("--project") + 1],
                "status": args[args.index("--status") + 1],
                "title": args[args.index("--title") + 1],
                "updated_at": observer.utc_now(),
            }
            self.items.append(item)
            self.issue_details[identifier] = item
            self.metadata[identifier] = {}
            self.created.append({"args": args, "description": input_text})
            return item
        if args[:2] == ["issue", "get"]:
            return self.issue_details.get(args[2])
        if args[:3] == ["issue", "comment", "list"]:
            return self.comment_records.get(args[3], [])
        if args[:3] == ["issue", "comment", "add"]:
            if self.fail_comment_add_once:
                self.fail_comment_add_once = False
                raise observer.ObserverError("injected comment failure")
            record = {
                "id": f"comment-{len(self.comments) + 1}",
                "content": input_text,
                "author_id": "agent-observer",
                "author_type": "agent",
            }
            self.comments.append((args[3], input_text))
            self.comment_records.setdefault(args[3], []).append(record)
            return record
        if args[:2] == ["issue", "update"]:
            item = self.issue_details[args[2]]
            if "--status" in args:
                item["status"] = args[args.index("--status") + 1]
            if "--priority" in args:
                item["priority"] = args[args.index("--priority") + 1]
            self.updates.append(args)
            return item
        return super().json(args, input_text)


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
        self.autopilots = []
        for index, autopilot_spec in enumerate(manifest["autopilots"], start=1):
            autopilot_hash = observer.sha256_value(
                {
                    "key": autopilot_spec["key"],
                    "title": autopilot_spec["title"],
                    "description": autopilot_spec["description"].strip() + "\n",
                    "agent": autopilot_spec["agent"],
                    "mode": autopilot_spec["mode"],
                    "project": autopilot_spec["project"],
                    "status": autopilot_spec["status"],
                    "issue_title_template": autopilot_spec.get(
                        "issue_title_template", ""
                    ),
                    "subscriber_ids": ["human-1"],
                }
            )
            self.autopilots.append(
                {
                    "id": f"autopilot-{index}",
                    "title": autopilot_spec["title"],
                    "description": observer_marker(
                        f"autopilot.{autopilot_spec['key']}",
                        autopilot_spec["description"].strip() + "\n",
                        autopilot_hash,
                    ),
                    "agent_id": self.agent_by_key[autopilot_spec["agent"]]["id"],
                    "mode": autopilot_spec["mode"],
                    "project_id": "project-1",
                    "status": autopilot_spec["status"],
                    "issue_title_template": autopilot_spec.get(
                        "issue_title_template", ""
                    ),
                    "subscribers": [
                        {"user_id": "human-1", "user_type": "member"}
                    ],
                    "triggers": [
                        {
                            "id": f"trigger-{index}",
                            **{
                                key: autopilot_spec["triggers"][0][key]
                                for key in [
                                    "kind",
                                    "label",
                                    "enabled",
                                    "cron",
                                    "timezone",
                                ]
                            },
                        }
                    ],
                }
            )

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

    def test_legacy_recovery_intake_is_not_treated_as_managed_v3_requirement(self):
        metadata = {
            "workflow_version": "1.1.0-rc.1",
            "protocol_revision": "v3",
            "blocked_reason": "historical partial Apply",
            "waiting_on": "workflow_fix",
        }
        findings = observer.audit_issue(
            AuditCLI(metadata),
            {"identifier": "WOR-1", "status": "blocked"},
            24,
        )
        self.assertNotIn("WF-PROTOCOL-001", {item["rule_id"] for item in findings})

        metadata["workflow_incident_pending"] = True
        findings = observer.audit_issue(
            AuditCLI(metadata),
            {"identifier": "WOR-1", "status": "blocked"},
            24,
        )
        self.assertIn("WF-INCIDENT-001", {item["rule_id"] for item in findings})

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
            "workflow_version": "1.1.0-rc.4",
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

    def test_phase1_control_contract_excludes_future_maintenance_components(self):
        contract = observer.control_contract()
        self.assertEqual(contract["workflow_phase"], 1)
        self.assertNotIn("workflow-maintainer", contract["agents"])
        self.assertNotIn("workflow-maintenance-reviewer", contract["agents"])
        self.assertNotIn("multica-workflow-maintainer", contract["skills"])

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
            "unexpected disabled trigger",
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
                elif case == "unexpected disabled trigger":
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
        for current, autopilot_spec in zip(
            cli.autopilots, manifest["autopilots"], strict=True
        ):
            current["status"] = "paused"
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
            current["description"] = observer_marker(
                f"autopilot.{autopilot_spec['key']}",
                autopilot_spec["description"].strip() + "\n",
                disabled_hash,
            )
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

    def test_duplicate_incident_preserves_active_workflow_waiting_state(self):
        cli = IncidentCLI()
        dedupe = "development-delivery:v3:WF-REVIEW-001:T-100"
        cli.incidents = [
            {"id": "active", "identifier": "T-801", "status": "in_progress"}
        ]
        cli.metadata["T-801"] = {
            "workflow_object_type": "incident",
            "incident_dedupe_key": dedupe,
            "incident_status": "in_fix",
            "logical_status": "in_fix",
            "waiting_on": "ordinary_development_workflow",
            "incident_severity": "high",
            "incident_source_requirements": '["T-100"]',
            "incident_last_notified_at": observer.utc_now(),
        }
        observer.report_incident(
            cli,
            Namespace(
                source_issue="T-100",
                source_requirement="T-100",
                rule_id="WF-REVIEW-001",
                severity="high",
                summary="new evidence during repair",
                expected="repair remains active",
                actual="same root cause reproduced",
                evidence="new evidence",
                entity=None,
                dedupe_key=dedupe,
                protocol_revision="v3",
                reporter_agent_id="agent-reviewer",
                reporter_role="代码审查员",
                block_source=False,
            ),
        )
        self.assertEqual(cli.metadata["T-801"]["incident_status"], "in_fix")
        self.assertEqual(
            cli.metadata["T-801"]["waiting_on"],
            "ordinary_development_workflow",
        )

    def test_active_incident_with_terminal_logical_state_repairs_reopen(self):
        cli = IncidentCLI()
        dedupe = "development-delivery:v3:WF-REVIEW-001:T-100"
        cli.incidents = [
            {"id": "active", "identifier": "T-801", "status": "todo"}
        ]
        cli.metadata["T-801"] = {
            "workflow_object_type": "incident",
            "incident_dedupe_key": dedupe,
            "incident_status": "resolved",
            "logical_status": "resolved",
            "incident_severity": "high",
            "incident_source_requirements": '["T-100"]',
            "incident_evidence_log": "[]",
        }
        result = observer.report_incident(
            cli,
            Namespace(
                source_issue="T-100",
                source_requirement="T-100",
                rule_id="WF-REVIEW-001",
                severity="high",
                summary="reopened evidence",
                expected="review matches",
                actual="stale review recurred",
                evidence="new evidence",
                entity=None,
                dedupe_key=dedupe,
                protocol_revision="v3",
                reporter_agent_id="agent-reviewer",
                reporter_role="reviewer",
                block_source=False,
            ),
        )
        self.assertEqual(result["incident_id"], "T-801")
        self.assertEqual(cli.metadata["T-801"]["logical_status"], "new")
        self.assertEqual(cli.metadata["T-801"]["waiting_on"], "workflow_observer")

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

    def phase1_report_args(self, source_issue="T-100", severity="medium"):
        return Namespace(
            source_issue=source_issue,
            source_requirement=None,
            rule_id="WF-PHASE1-TEST",
            severity=severity,
            summary="observer test anomaly",
            expected="workflow remains consistent",
            actual="workflow drifted",
            evidence="bounded evidence",
            entity="runtime:shared",
            dedupe_key=None,
            protocol_revision=None,
            reporter_agent_id="agent-dev",
            reporter_role="developer",
            block_source=False,
            notification_cooldown_hours=24,
            deterministic_confirmation=False,
            blocked_requirement_count=0,
            no_wake=True,
        )

    def test_phase1_parser_exposes_required_commands(self):
        commands = observer.parser()._subparsers._group_actions[0].choices
        for command in [
            "report-anomaly",
            "register-project",
            "bind-workflow-issue",
            "scan",
            "triage",
            "prepare-maintenance-decision",
            "record-maintenance-decision",
            "record-maintenance-progress",
            "verify-fix",
        ]:
            self.assertIn(command, commands)

    def test_bind_workflow_issue_injects_registered_source_contract(self):
        cli = Phase1CLI()
        cli.add_registration()
        cli.metadata["T-100"] = {}
        result = observer.bind_workflow_issue(
            cli,
            Namespace(
                issue="T-100",
                object_type="requirement",
                root_requirement_id=None,
                created_by_role="leader",
            ),
        )
        self.assertEqual(result["workflow_instance_id"], "instance-1")
        self.assertEqual(cli.metadata["T-100"]["managed_by"], observer.MANAGED_BY)
        self.assertEqual(cli.metadata["T-100"]["root_requirement_id"], "T-100")

    def test_bind_workflow_issue_rejects_contract_overwrite(self):
        cli = Phase1CLI()
        cli.add_registration()
        observer.bind_workflow_issue(
            cli,
            Namespace(
                issue="T-100",
                object_type="requirement",
                root_requirement_id=None,
                created_by_role="leader",
            ),
        )
        with self.assertRaisesRegex(observer.ObserverError, "created_by_role"):
            observer.bind_workflow_issue(
                cli,
                Namespace(
                    issue="T-100",
                    object_type="requirement",
                    root_requirement_id=None,
                    created_by_role="developer",
                ),
            )

    def test_partial_observation_metadata_failure_reuses_title_fingerprint(self):
        cli = Phase1CLI()
        cli.add_registration()
        cli.fail_metadata_keys.add("workflow_object_type")
        args = self.phase1_report_args()
        with self.assertRaisesRegex(observer.ObserverError, "workflow_object_type"):
            observer.report_anomaly(cli, args)
        result = observer.report_anomaly(cli, args)
        observations = [
            item
            for item in cli.items
            if "[Workflow Observation]" in str(item.get("title") or "")
        ]
        self.assertEqual(len(observations), 1)
        self.assertEqual(result["observation_id"], observations[0]["identifier"])

    def test_same_quarantined_observation_does_not_wake_observer_again(self):
        cli = Phase1CLI()
        cli.add_registration()
        args = self.phase1_report_args(severity="high")
        args.no_wake = False
        first = observer.report_anomaly(cli, args)
        cli.metadata[first["observation_id"]].update(
            {
                "observation_status": "quarantined",
                "status": "quarantined",
                "attempt_count": observer.OBSERVATION_MAX_ATTEMPTS,
            }
        )
        cli.triggered.clear()
        second = observer.report_anomaly(cli, args)
        self.assertEqual(second["status"], "quarantined")
        self.assertFalse(second["observer_awakened"])
        self.assertEqual(cli.triggered, [])

    def test_invalid_observation_attempt_count_is_repaired_without_aborting(self):
        cli = Phase1CLI()
        observation = cli.add_item(
            "WOR-BAD-COUNT",
            "project-ops",
            {
                "workflow_object_type": "observation",
                "observation_status": "failed",
                "status": "failed",
                "attempt_count": "invalid",
                "observation_payload": "{}",
            },
        )
        result = observer.process_observation(cli, observation)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(
            cli.metadata[observation["identifier"]]["attempt_count"], 1
        )

    def test_scan_quarantines_permanent_observation_and_continues(self):
        cli = Phase1CLI()
        cli.add_registration()
        invalid = cli.add_item(
            "WOR-BAD",
            "project-ops",
            {
                "workflow_object_type": "observation",
                "workflow_instance_id": "instance-1",
                "observation_status": "failed",
                "status": "failed",
                "attempt_count": observer.OBSERVATION_MAX_ATTEMPTS - 1,
                "observation_payload": "{}",
            },
        )
        payload = {
            "workflow_instance_id": "instance-1",
            "source_issue_id": "T-100",
            "rule_id": "WF-VALID-001",
            "severity": "medium",
            "entity": "runtime:shared",
            "summary": "valid observation",
            "expected": "consistent workflow",
            "actual": "drift",
            "protocol_revision": "v3",
            "reporter_agent_id": "agent-dev",
            "reporter_role": "developer",
            "registered": True,
            "block_source": False,
        }
        valid = cli.add_item(
            "WOR-GOOD",
            "project-ops",
            {
                "workflow_object_type": "observation",
                "workflow_instance_id": "instance-1",
                "observation_status": "pending",
                "status": "pending",
                "attempt_count": 0,
                "observation_payload": json.dumps(payload),
            },
        )
        args = Namespace(
            mode="incremental",
            workflow_instance_id=None,
            max_issues=5000,
            backlog_hours=24,
            lease_minutes=30,
        )
        with (
            patch.object(observer, "audit_operation_issue", return_value=[]),
            patch.object(observer, "audit_control_plane", return_value=[]),
            patch.object(observer, "audit_issue", return_value=[]),
            patch.object(observer, "parent_findings", return_value=[]),
        ):
            result = observer.scan(cli, args)
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            cli.metadata[invalid["identifier"]]["observation_status"],
            "quarantined",
        )
        self.assertEqual(
            cli.metadata[valid["identifier"]]["observation_status"], "processed"
        )
        self.assertEqual(len(result["observation_failures"]), 1)

    def test_scan_contains_observation_claim_failure_and_continues(self):
        cli = Phase1CLI()
        cli.add_registration()
        payload = {
            "workflow_instance_id": "instance-1",
            "source_issue_id": "T-100",
            "rule_id": "WF-VALID-001",
            "severity": "medium",
            "entity": "runtime:shared",
            "summary": "valid observation",
            "expected": "consistent workflow",
            "actual": "drift",
            "protocol_revision": "v3",
            "reporter_agent_id": "agent-dev",
            "reporter_role": "developer",
            "registered": True,
            "block_source": False,
        }
        failed = cli.add_item(
            "WOR-CLAIM-FAIL",
            "project-ops",
            {
                "workflow_object_type": "observation",
                "workflow_instance_id": "instance-1",
                "observation_status": "pending",
                "status": "pending",
                "attempt_count": 0,
                "observation_payload": json.dumps(payload),
            },
        )
        processed = cli.add_item(
            "WOR-CLAIM-NEXT",
            "project-ops",
            {
                "workflow_object_type": "observation",
                "workflow_instance_id": "instance-1",
                "observation_status": "pending",
                "status": "pending",
                "attempt_count": 0,
                "observation_payload": json.dumps(payload),
            },
        )
        cli.fail_metadata_keys.add("attempt_count")
        args = Namespace(
            mode="incremental",
            workflow_instance_id=None,
            max_issues=5000,
            backlog_hours=24,
            lease_minutes=30,
        )
        with (
            patch.object(observer, "audit_operation_issue", return_value=[]),
            patch.object(observer, "audit_control_plane", return_value=[]),
            patch.object(observer, "audit_issue", return_value=[]),
            patch.object(observer, "parent_findings", return_value=[]),
        ):
            result = observer.scan(cli, args)
        self.assertEqual(result["status"], "success")
        self.assertEqual(
            cli.metadata[failed["identifier"]]["observation_status"], "failed"
        )
        self.assertEqual(
            cli.metadata[processed["identifier"]]["observation_status"],
            "processed",
        )
        self.assertEqual(len(result["observation_failures"]), 1)

    def test_project_registration_is_idempotent(self):
        cli = Phase1CLI()
        args = Namespace(
            project_id="project-dev",
            workflow_instance_id="instance-1",
            development_squad_id="squad-dev",
            managed_agent_ids="agent-dev,agent-reviewer",
            protocol_revision="v3",
            disabled=False,
        )
        created = observer.register_project(cli, args)
        updated = observer.register_project(cli, args)
        self.assertEqual(created["action"], "created")
        self.assertEqual(updated["action"], "updated")
        registrations = [
            item
            for item in cli.items
            if cli.metadata[item["identifier"]].get("workflow_object_type")
            == "project_registration"
        ]
        self.assertEqual(len(registrations), 1)

    def test_report_anomaly_creates_durable_observation_before_incident(self):
        cli = Phase1CLI()
        cli.add_registration()
        args = self.phase1_report_args()
        first = observer.report_anomaly(cli, args)
        second = observer.report_anomaly(cli, args)
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "updated")
        observations = [
            item
            for item in cli.items
            if cli.metadata[item["identifier"]].get("workflow_object_type")
            == "observation"
        ]
        incidents = [
            item
            for item in cli.items
            if cli.metadata[item["identifier"]].get("workflow_object_type")
            == "incident"
        ]
        self.assertEqual(len(observations), 1)
        self.assertEqual(incidents, [])
        self.assertTrue(cli.metadata["T-100"]["workflow_observation_pending"])

    def test_high_anomaly_wakes_observer_after_observation_is_durable(self):
        cli = Phase1CLI()
        cli.add_registration()
        args = self.phase1_report_args(severity="high")
        args.no_wake = False
        result = observer.report_anomaly(cli, args)
        self.assertTrue(result["observer_awakened"])
        self.assertEqual(cli.triggered, ["autopilot-observer"])
        self.assertEqual(
            cli.metadata[result["observation_id"]]["observation_status"], "pending"
        )

    def test_observations_from_two_sources_share_one_incident(self):
        cli = Phase1CLI()
        cli.add_registration()
        cli.issue_details["T-101"] = {
            "id": "source-101",
            "identifier": "T-101",
            "status": "in_progress",
            "project_id": "project-dev",
            "assignee_id": "agent-dev",
            "updated_at": "2026-07-21T00:01:00Z",
        }
        cli.metadata["T-101"] = dict(cli.metadata["T-100"])
        first = observer.report_anomaly(cli, self.phase1_report_args("T-100"))
        second = observer.report_anomaly(cli, self.phase1_report_args("T-101"))
        observer.process_observation(cli, cli.issue_details[first["observation_id"]])
        observer.process_observation(cli, cli.issue_details[second["observation_id"]])
        incidents = [
            item
            for item in cli.items
            if cli.metadata[item["identifier"]].get("workflow_object_type")
            == "incident"
        ]
        self.assertEqual(len(incidents), 1)
        self.assertEqual(
            cli.metadata[first["observation_id"]]["incident_id"],
            cli.metadata[second["observation_id"]]["incident_id"],
        )

    def test_scan_recovers_observation_left_processing_after_crash(self):
        cli = Phase1CLI()
        cli.add_registration()
        reported = observer.report_anomaly(cli, self.phase1_report_args())
        observation_id = reported["observation_id"]
        cli.metadata[observation_id]["observation_status"] = "processing"
        cli.metadata[observation_id]["status"] = "processing"
        args = Namespace(
            mode="incremental",
            workflow_instance_id="instance-1",
            max_issues=5000,
            backlog_hours=24,
            lease_minutes=30,
        )
        with (
            patch.object(observer, "audit_control_plane", return_value=[]),
            patch.object(observer, "audit_issue", return_value=[]),
            patch.object(observer, "parent_findings", return_value=[]),
        ):
            result = observer.scan(cli, args)
        self.assertEqual(len(result["processed_observations"]), 1)
        processed = result["processed_observations"][0]
        self.assertEqual(processed["observation_id"], observation_id)
        self.assertEqual(processed["action"], "processed")
        incident_id = processed["incident_id"]
        self.assertEqual(
            cli.metadata[observation_id]["observation_status"], "processed"
        )
        self.assertFalse(cli.metadata["T-100"]["workflow_observation_pending"])
        self.assertEqual(
            cli.metadata[incident_id]["workflow_instance_id"], "instance-1"
        )

    def test_scan_commits_cursor_only_after_success(self):
        cli = Phase1CLI()
        registration = cli.add_registration()
        args = Namespace(
            mode="incremental",
            max_issues=5000,
            backlog_hours=24,
            lease_minutes=30,
        )
        with (
            patch.object(observer, "audit_control_plane", return_value=[]),
            patch.object(observer, "audit_issue", return_value=[]),
            patch.object(observer, "parent_findings", return_value=[]),
        ):
            result = observer.scan(cli, args)
        self.assertEqual(result["status"], "success")
        registration_metadata = cli.metadata[registration["identifier"]]
        self.assertEqual(
            registration_metadata["committed_cursor"],
            registration_metadata["checkpoint_cursor"],
        )
        controls = [
            item
            for item in cli.items
            if cli.metadata[item["identifier"]].get("workflow_object_type")
            == "observer_control"
        ]
        self.assertEqual(len(controls), 1)
        self.assertEqual(cli.metadata[controls[0]["identifier"]]["status"], "success")

    def test_scan_uses_instance_scoped_control_and_requires_selection(self):
        cli = Phase1CLI()
        cli.add_registration()
        cli.add_item(
            "WOR-REG-2",
            "project-ops",
            {
                "workflow_object_type": "project_registration",
                "workflow_id": observer.WORKFLOW_ID,
                "workflow_instance_id": "instance-2",
                "workspace_id": cli.workspace_id,
                "project_id": "project-two",
                "project_name": "Two",
                "protocol_revision": "v3",
                "enabled": True,
                "managed_agent_ids": "[]",
                "committed_cursor": observer.cursor_value(
                    datetime.fromtimestamp(0, timezone.utc)
                ),
                "checkpoint_cursor": observer.cursor_value(
                    datetime.fromtimestamp(0, timezone.utc)
                ),
            },
        )
        args = Namespace(
            mode="incremental",
            workflow_instance_id=None,
            max_issues=5000,
            backlog_hours=24,
            lease_minutes=30,
        )
        with self.assertRaisesRegex(observer.ObserverError, "multiple workflow instances"):
            observer.scan(cli, args)
        args.workflow_instance_id = "instance-2"
        with (
            patch.object(observer, "audit_control_plane", return_value=[]),
            patch.object(observer, "audit_issue", return_value=[]),
            patch.object(observer, "parent_findings", return_value=[]),
        ):
            result = observer.scan(cli, args)
        self.assertEqual(result["workflow_instance_id"], "instance-2")
        controls = [
            cli.metadata[item["identifier"]]
            for item in cli.items
            if cli.metadata[item["identifier"]].get("workflow_object_type")
            == "observer_control"
        ]
        self.assertEqual([item["workflow_instance_id"] for item in controls], ["instance-2"])

    def test_scan_lost_lease_never_commits_checkpoint(self):
        cli = Phase1CLI()
        registration = cli.add_registration()
        original_cursor = cli.metadata[registration["identifier"]]["committed_cursor"]
        args = Namespace(
            mode="incremental",
            workflow_instance_id=None,
            max_issues=5000,
            backlog_hours=24,
            lease_minutes=30,
        )
        original_renew = observer.renew_scan_lease
        calls = 0

        def lose_before_commit(target, control_id, owner, lease_minutes):
            nonlocal calls
            calls += 1
            if calls == 4:
                target.metadata[control_id]["lease_owner"] = "new-owner"
                raise observer.ObserverError("Observer scan lease was lost")
            original_renew(target, control_id, owner, lease_minutes)

        with (
            patch.object(observer, "audit_control_plane", return_value=[]),
            patch.object(observer, "audit_issue", return_value=[]),
            patch.object(observer, "parent_findings", return_value=[]),
            patch.object(observer, "renew_scan_lease", side_effect=lose_before_commit),
        ):
            with self.assertRaisesRegex(observer.ObserverError, "lease was lost"):
                observer.scan(cli, args)
        self.assertEqual(
            cli.metadata[registration["identifier"]]["committed_cursor"],
            original_cursor,
        )
        self.assertNotEqual(
            cli.metadata[registration["identifier"]]["checkpoint_cursor"],
            original_cursor,
        )

    def test_external_health_requires_existing_instance_control_without_creating_one(self):
        cli = Phase1CLI()
        cli.add_registration()
        with patch.object(observer, "health", return_value={"age_minutes": 1}):
            with self.assertRaisesRegex(observer.ObserverError, "missing for instances"):
                observer.external_health(cli, 135, 1560)
        self.assertFalse(
            any(
                cli.metadata[item["identifier"]].get("workflow_object_type")
                == "observer_control"
                for item in cli.items
            )
        )

    def test_prepare_maintenance_decision_repairs_missing_prompt_comment(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "awaiting_maintenance_decision",
                "logical_status": "awaiting_maintenance_decision",
                "verdict": "CONFIRMED_WORKFLOW_BUG",
                "incident_dedupe_key": "dedupe",
                "incident_severity": "high",
                "incident_source_requirements": '["T-100"]',
                "source_issue_id": "T-100",
                "workflow_version": "1.2.0",
                "incident_evidence_log": "[]",
            },
            status="in_review",
        )
        cli.fail_comment_add_once = True
        with self.assertRaisesRegex(observer.ObserverError, "comment failure"):
            observer.prepare_maintenance_decision(
                cli, Namespace(incident=incident["identifier"])
            )
        result = observer.prepare_maintenance_decision(
            cli, Namespace(incident=incident["identifier"])
        )
        self.assertEqual(result["action"], "repaired")
        self.assertEqual(len(cli.comment_records[incident["identifier"]]), 1)
        self.assertIn(
            result["approve"],
            cli.comment_records[incident["identifier"]][0]["content"],
        )

    def test_triage_approval_creates_one_minimal_maintenance_case(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "new",
                "incident_dedupe_key": "dedupe",
                "incident_severity": "high",
                "incident_source_requirements": '["T-100"]',
                "source_issue_id": "T-100",
                "workflow_version": "1.2.0",
                "incident_evidence_log": "[]",
                "human_approver_id": "human-1",
            },
            status="todo",
        )
        observer.triage_incident(
            cli,
            Namespace(
                incident=incident["identifier"],
                verdict="CONFIRMED_WORKFLOW_BUG",
                reason="reproduced",
            ),
        )
        prepared = observer.prepare_maintenance_decision(
            cli, Namespace(incident=incident["identifier"])
        )
        cli.comment_records[incident["identifier"]].append(
            {
                "id": "approval-1",
                "content": prepared["approve"],
                "author_id": "human-1",
                "author_type": "member",
            }
        )
        first = observer.record_maintenance_decision(
            cli,
            Namespace(
                incident=incident["identifier"],
                comment_id="approval-1",
                executor=None,
            ),
        )
        case_metadata = cli.metadata[first["maintenance_case_id"]]
        case_metadata.update(
            {
                "implementation_issue_ids": '["T-REQ", "T-VALIDATE"]',
                "pr_number": "42",
                "merge_commit_sha": "a" * 40,
                "release_version": "v1.2.0",
                "deployment_target": "workspace-canary",
                "maintenance_case_status": "awaiting_observer_verification",
                "logical_status": "awaiting_observer_verification",
            }
        )
        cli.metadata[incident["identifier"]].update(
            {
                "incident_status": "awaiting_verification",
                "logical_status": "awaiting_verification",
            }
        )
        second = observer.record_maintenance_decision(
            cli,
            Namespace(
                incident=incident["identifier"],
                comment_id="approval-1",
                executor=None,
            ),
        )
        self.assertEqual(first["action"], "created")
        self.assertEqual(second["action"], "reused")
        self.assertEqual(first["maintenance_case_id"], second["maintenance_case_id"])
        self.assertEqual(
            cli.metadata[first["maintenance_case_id"]]["human_approver_id"],
            "human-1",
        )
        self.assertEqual(
            cli.metadata[first["maintenance_case_id"]]["merge_commit_sha"],
            "a" * 40,
        )
        self.assertEqual(
            cli.metadata[first["maintenance_case_id"]]["deployment_target"],
            "workspace-canary",
        )

    def test_maintenance_decision_ignores_matching_non_approver_comment(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "awaiting_maintenance_decision",
                "logical_status": "awaiting_maintenance_decision",
                "verdict": "CONFIRMED_WORKFLOW_BUG",
                "incident_dedupe_key": "dedupe",
                "incident_severity": "high",
                "incident_source_requirements": '["T-100"]',
                "source_issue_id": "T-100",
                "workflow_version": "1.2.0",
                "incident_evidence_log": "[]",
                "human_approver_id": "human-1",
            },
            status="in_review",
        )
        prepared = observer.prepare_maintenance_decision(
            cli, Namespace(incident=incident["identifier"])
        )
        cli.comment_records[incident["identifier"]].extend(
            [
                {
                    "id": "wrong-approval",
                    "content": prepared["approve"],
                    "author_id": "human-2",
                    "author_type": "member",
                },
                {
                    "id": "approval-1",
                    "content": prepared["approve"],
                    "author_id": "human-1",
                    "author_type": "member",
                },
            ]
        )
        result = observer.record_maintenance_decision(
            cli,
            Namespace(
                incident=incident["identifier"],
                comment_id=None,
                executor="ordinary_development_workflow",
            ),
        )
        self.assertEqual(result["decision"], "approved")
        self.assertEqual(
            cli.metadata[incident["identifier"]][
                "maintenance_decision_comment_id"
            ],
            "approval-1",
        )

    def test_routed_incident_can_be_retriaged_when_new_evidence_arrives(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "routed",
                "logical_status": "routed",
                "verdict": "RUNTIME_INCIDENT",
                "waiting_on": "runtime_incident",
            },
            status="in_review",
        )
        result = observer.triage_incident(
            cli,
            Namespace(
                incident=incident["identifier"],
                verdict="FALSE_POSITIVE",
                reason="controlled canary fault injection",
            ),
        )
        self.assertEqual(result["previous_status"], "routed")
        self.assertEqual(result["status"], "false_positive")
        self.assertEqual(cli.issue_details[incident["identifier"]]["status"], "done")
        self.assertEqual(cli.metadata[incident["identifier"]]["waiting_on"], "")

    def test_false_positive_restores_incident_owned_source_block(self):
        cli = Phase1CLI()
        cli.issue_details["T-100"]["status"] = "blocked"
        cli.metadata["T-100"].update(
            {
                "workflow_blocked_by_incident_id": "WOR-INC",
                "workflow_blocked_previous_status": "in_progress",
                "waiting_on": "workflow_fix",
            }
        )
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "new",
                "logical_status": "new",
                "incident_blocked_source_ids": '["T-100"]',
            },
            status="todo",
        )
        result = observer.triage_incident(
            cli,
            Namespace(
                incident=incident["identifier"],
                verdict="FALSE_POSITIVE",
                reason="not reproducible",
            ),
        )
        self.assertEqual(result["restored_source_issue_ids"], ["T-100"])
        self.assertEqual(cli.issue_details["T-100"]["status"], "in_progress")
        self.assertEqual(
            cli.metadata["T-100"]["workflow_blocked_by_incident_id"], ""
        )

    def test_maintenance_progress_reaches_observer_verification_with_bound_journal(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "maintenance_approved",
                "logical_status": "maintenance_approved",
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_id": "WOR-CASE",
                "observer_id": "agent-observer",
            },
            status="in_review",
        )
        case = cli.add_item(
            "WOR-CASE",
            "project-ops",
            {
                "workflow_object_type": "maintenance_case",
                "incident_id": incident["identifier"],
                "maintenance_intake_digest": "approved-digest",
                "executor": "ordinary_development_workflow",
                "maintenance_case_status": "approved",
                "logical_status": "approved",
                "implementation_issue_ids": "[]",
            },
            status="todo",
        )

        def progress(stage, **values):
            defaults = {
                "incident": incident["identifier"],
                "stage": stage,
                "implementation_issue": [],
                "requirement_issue": None,
                "integration_validation_issue": None,
                "pr_number": None,
                "merge_commit_sha": None,
                "release_version": None,
                "release_source_commit": None,
                "release_request_digest": None,
                "deployment_target": None,
                "deployment_plan_digest": None,
                "deployment_journal": None,
            }
            defaults.update(values)
            return observer.record_maintenance_progress(cli, Namespace(**defaults))

        progress("in-development", implementation_issue=["T-REQ"])
        progress(
            "fix-ready",
            requirement_issue="T-REQ",
            integration_validation_issue="T-VALIDATE",
            pr_number="42",
            merge_commit_sha="a" * 40,
        )
        progress(
            "release-recorded",
            release_version="v1.2.0",
            release_source_commit="a" * 40,
            release_request_digest="b" * 64,
        )
        with tempfile.TemporaryDirectory() as temporary:
            journal = Path(temporary) / "journal.json"
            deployment_record = Path(temporary) / "deployment.json"
            journal.write_text(
                json.dumps(
                    {
                        "finished_at": "2026-07-25T00:00:00Z",
                        "plan_digest": "c" * 64,
                        "source_commit": "a" * 40,
                        "applied_actor": "human_host",
                        "deployment_record": str(deployment_record),
                        "workspace": {
                            "id": "workspace-canary",
                            "slug": "workflow-canary",
                        },
                    }
                ),
                encoding="utf-8",
            )
            deployment_record.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "deployed_at": "2026-07-25T00:00:00Z",
                        "plan_digest": "c" * 64,
                        "source_commit": "a" * 40,
                        "applied_actor": "human_host",
                        "workspace": {
                            "id": "workspace-canary",
                            "slug": "workflow-canary",
                        },
                        "journal": str(journal),
                    }
                ),
                encoding="utf-8",
            )
            progress(
                "deployment-recorded",
                deployment_target="workflow-canary",
                deployment_plan_digest="c" * 64,
                deployment_journal=str(journal),
            )
        self.assertEqual(
            cli.metadata[case["identifier"]]["maintenance_case_status"],
            "awaiting_observer_verification",
        )
        self.assertEqual(
            cli.metadata[incident["identifier"]]["incident_status"],
            "awaiting_verification",
        )
        self.assertEqual(
            cli.metadata[case["identifier"]]["deployment_plan_digest"], "c" * 64
        )

    def test_maintenance_progress_rejects_skipped_stage(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "maintenance_approved",
                "logical_status": "maintenance_approved",
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_id": "WOR-CASE",
            },
            status="in_review",
        )
        cli.add_item(
            "WOR-CASE",
            "project-ops",
            {
                "workflow_object_type": "maintenance_case",
                "incident_id": incident["identifier"],
                "maintenance_intake_digest": "approved-digest",
                "executor": "ordinary_development_workflow",
                "maintenance_case_status": "approved",
                "logical_status": "approved",
                "implementation_issue_ids": "[]",
            },
            status="todo",
        )
        with self.assertRaisesRegex(observer.ObserverError, "fix cannot be recorded"):
            observer.record_maintenance_progress(
                cli,
                Namespace(
                    incident=incident["identifier"],
                    stage="fix-ready",
                    implementation_issue=[],
                    requirement_issue="T-REQ",
                    integration_validation_issue="T-VALIDATE",
                    pr_number="42",
                    merge_commit_sha="a" * 40,
                    release_version=None,
                    release_source_commit=None,
                    release_request_digest=None,
                    deployment_target=None,
                    deployment_plan_digest=None,
                    deployment_journal=None,
                ),
            )

    def test_maintenance_progress_retries_after_partial_status_metadata_failure(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "in_fix",
                "logical_status": "in_fix",
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_id": "WOR-CASE",
            },
            status="in_progress",
        )
        case = cli.add_item(
            "WOR-CASE",
            "project-ops",
            {
                "workflow_object_type": "maintenance_case",
                "incident_id": incident["identifier"],
                "maintenance_intake_digest": "approved-digest",
                "executor": "ordinary_development_workflow",
                "maintenance_case_status": "in_development",
                "logical_status": "in_development",
                "implementation_issue_ids": '["T-REQ"]',
            },
            status="in_progress",
        )
        args = Namespace(
            incident=incident["identifier"],
            stage="fix-ready",
            implementation_issue=[],
            requirement_issue="T-REQ",
            integration_validation_issue="T-VALIDATE",
            pr_number="42",
            merge_commit_sha="a" * 40,
            release_version=None,
            release_source_commit=None,
            release_request_digest=None,
            deployment_target=None,
            deployment_plan_digest=None,
            deployment_journal=None,
        )
        cli.fail_metadata_keys.add("logical_status")
        with self.assertRaisesRegex(observer.ObserverError, "logical_status"):
            observer.record_maintenance_progress(cli, args)
        result = observer.record_maintenance_progress(cli, args)
        self.assertEqual(result["maintenance_case_status"], "fix_ready")
        self.assertEqual(
            cli.metadata[case["identifier"]]["logical_status"], "fix_ready"
        )

    def test_failed_verification_archives_attempt_and_accepts_new_fix(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "awaiting_verification",
                "logical_status": "awaiting_verification",
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_id": "WOR-CASE",
                "observer_id": "agent-observer",
            },
            status="in_review",
        )
        case = cli.add_item(
            "WOR-CASE",
            "project-ops",
            {
                "workflow_object_type": "maintenance_case",
                "incident_id": incident["identifier"],
                "maintenance_intake_digest": "approved-digest",
                "executor": "ordinary_development_workflow",
                "maintenance_case_status": "awaiting_observer_verification",
                "logical_status": "awaiting_observer_verification",
                "implementation_issue_ids": '["T-REQ", "T-VALIDATE"]',
                "requirement_issue_id": "T-REQ",
                "review_issue_id": "T-VALIDATE",
                "pr_number": "42",
                "merge_commit_sha": "a" * 40,
                "release_version": "v1.2.0",
                "release_tag": "v1.2.0",
                "release_source_commit": "a" * 40,
                "release_request_digest": "b" * 64,
                "deployment_target": "workspace-canary",
                "deployment_plan_digest": "c" * 64,
                "deployment_journal_sha256": "d" * 64,
                "deployment_record_sha256": "f" * 64,
                "deployed_version": "v1.2.0",
            },
            status="in_review",
        )
        with patch.dict(
            observer.os.environ, {"MULTICA_AGENT_ID": "agent-observer"}, clear=True
        ):
            observer.verify_fix(
                cli,
                Namespace(
                    incident=incident["identifier"],
                    result="failed",
                    evidence="regression still reproduces",
                    deployed_version=None,
                    deployment_target=None,
                ),
            )
        result = observer.record_maintenance_progress(
            cli,
            Namespace(
                incident=incident["identifier"],
                stage="fix-ready",
                implementation_issue=[],
                requirement_issue="T-REQ-2",
                integration_validation_issue="T-VALIDATE-2",
                pr_number="43",
                merge_commit_sha="e" * 40,
                release_version=None,
                release_source_commit=None,
                release_request_digest=None,
                deployment_target=None,
                deployment_plan_digest=None,
                deployment_journal=None,
            ),
        )
        history = observer.parse_json_map_list(
            cli.metadata[case["identifier"]]["maintenance_attempt_history"]
        )
        self.assertEqual(result["maintenance_case_status"], "fix_ready")
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["merge_commit_sha"], "a" * 40)
        self.assertEqual(history[0]["verification_result"], "failed")
        self.assertEqual(
            cli.metadata[case["identifier"]]["merge_commit_sha"], "e" * 40
        )
        self.assertEqual(cli.metadata[case["identifier"]]["release_version"], "")
        self.assertEqual(
            cli.metadata[case["identifier"]]["deployment_plan_digest"], ""
        )
        self.assertEqual(
            cli.metadata[case["identifier"]]["verification_result"], ""
        )

    def test_verify_fix_rejects_host_without_observer_identity(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "awaiting_verification",
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_id": "WOR-CASE",
                "observer_id": "agent-observer",
            },
            status="in_review",
        )
        cli.add_item(
            "WOR-CASE",
            "project-ops",
            {
                "workflow_object_type": "maintenance_case",
                "incident_id": incident["identifier"],
                "maintenance_intake_digest": "approved-digest",
                "executor": "ordinary_development_workflow",
                "maintenance_case_status": "awaiting_observer_verification",
            },
            status="in_review",
        )
        with (
            patch.dict(observer.os.environ, {}, clear=True),
            self.assertRaisesRegex(observer.ObserverError, "Observer Agent identity"),
        ):
            observer.verify_fix(
                cli,
                Namespace(
                    incident=incident["identifier"],
                    result="passed",
                    evidence="passed",
                    deployed_version="v1.2.0",
                    deployment_target="workflow-canary",
                ),
            )

    def test_failed_verification_requires_completed_deployment_state(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "in_fix",
                "logical_status": "in_fix",
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_id": "WOR-CASE",
                "observer_id": "agent-observer",
            },
            status="in_progress",
        )
        cli.add_item(
            "WOR-CASE",
            "project-ops",
            {
                "workflow_object_type": "maintenance_case",
                "incident_id": incident["identifier"],
                "maintenance_intake_digest": "approved-digest",
                "executor": "ordinary_development_workflow",
                "maintenance_case_status": "in_development",
                "logical_status": "in_development",
            },
            status="in_progress",
        )
        with (
            patch.dict(
                observer.os.environ,
                {"MULTICA_AGENT_ID": "agent-observer"},
                clear=True,
            ),
            self.assertRaisesRegex(observer.ObserverError, "completed deployment"),
        ):
            observer.verify_fix(
                cli,
                Namespace(
                    incident=incident["identifier"],
                    result="failed",
                    evidence="not yet deployed",
                    deployed_version=None,
                    deployment_target=None,
                ),
            )

    def test_main_default_audit_uses_read_only_audit_path(self):
        args = Namespace(command="audit", report=False)
        parser = Namespace(parse_args=lambda: args)
        result = {"coverage_complete": True, "health_error": None}
        with (
            patch.object(observer, "parser", return_value=parser),
            patch.object(observer, "build_cli", return_value=object()),
            patch.object(observer, "audit", return_value=result) as audit_call,
            patch.object(observer, "scan") as scan_call,
            patch("builtins.print"),
        ):
            self.assertEqual(observer.main(), 0)
        audit_call.assert_called_once()
        scan_call.assert_not_called()

    def test_verify_fix_closes_case_and_incident(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "awaiting_verification",
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_id": "WOR-CASE",
                "observer_id": "agent-observer",
            },
            status="in_review",
        )
        case = cli.add_item(
            "WOR-CASE",
            "project-ops",
            {
                "workflow_object_type": "maintenance_case",
                "incident_id": incident["identifier"],
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_status": "awaiting_observer_verification",
                "executor": "ordinary_development_workflow",
                "release_version": "v1.2.0-rc.1",
                "release_source_commit": "c" * 40,
                "release_request_digest": "d" * 64,
                "deployment_target": "workflow-canary",
                "deployment_plan_digest": "a" * 64,
                "deployment_journal_sha256": "b" * 64,
                "deployment_record_sha256": "e" * 64,
                "deployed_version": "v1.2.0-rc.1",
            },
            status="in_review",
        )
        deployment_record_sha256 = cli.metadata[case["identifier"]].pop(
            "deployment_record_sha256"
        )
        with (
            patch.dict(
                observer.os.environ,
                {"MULTICA_AGENT_ID": "agent-observer"},
                clear=True,
            ),
            self.assertRaisesRegex(observer.ObserverError, "evidence is incomplete"),
        ):
            observer.verify_fix(
                cli,
                Namespace(
                    incident=incident["identifier"],
                    result="passed",
                    evidence="reproduction and regression tests passed",
                    deployed_version="v1.2.0-rc.1",
                    deployment_target="workflow-canary",
                ),
            )
        cli.metadata[case["identifier"]][
            "deployment_record_sha256"
        ] = deployment_record_sha256
        with patch.dict(
            observer.os.environ, {"MULTICA_AGENT_ID": "agent-observer"}, clear=True
        ):
            result = observer.verify_fix(
                cli,
                Namespace(
                    incident=incident["identifier"],
                    result="passed",
                    evidence="reproduction and regression tests passed",
                    deployed_version="v1.2.0-rc.1",
                    deployment_target="workflow-canary",
                ),
            )
        self.assertEqual(result["result"], "passed")
        self.assertEqual(cli.issue_details[incident["identifier"]]["status"], "done")
        self.assertEqual(cli.issue_details[case["identifier"]]["status"], "done")
        self.assertEqual(cli.metadata[incident["identifier"]]["incident_status"], "resolved")

    def test_successful_verification_repairs_partial_incident_completion(self):
        cli = Phase1CLI()
        incident = cli.add_item(
            "WOR-INC",
            "project-ops",
            {
                "workflow_object_type": "incident",
                "incident_status": "awaiting_verification",
                "logical_status": "awaiting_verification",
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_id": "WOR-CASE",
                "observer_id": "agent-observer",
            },
            status="in_review",
        )
        case = cli.add_item(
            "WOR-CASE",
            "project-ops",
            {
                "workflow_object_type": "maintenance_case",
                "incident_id": incident["identifier"],
                "maintenance_intake_digest": "approved-digest",
                "maintenance_case_status": "awaiting_observer_verification",
                "logical_status": "awaiting_observer_verification",
                "executor": "ordinary_development_workflow",
                "release_version": "v1.2.0",
                "release_source_commit": "c" * 40,
                "release_request_digest": "d" * 64,
                "deployment_target": "workflow-canary",
                "deployment_plan_digest": "a" * 64,
                "deployment_journal_sha256": "b" * 64,
                "deployment_record_sha256": "e" * 64,
                "deployed_version": "v1.2.0",
            },
            status="in_review",
        )
        args = Namespace(
            incident=incident["identifier"],
            result="passed",
            evidence="regression suite passed",
            deployed_version="v1.2.0",
            deployment_target="workflow-canary",
        )
        cli.fail_metadata_keys.add("incident_status")
        with (
            patch.dict(
                observer.os.environ,
                {"MULTICA_AGENT_ID": "agent-observer"},
                clear=True,
            ),
            self.assertRaisesRegex(observer.ObserverError, "incident_status"),
        ):
            observer.verify_fix(cli, args)
        self.assertEqual(
            cli.metadata[case["identifier"]]["logical_status"], "completed"
        )

        with patch.dict(
            observer.os.environ,
            {"MULTICA_AGENT_ID": "agent-observer"},
            clear=True,
        ):
            repaired = observer.verify_fix(cli, args)
            reused = observer.verify_fix(cli, args)
        self.assertEqual(repaired["action"], "repaired")
        self.assertEqual(reused["action"], "reused")
        self.assertEqual(
            cli.metadata[incident["identifier"]]["logical_status"], "resolved"
        )


if __name__ == "__main__":
    unittest.main()
