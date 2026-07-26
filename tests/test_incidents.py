from pathlib import Path
import argparse
import importlib.util
import json
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
INCIDENTS_PATH = ROOT / "skills/multica-workflow-incidents/scripts/incidents.py"
SPEC = importlib.util.spec_from_file_location("workflow_incidents", INCIDENTS_PATH)
incidents = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = incidents
SPEC.loader.exec_module(incidents)


def marker(object_key: str) -> str:
    return (
        "<!-- multica-workflow\n"
        "managed_by=multica-dev-workflow\n"
        "workflow_id=development-delivery\n"
        f"object_key={object_key}\n"
        "spec_hash=test\n"
        "-->\n"
    )


def flag(args, name, default=None):
    try:
        return args[args.index(name) + 1]
    except ValueError:
        return default


class FakeCLI:
    def __init__(self):
        self.workspace_id = "workspace-test"
        self.issues = {}
        self.metadata = {}
        self.comments = {}
        self.projects = [
            {
                "id": "project-incidents",
                "title": "工作流问题",
                "description": marker("project.workflow-incidents"),
            }
        ]
        self.agents = [
            {
                "id": "agent-leader",
                "name": "开发队长",
                "instructions": marker("agent.leader"),
            }
        ]
        self.created = 0

    def add_issue(
        self,
        identifier,
        status="todo",
        parent_issue_id=None,
        project_id="project-product",
    ):
        issue = {
            "id": f"id-{identifier}",
            "identifier": identifier,
            "title": identifier,
            "description": "",
            "status": status,
            "project_id": project_id,
            "parent_issue_id": parent_issue_id,
        }
        self.issues[identifier] = issue
        self.metadata.setdefault(identifier, {})
        self.comments.setdefault(identifier, [])
        return issue

    def resolve(self, reference):
        if reference in self.issues:
            return self.issues[reference]
        return next(
            item for item in self.issues.values() if item.get("id") == reference
        )

    def json(self, args, input_text=None):
        args = list(args)
        command = tuple(args)
        if command[:2] == ("project", "list"):
            return self.projects
        if command[:2] == ("agent", "list"):
            return self.agents
        if command[:2] == ("issue", "get"):
            return self.resolve(args[2])
        if command[:3] == ("issue", "metadata", "list"):
            issue = self.resolve(args[3])
            return self.metadata[issue["identifier"]]
        if command[:3] == ("issue", "metadata", "set"):
            issue = self.resolve(args[3])
            value = flag(args, "--value", "")
            value_type = flag(args, "--type", "string")
            if value_type == "bool":
                value = value == "true"
            elif value_type == "number":
                value = float(value) if "." in value else int(value)
            self.metadata[issue["identifier"]][flag(args, "--key")] = value
            return {"ok": True}
        if command[:2] == ("issue", "list"):
            project_id = flag(args, "--project")
            filters = []
            for index, item in enumerate(args):
                if item == "--metadata":
                    key, value = args[index + 1].split("=", 1)
                    filters.append((key, value))
            result = []
            for issue in self.issues.values():
                if project_id and issue.get("project_id") != project_id:
                    continue
                metadata = self.metadata[issue["identifier"]]
                if all(str(metadata.get(key, "")) == value for key, value in filters):
                    result.append(issue)
            offset = int(flag(args, "--offset", "0"))
            limit = int(flag(args, "--limit", "100"))
            return result[offset : offset + limit]
        if command[:2] == ("issue", "create"):
            self.created += 1
            identifier = f"INC-{self.created}"
            issue = self.add_issue(
                identifier,
                status=flag(args, "--status", "todo"),
                project_id=flag(args, "--project"),
            )
            issue.update(
                {
                    "title": flag(args, "--title"),
                    "description": input_text or "",
                    "priority": flag(args, "--priority"),
                    "assignee_id": flag(args, "--assignee-id"),
                }
            )
            return issue
        if command[:2] == ("issue", "update"):
            issue = self.resolve(args[2])
            issue["status"] = flag(args, "--status", issue["status"])
            issue["priority"] = flag(args, "--priority", issue.get("priority"))
            return issue
        if command[:3] == ("issue", "comment", "add"):
            issue = self.resolve(args[3])
            self.comments[issue["identifier"]].append(input_text or "")
            return {"id": f"comment-{len(self.comments[issue['identifier']])}"}
        raise AssertionError(f"unexpected command: {command}")


def bind_args(issue, object_type="requirement", root_requirement_id=None):
    return argparse.Namespace(
        issue=issue,
        object_type=object_type,
        root_requirement_id=root_requirement_id,
        created_by_role="leader",
    )


def report_args(source, **overrides):
    values = {
        "source_issue": source,
        "rule_id": "WF-TEST-001",
        "severity": "medium",
        "summary": "Workflow gate failed",
        "expected": "Gate is enforced",
        "actual": "Gate was skipped",
        "evidence": "Authorization: Bearer secret-token",
        "entity": None,
        "dedupe_key": None,
        "reporter_agent_id": "agent-test",
        "reporter_role": "developer",
        "block_source": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def link_args(incident, requirement):
    return argparse.Namespace(incident=incident, requirement=requirement)


def close_args(incident, result="passed", **overrides):
    values = {
        "incident": incident,
        "result": result,
        "evidence": "verified in workspace",
        "source_commit": "a" * 40,
        "deployment_plan_digest": "b" * 64,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.cli = FakeCLI()
        self.cli.add_issue("REQ-1")
        incidents.bind_workflow_issue(self.cli, bind_args("REQ-1"))

    def bind_fix(self, identifier="REQ-FIX"):
        self.cli.add_issue(identifier, status="done")
        incidents.bind_workflow_issue(self.cli, bind_args(identifier))
        return identifier

    def test_redaction_handles_nested_values_headers_and_private_keys(self):
        value = {
            "safe": "ok",
            "token": {"nested": "secret"},
            "items": [
                "Authorization: Bearer abc.def",
                "-----BEGIN PRIVATE KEY-----\nsecret\n-----END PRIVATE KEY-----",
            ],
        }
        redacted = incidents.redact(value)
        self.assertEqual(redacted["token"], "<redacted>")
        self.assertIn("Authorization: <redacted>", redacted["items"][0])
        self.assertIn("<redacted-private-key>", redacted["items"][1])

    def test_bind_derives_root_and_rejects_conflicts_or_unknown_types(self):
        self.cli.add_issue("TASK-1", parent_issue_id="REQ-1")
        result = incidents.bind_workflow_issue(
            self.cli, bind_args("TASK-1", "development_task")
        )
        self.assertEqual(result["root_requirement_id"], "REQ-1")
        self.assertEqual(result["protocol_revision"], "v4")
        self.cli.metadata["TASK-1"]["workflow_id"] = "other"
        with self.assertRaisesRegex(incidents.IncidentError, "conflicts"):
            incidents.bind_workflow_issue(
                self.cli, bind_args("TASK-1", "development_task")
            )
        with self.assertRaisesRegex(incidents.IncidentError, "not part of protocol v4"):
            incidents.bind_workflow_issue(self.cli, bind_args("REQ-1", "custom"))

    def test_report_creates_then_deduplicates_without_regressing_fix_state(self):
        created = incidents.report_incident(self.cli, report_args("REQ-1", severity="high"))
        self.assertEqual(created["action"], "created")
        incident_id = created["incident_id"]
        self.assertNotIn("secret-token", self.cli.issues[incident_id]["description"])
        fix = self.bind_fix()
        incidents.link_fix(self.cli, link_args(incident_id, fix))
        updated = incidents.report_incident(self.cli, report_args("REQ-1", severity="low"))
        self.assertEqual(updated["incident_id"], incident_id)
        metadata = self.cli.metadata[incident_id]
        self.assertEqual(metadata["incident_status"], "in_fix")
        self.assertEqual(metadata["incident_severity"], "high")
        self.assertEqual(metadata["fix_requirement_id"], fix)
        evidence = json.loads(metadata["incident_evidence_log"])
        self.assertEqual(len(evidence), 1)

    def test_deduplicated_report_updates_issue_priority_only_upward(self):
        created = incidents.report_incident(
            self.cli, report_args("REQ-1", severity="low")
        )
        incident_id = created["incident_id"]
        incidents.report_incident(
            self.cli, report_args("REQ-1", severity="urgent")
        )
        self.assertEqual(self.cli.issues[incident_id]["priority"], "urgent")
        incidents.report_incident(
            self.cli, report_args("REQ-1", severity="medium")
        )
        self.assertEqual(self.cli.issues[incident_id]["priority"], "urgent")

    def test_blocked_sources_are_all_restored_after_passed_verification(self):
        self.cli.add_issue("TASK-A", status="todo", parent_issue_id="REQ-1")
        self.cli.add_issue("TASK-B", status="in_progress", parent_issue_id="REQ-1")
        incidents.bind_workflow_issue(
            self.cli, bind_args("TASK-A", "development_task")
        )
        incidents.bind_workflow_issue(
            self.cli, bind_args("TASK-B", "development_task")
        )
        first = incidents.report_incident(
            self.cli,
            report_args("TASK-A", dedupe_key="same-root", block_source=True),
        )
        incidents.report_incident(
            self.cli,
            report_args("TASK-B", dedupe_key="same-root", block_source=True),
        )
        self.assertEqual(self.cli.issues["TASK-A"]["status"], "blocked")
        self.assertEqual(self.cli.issues["TASK-B"]["status"], "blocked")
        fix = self.bind_fix()
        incidents.link_fix(self.cli, link_args(first["incident_id"], fix))
        result = incidents.close_incident(
            self.cli, close_args(first["incident_id"])
        )
        self.assertEqual(result["sources_restored"], 2)
        self.assertEqual(self.cli.issues["TASK-A"]["status"], "todo")
        self.assertEqual(self.cli.issues["TASK-B"]["status"], "in_progress")

    def test_failed_verification_keeps_incident_in_fix(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        incidents.link_fix(self.cli, link_args(incident_id, self.bind_fix()))
        result = incidents.close_incident(
            self.cli,
            close_args(
                incident_id,
                result="failed",
                source_commit=None,
                deployment_plan_digest=None,
            ),
        )
        self.assertFalse(result["closed"])
        self.assertEqual(self.cli.metadata[incident_id]["incident_status"], "in_fix")
        self.assertEqual(self.cli.issues[incident_id]["status"], "in_progress")

    def test_passed_verification_requires_fix_commit_and_plan_digest(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        with self.assertRaisesRegex(incidents.IncidentError, "link an ordinary"):
            incidents.close_incident(self.cli, close_args(incident_id))
        incidents.link_fix(self.cli, link_args(incident_id, self.bind_fix()))
        with self.assertRaisesRegex(incidents.IncidentError, "source commit"):
            incidents.close_incident(
                self.cli, close_args(incident_id, source_commit="short")
            )
        with self.assertRaisesRegex(incidents.IncidentError, "Plan digest"):
            incidents.close_incident(
                self.cli, close_args(incident_id, deployment_plan_digest="short")
            )

    def test_report_after_closure_creates_recurrence(self):
        first = incidents.report_incident(self.cli, report_args("REQ-1"))
        incidents.link_fix(self.cli, link_args(first["incident_id"], self.bind_fix()))
        incidents.close_incident(self.cli, close_args(first["incident_id"]))
        second = incidents.report_incident(self.cli, report_args("REQ-1"))
        self.assertNotEqual(second["incident_id"], first["incident_id"])
        self.assertEqual(
            self.cli.metadata[second["incident_id"]]["recurrence_of"],
            first["incident_id"],
        )

    def test_link_fix_rejects_replacement_requirement(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        incidents.link_fix(self.cli, link_args(incident_id, self.bind_fix("REQ-FIX-1")))
        self.bind_fix("REQ-FIX-2")
        with self.assertRaisesRegex(incidents.IncidentError, "different fix"):
            incidents.link_fix(self.cli, link_args(incident_id, "REQ-FIX-2"))

    def test_blocking_does_not_overwrite_another_incident(self):
        self.cli.metadata["REQ-1"]["workflow_blocked_by_incident_id"] = "INC-OTHER"
        issue_count = len(self.cli.issues)
        with self.assertRaisesRegex(incidents.IncidentError, "another Incident"):
            incidents.report_incident(
                self.cli, report_args("REQ-1", block_source=True)
            )
        self.assertEqual(len(self.cli.issues), issue_count)
        self.assertNotIn("workflow_incident_id", self.cli.metadata["REQ-1"])

    def test_protocol_v3_incident_is_not_mutated(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        self.cli.metadata[incident_id]["protocol_revision"] = "v3"
        with self.assertRaisesRegex(incidents.IncidentError, "managed workflow Incident"):
            incidents.link_fix(
                self.cli, link_args(incident_id, self.bind_fix())
            )


if __name__ == "__main__":
    unittest.main()
