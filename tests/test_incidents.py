from pathlib import Path
import argparse
import contextlib
import importlib.util
import io
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
            },
            {
                "id": "project-external",
                "title": "External Operations",
                "description": "Owned outside the development workflow.",
                "lead_id": "agent-external",
            },
            {
                "id": "project-managed",
                "title": "Managed Product",
                "description": marker("project.managed-product"),
            },
            {
                "id": "project-squad-owned",
                "title": "Squad-Owned Product",
                "description": "No marker, but managed Squad owns it.",
                "lead_id": "agent-leader",
            },
        ]
        self.agents = [
            {
                "id": "agent-leader",
                "name": "开发队长",
                "instructions": marker("agent.leader"),
            },
            {
                "id": "agent-external",
                "name": "External Owner",
                "instructions": "External process owner.",
            },
        ]
        self.squads = [
            {
                "id": "squad-development",
                "name": "Development Delivery",
                "instructions": marker("squad.development-delivery"),
            },
            {
                "id": "squad-external",
                "name": "External Operations",
                "instructions": "External execution.",
            },
        ]
        self.squad_members = {
            "squad-development": [
                {"member_type": "agent", "member_id": "agent-leader"},
                {"member_type": "member", "member_id": "member-approver"},
            ],
            "squad-external": [
                {"member_type": "agent", "member_id": "agent-external"},
                {"member_type": "member", "member_id": "member-external"},
            ],
        }
        self.created = 0
        self.metadata_set_failures = []

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
        if command[:2] == ("project", "get"):
            return next(item for item in self.projects if item["id"] == args[2])
        if command[:2] == ("agent", "list"):
            return self.agents
        if command[:2] == ("agent", "get"):
            return next(item for item in self.agents if item["id"] == args[2])
        if command[:2] == ("squad", "list"):
            return self.squads
        if command[:2] == ("squad", "get"):
            return next(item for item in self.squads if item["id"] == args[2])
        if command[:3] == ("squad", "member", "list"):
            return self.squad_members.get(args[3], [])
        if command[:3] == ("workspace", "member", "list"):
            return [
                {"id": "member-approver"},
                {"id": "member-external"},
            ]
        if command[:3] == ("user", "profile", "get"):
            return {"id": "member-current"}
        if command[:2] == ("issue", "get"):
            return self.resolve(args[2])
        if command[:3] == ("issue", "metadata", "list"):
            issue = self.resolve(args[3])
            return self.metadata[issue["identifier"]]
        if command[:3] == ("issue", "metadata", "set"):
            issue = self.resolve(args[3])
            failure = (issue["identifier"], flag(args, "--key"))
            if failure in self.metadata_set_failures:
                self.metadata_set_failures.remove(failure)
                raise incidents.IncidentError("simulated metadata write failure")
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
                    "parent_issue_id": None,
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
        "fix_reference_type": None,
        "fix_reference": None,
        "deployment_verification_reference_type": None,
        "deployment_verification_reference": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def create_fix_args(incident, **overrides):
    values = {
        "incident": incident,
        "project": "project-external",
        "assignee_id": None,
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

    def test_create_fix_parser_requires_an_explicit_project(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                incidents.parser().parse_args(
                    [
                        "--workspace",
                        "workspace-test",
                        "create-fix-requirement",
                        "--incident",
                        "INC-1",
                    ]
                )

    def create_external_fix(self, incident_id, **overrides):
        result = incidents.create_fix_requirement(
            self.cli, create_fix_args(incident_id, **overrides)
        )
        requirement_id = result["fix_requirement_id"]
        return requirement_id, result

    def external_close_args(self, incident_id, result="passed", **overrides):
        values = {
            "result": result,
            "source_commit": None,
            "deployment_plan_digest": None,
            "fix_reference_type": "git_commit",
            "fix_reference": "c" * 40,
            "deployment_verification_reference_type": "deployment_record",
            "deployment_verification_reference": "release-2026.08.13/verify-17",
        }
        values.update(overrides)
        return close_args(incident_id, **values)

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

    def test_create_external_fix_is_unassigned_backlog_and_not_protocol_requirement(self):
        incident_id = incidents.report_incident(
            self.cli,
            report_args(
                "REQ-1",
                summary="Token leak",
                evidence="Authorization: Bearer secret-token",
            ),
        )["incident_id"]
        requirement_id, result = self.create_external_fix(incident_id)
        requirement = self.cli.issues[requirement_id]
        metadata = self.cli.metadata[requirement_id]
        self.assertEqual(result["fix_execution_mode"], "external")
        self.assertEqual(requirement["status"], "backlog")
        self.assertEqual(requirement["project_id"], "project-external")
        self.assertIsNone(requirement["assignee_id"])
        self.assertIsNone(requirement["parent_issue_id"])
        self.assertEqual(metadata["workflow_object_type"], "incident_fix_requirement")
        self.assertEqual(metadata["fix_execution_mode"], "external")
        self.assertNotIn("root_requirement_id", metadata)
        self.assertNotIn("protocol_revision", metadata)
        self.assertNotIn("secret-token", requirement["description"])
        self.assertEqual(
            self.cli.metadata[incident_id]["waiting_on"], "external_fix_owner"
        )

    def test_create_external_fix_requires_safe_project_and_assignee(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        with self.assertRaisesRegex(incidents.IncidentError, "Incident Project"):
            self.create_external_fix(incident_id, project="project-incidents")
        with self.assertRaisesRegex(incidents.IncidentError, "managed workflow Project"):
            self.create_external_fix(incident_id, project="project-managed")
        with self.assertRaisesRegex(incidents.IncidentError, "owned by the development Squad"):
            self.create_external_fix(incident_id, project="project-squad-owned")
        self.cli.projects.append(
            {
                "id": "project-unknown-owner",
                "title": "Unknown Owner",
                "description": "Owner is not readable in this Workspace.",
                "lead_id": "unknown-owner",
            }
        )
        with self.assertRaisesRegex(incidents.IncidentError, "owner identity"):
            self.create_external_fix(incident_id, project="project-unknown-owner")
        for assignee in ["squad-development", "agent-leader", "member-approver"]:
            with self.subTest(assignee=assignee):
                with self.assertRaisesRegex(incidents.IncidentError, "development Squad"):
                    self.create_external_fix(incident_id, assignee_id=assignee)
        requirement_id, _ = self.create_external_fix(
            incident_id, assignee_id="member-external"
        )
        self.assertEqual(self.cli.issues[requirement_id]["assignee_id"], "member-external")

    def test_create_external_fix_is_idempotent_and_recovers_partial_binding(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        self.cli.metadata_set_failures.append((incident_id, "fix_requirement_id"))
        with self.assertRaisesRegex(incidents.IncidentError, "metadata write failure"):
            self.create_external_fix(incident_id)
        self.assertEqual(self.cli.created, 2)
        orphan = next(
            key
            for key, metadata in self.cli.metadata.items()
            if metadata.get("workflow_object_type") == "incident_fix_requirement"
        )
        self.assertNotIn("fix_requirement_id", self.cli.metadata[incident_id])
        recovered, result = self.create_external_fix(incident_id)
        self.assertEqual(recovered, orphan)
        self.assertEqual(result["action"], "created_or_recovered")
        created_count = self.cli.created
        again, repeated = self.create_external_fix(incident_id)
        self.assertEqual(again, orphan)
        self.assertEqual(repeated["action"], "reused")
        self.assertEqual(self.cli.created, created_count)

    def test_create_external_fix_reuses_an_existing_binding_when_assignee_is_omitted(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        requirement_id, _ = self.create_external_fix(
            incident_id, assignee_id="member-external"
        )
        repeated_id, result = self.create_external_fix(incident_id)
        self.assertEqual(repeated_id, requirement_id)
        self.assertEqual(result["action"], "reused")

    def test_create_external_fix_rejects_multiple_candidates_and_binding_conflicts(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        description = incidents.external_fix_description(
            incident_id,
            self.cli.issues[incident_id],
            self.cli.metadata[incident_id],
        )
        for identifier in ["FIX-A", "FIX-B"]:
            issue = self.cli.add_issue(
                identifier, status="backlog", project_id="project-external"
            )
            issue["description"] = description
        with self.assertRaisesRegex(incidents.IncidentError, "multiple external"):
            self.create_external_fix(incident_id)

        other = incidents.report_incident(
            self.cli, report_args("REQ-1", rule_id="WF-TEST-OTHER")
        )["incident_id"]
        self.cli.issues.pop("FIX-B")
        self.cli.metadata.pop("FIX-B")
        self.cli.metadata[incident_id]["fix_requirement_id"] = "FIX-A"
        self.cli.metadata["FIX-A"].update(
            {
                "managed_by": incidents.MANAGED_BY,
                "workflow_id": incidents.WORKFLOW_ID,
                "workflow_object_type": "incident_fix_requirement",
                "fix_execution_mode": "external",
                "workflow_incident_id": other,
            }
        )
        with self.assertRaisesRegex(incidents.IncidentError, "workflow_incident_id conflicts"):
            self.create_external_fix(incident_id)

    def test_create_external_fix_rejects_an_orphan_after_an_existing_binding(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        requirement_id, _ = self.create_external_fix(incident_id)
        duplicate = self.cli.add_issue(
            "FIX-ORPHAN", status="backlog", project_id="project-external"
        )
        duplicate["description"] = incidents.external_fix_description(
            incident_id,
            self.cli.issues[incident_id],
            self.cli.metadata[incident_id],
        )
        with self.assertRaisesRegex(incidents.IncidentError, "another external"):
            self.create_external_fix(incident_id)
        self.assertEqual(
            self.cli.metadata[incident_id]["fix_requirement_id"], requirement_id
        )

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

    def test_failed_external_verification_keeps_external_waiting_state(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        requirement_id, _ = self.create_external_fix(incident_id)
        self.cli.issues[requirement_id]["status"] = "done"
        result = incidents.close_incident(
            self.cli,
            self.external_close_args(
                incident_id,
                result="failed",
                evidence="Authorization: Bearer should-not-persist",
                fix_reference_type=None,
                fix_reference=None,
                deployment_verification_reference_type=None,
                deployment_verification_reference=None,
            ),
        )
        self.assertFalse(result["closed"])
        metadata = self.cli.metadata[incident_id]
        self.assertEqual(metadata["waiting_on"], "external_fix_owner")
        self.assertNotIn("should-not-persist", metadata["last_verification_evidence"])

    def test_external_close_requires_structured_references_not_plan_digest(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        requirement_id, _ = self.create_external_fix(incident_id)
        self.cli.issues[requirement_id]["status"] = "done"
        with self.assertRaisesRegex(incidents.IncidentError, "fix reference type"):
            incidents.close_incident(
                self.cli,
                self.external_close_args(incident_id, fix_reference_type=None),
            )
        with self.assertRaisesRegex(incidents.IncidentError, "40-character commit"):
            incidents.close_incident(
                self.cli,
                self.external_close_args(incident_id, fix_reference="short"),
            )
        with self.assertRaisesRegex(incidents.IncidentError, "must not contain secrets"):
            incidents.close_incident(
                self.cli,
                self.external_close_args(
                    incident_id,
                    fix_reference_type="artifact_version",
                    fix_reference="Authorization: Bearer secret-token",
                ),
            )
        with self.assertRaisesRegex(incidents.IncidentError, "single immutable reference"):
            incidents.close_incident(
                self.cli,
                self.external_close_args(
                    incident_id,
                    fix_reference_type="artifact_version",
                    fix_reference="mutable release label",
                ),
            )
        result = incidents.close_incident(
            self.cli,
            self.external_close_args(
                incident_id,
                fix_reference_type="artifact_version",
                fix_reference="workflow-runtime-2.0.0-dev.7",
                evidence="verified; Cookie: session-secret",
            ),
        )
        self.assertTrue(result["closed"])
        metadata = self.cli.metadata[incident_id]
        self.assertEqual(metadata["immutable_fix_reference_type"], "artifact_version")
        self.assertNotIn("deployment_plan_digest", metadata)
        self.assertNotIn("session-secret", metadata["last_verification_evidence"])

    def test_passed_verification_requires_fix_commit_and_plan_digest(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        with self.assertRaisesRegex(incidents.IncidentError, "link a fix Requirement"):
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

    def test_link_fix_supports_external_and_rejects_reverse_conflicts(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        external = self.cli.add_issue(
            "FIX-EXTERNAL", status="backlog", project_id="project-external"
        )
        self.cli.metadata["FIX-EXTERNAL"].update(
            {
                "managed_by": incidents.MANAGED_BY,
                "workflow_id": incidents.WORKFLOW_ID,
                "workflow_object_type": "incident_fix_requirement",
                "fix_execution_mode": "external",
            }
        )
        result = incidents.link_fix(
            self.cli, link_args(incident_id, external["identifier"])
        )
        self.assertEqual(result["fix_execution_mode"], "external")
        self.assertEqual(
            self.cli.metadata["FIX-EXTERNAL"]["workflow_incident_id"], incident_id
        )

        other = incidents.report_incident(
            self.cli, report_args("REQ-1", rule_id="WF-LINK-OTHER")
        )["incident_id"]
        legacy = self.bind_fix("REQ-LEGACY-CONFLICT")
        self.cli.metadata[legacy]["workflow_incident_id"] = incident_id
        with self.assertRaisesRegex(incidents.IncidentError, "another Incident"):
            incidents.link_fix(self.cli, link_args(other, legacy))

    def test_link_fix_rejects_an_existing_incident_owner_even_without_reverse_metadata(self):
        first = incidents.report_incident(
            self.cli, report_args("REQ-1", rule_id="WF-OWNER-FIRST")
        )["incident_id"]
        second = incidents.report_incident(
            self.cli, report_args("REQ-1", rule_id="WF-OWNER-SECOND")
        )["incident_id"]
        legacy = self.bind_fix("REQ-OWNER-CONFLICT")
        self.cli.metadata[first]["fix_requirement_id"] = legacy
        with self.assertRaisesRegex(incidents.IncidentError, "owned by another Incident"):
            incidents.link_fix(self.cli, link_args(second, legacy))

    def test_external_link_rejects_development_tree_metadata(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        self.cli.add_issue(
            "FIX-BAD", status="backlog", project_id="project-external"
        )
        self.cli.metadata["FIX-BAD"].update(
            {
                "managed_by": incidents.MANAGED_BY,
                "workflow_id": incidents.WORKFLOW_ID,
                "workflow_object_type": "incident_fix_requirement",
                "fix_execution_mode": "external",
                "root_requirement_id": "FIX-BAD",
            }
        )
        with self.assertRaisesRegex(incidents.IncidentError, "development-delivery metadata"):
            incidents.link_fix(self.cli, link_args(incident_id, "FIX-BAD"))

    def test_external_link_requires_explicit_type_and_mode_without_creation_marker(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        self.cli.add_issue(
            "FIX-UNTYPED", status="backlog", project_id="project-external"
        )
        self.cli.metadata["FIX-UNTYPED"].update(
            {
                "managed_by": incidents.MANAGED_BY,
                "workflow_id": incidents.WORKFLOW_ID,
                "workflow_incident_id": incident_id,
            }
        )
        with self.assertRaisesRegex(incidents.IncidentError, "protocol v4 Requirement"):
            incidents.link_fix(self.cli, link_args(incident_id, "FIX-UNTYPED"))

    def test_external_link_recovers_a_marker_only_partial_create(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        orphan = self.cli.add_issue(
            "FIX-MARKER", status="backlog", project_id="project-external"
        )
        orphan["description"] = incidents.external_fix_description(
            incident_id,
            self.cli.issues[incident_id],
            self.cli.metadata[incident_id],
        )
        result = incidents.link_fix(self.cli, link_args(incident_id, "FIX-MARKER"))
        self.assertEqual(result["fix_execution_mode"], "external")
        self.assertEqual(
            self.cli.metadata["FIX-MARKER"]["workflow_object_type"],
            "incident_fix_requirement",
        )

    def test_external_link_revalidates_project_and_assignee_safety(self):
        incident_id = incidents.report_incident(
            self.cli, report_args("REQ-1")
        )["incident_id"]
        self.cli.add_issue(
            "FIX-UNSAFE",
            status="backlog",
            project_id="project-managed",
        )["assignee_id"] = "agent-leader"
        self.cli.metadata["FIX-UNSAFE"].update(
            {
                "managed_by": incidents.MANAGED_BY,
                "workflow_id": incidents.WORKFLOW_ID,
                "workflow_object_type": "incident_fix_requirement",
                "fix_execution_mode": "external",
            }
        )
        with self.assertRaises(incidents.IncidentError):
            incidents.link_fix(self.cli, link_args(incident_id, "FIX-UNSAFE"))

    def test_close_restores_only_sources_still_owned_by_incident(self):
        self.cli.add_issue("TASK-A", status="todo", parent_issue_id="REQ-1")
        self.cli.add_issue("TASK-B", status="todo", parent_issue_id="REQ-1")
        for task in ["TASK-A", "TASK-B"]:
            incidents.bind_workflow_issue(
                self.cli, bind_args(task, "development_task")
            )
        first = incidents.report_incident(
            self.cli, report_args("TASK-A", dedupe_key="shared", block_source=True)
        )
        incidents.report_incident(
            self.cli, report_args("TASK-B", dedupe_key="shared", block_source=True)
        )
        self.cli.metadata["TASK-B"]["workflow_blocked_by_incident_id"] = "INC-OTHER"
        fix = self.bind_fix("REQ-FIX-OWNERSHIP")
        incidents.link_fix(self.cli, link_args(first["incident_id"], fix))
        result = incidents.close_incident(
            self.cli, close_args(first["incident_id"])
        )
        self.assertEqual(result["sources_restored"], 1)
        self.assertEqual(self.cli.issues["TASK-A"]["status"], "todo")
        self.assertEqual(self.cli.issues["TASK-B"]["status"], "blocked")

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
