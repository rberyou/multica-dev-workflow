from pathlib import Path
import argparse
import importlib.util
import json
import copy
import os
import subprocess
import tempfile
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
        self.transition_write_count = 0
        self.fail_on_transition_write = None
        self.incident_close_write_count = 0
        self.fail_on_incident_close_write = None

    def before_transition_write(self, issue, key):
        if self.metadata.get(issue["identifier"], {}).get("workflow_object_type") == "incident":
            if key in {
                "status",
                "waiting_on",
                "last_verification_result",
                "last_verification_evidence",
                "verified_at",
                "verified_by",
                "fixed_source_commit",
                "deployment_plan_digest",
                "incident_status",
            }:
                self.incident_close_write_count += 1
                if (
                    self.incident_close_write_count
                    == self.fail_on_incident_close_write
                ):
                    raise incidents.IncidentError(
                        "injected Incident close write failure"
                    )
            return
        if key not in {
            "status",
            "workflow_block_transition_record",
            "workflow_blocked_by_incident_id",
            "workflow_blocked_previous_status",
            "waiting_on",
            "blocked_reason",
        }:
            return
        self.transition_write_count += 1
        if self.transition_write_count == self.fail_on_transition_write:
            raise incidents.IncidentError("injected transition write failure")

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
            self.before_transition_write(issue, flag(args, "--key"))
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
            if "--status" in args:
                self.before_transition_write(issue, "status")
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


def integration_snapshot() -> dict:
    return {
        "context": {
            "workspace_id": "workspace-test",
            "squad_id": "squad-test",
            "roster_complete": True,
            "roster": [
                {
                    "agent_id": "integrator-1",
                    "member_type": "agent",
                    "role_key": "integrator",
                    "active": True,
                    "archived": False,
                },
                {
                    "agent_id": "reviewer-1",
                    "member_type": "agent",
                    "role_key": "code_reviewer",
                    "active": True,
                    "archived": False,
                },
            ],
        },
        "issue": {
            "issue_id": "IV-1",
            "status": "in_progress",
            "workflow_id": "development-delivery",
            "protocol_revision": "v4",
            "workflow_object_type": "integration_validation",
            "workflow_instance_id": "workspace-test",
            "assignee_id": "integrator-1",
            "original_owner_id": "integrator-1",
            "reviewer_id": "reviewer-1",
            "plan_revision": 2,
            "delivery_policy_digest": "6" * 64,
            "base_commit_sha": "1" * 40,
            "reviewed_commit_sha": "2" * 40,
            "metadata_keys": [f"existing_{index}" for index in range(42)],
            "workflow_blocked_by_incident_id": "",
        },
        "dependency": {"satisfied": True, "contract": "done:T-1"},
        "lease": {
            "scope": "requirement",
            "state": "held",
            "owner_issue_id": "IV-1",
            "owner_agent_id": "integrator-1",
        },
        "used_review_comment_ids": [],
        "used_review_comment_ids_complete": True,
    }


def apply_integration_result(snapshot: dict, result: dict) -> dict:
    updated = copy.deepcopy(snapshot)
    updated["issue"].update(result["metadata_updates"])
    updated["issue"]["metadata_keys"] = sorted(
        set(updated["issue"]["metadata_keys"]) | set(result["metadata_updates"])
    )
    if result["assignee_write"]:
        updated["issue"]["assignee_id"] = result["assignee_write"]
    return updated


def apply_integration_block_write(snapshot: dict, write: dict) -> dict:
    updated = copy.deepcopy(snapshot)
    if write["kind"] == "status":
        updated["issue"]["status"] = write["value"]
    else:
        updated["issue"][write["key"]] = write["value"]
        updated["issue"]["metadata_keys"] = sorted(
            set(updated["issue"]["metadata_keys"]) | {write["key"]}
        )
    return updated


def apply_integration_block_writes(snapshot: dict, writes: list[dict]) -> dict:
    updated = copy.deepcopy(snapshot)
    for write in writes:
        updated = apply_integration_block_write(updated, write)
    return updated


def handoff_evidence(comment="handoff-1", run="run-1", outcome="queued", attempt=1):
    return {
        "issue_id": "IV-1",
        "author_type": "agent",
        "author_id": "integrator-1",
        "mentioned_agent_id": "reviewer-1",
        "comment_id": comment,
        "created_at": "2026-08-11T10:00:00Z",
        "trigger_run_id": run,
        "attempt": attempt,
        "max_attempts": 3,
        "previous_attempts_complete": True,
        "previous_attempts": [
            {
                "attempt": index,
                "comment_id": f"prior-handoff-{index}",
                "trigger_run_id": f"prior-run-{index}",
                "trigger_outcome": "lost" if index % 2 else "busy",
            }
            for index in range(1, attempt)
        ],
        "trigger_outcomes": [
            {
                "recipient_id": "reviewer-1",
                "status": outcome,
                "run_id": run,
            }
        ],
    }


def apply_block_write_to_snapshot(snapshot: dict, write: dict) -> dict:
    updated = copy.deepcopy(snapshot)
    if write["kind"] == "status":
        updated["source"]["status"] = write["value"]
    else:
        updated["source"][write["key"]] = write["value"]
        updated["source"]["metadata_keys"] = sorted(
            set(updated["source"]["metadata_keys"]) | {write["key"]}
        )
    return updated


class IncidentTests(unittest.TestCase):
    def setUp(self):
        self.cli = FakeCLI()
        self.cli.add_issue("REQ-1")
        incidents.bind_workflow_issue(self.cli, bind_args("REQ-1"))

    def bind_fix(self, identifier="REQ-FIX"):
        self.cli.add_issue(identifier, status="done")
        incidents.bind_workflow_issue(self.cli, bind_args(identifier))
        return identifier

    def test_original_cli_commands_remain_available(self):
        command_choices = next(
            action.choices
            for action in incidents.parser()._actions
            if isinstance(getattr(action, "choices", None), dict)
        )
        self.assertTrue(
            {"bind-workflow-issue", "report", "link-fix", "close"}.issubset(
                command_choices
            )
        )

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

    def test_integration_review_prepare_start_handoff_and_approve_share_current_tuple(self):
        snapshot = integration_snapshot()
        prepared = incidents.integration_review_transition(snapshot, "prepare")
        self.assertTrue(prepared["allowed"])
        self.assertEqual(prepared["assignee_write"], None)
        snapshot = apply_integration_result(snapshot, prepared)

        started = incidents.integration_review_transition(snapshot, "start")
        self.assertTrue(started["allowed"])
        snapshot = apply_integration_result(snapshot, started)
        snapshot["handoff"] = handoff_evidence()
        handed_off = incidents.integration_review_transition(snapshot, "handoff")
        self.assertTrue(handed_off["allowed"])
        snapshot = apply_integration_result(snapshot, handed_off)
        record = incidents.decode_metadata_record(
            snapshot["issue"]["integration_review_role_record"],
            "role",
            "integration_review_role",
        )
        snapshot["review"] = {
            "issue_id": "IV-1",
            "author_type": "agent",
            "author_id": "reviewer-1",
            "comment_id": "review-1",
            "created_at": "2026-08-11T10:01:00Z",
            "verdict": "APPROVED",
            "review_epoch_id": record["review_epoch_id"],
            "trigger_comment_id": "handoff-1",
            "source_run_id": "run-1",
        }
        approved = incidents.integration_review_transition(snapshot, "approve")
        self.assertTrue(approved["allowed"])
        approved_record = incidents.decode_metadata_record(
            approved["metadata_updates"]["integration_review_role_record"],
            "role",
            "integration_review_role",
        )
        self.assertEqual(approved_record["state"], "approved")
        self.assertEqual(approved_record["review_author_id"], "reviewer-1")
        self.assertEqual(approved["status_write"], None)

    def test_repeated_start_and_recover_do_not_regress_a_current_review_epoch(self):
        snapshot = integration_snapshot()
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "prepare")
        )
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "start")
        )
        snapshot["handoff"] = handoff_evidence()
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "handoff")
        )
        before = snapshot["issue"]["integration_review_role_record"]
        repeated = incidents.integration_review_transition(snapshot, "start")
        self.assertTrue(repeated["allowed"])
        self.assertEqual(repeated["outcome"], "already_started")
        self.assertEqual(repeated["metadata_updates"], {})
        self.assertEqual(snapshot["issue"]["integration_review_role_record"], before)

        recovery = integration_snapshot()
        recovery["issue"].update(
            {
                "status": "blocked",
                "assignee_id": "reviewer-1",
                "original_owner_id": "reviewer-1",
                "reviewer_id": "reviewer-1",
                "workflow_blocked_by_incident_id": "INC-1",
                "workflow_blocked_previous_status": "in_progress",
                "waiting_on": "workflow_fix",
                "blocked_reason": "workflow Incident INC-1",
            }
        )
        recovery["lease"] = {
            "current": {
                "scope": "requirement",
                "state": "released",
                "owner_issue_id": "",
                "owner_agent_id": "",
            },
            "target": {
                "scope": "requirement",
                "state": "held",
                "owner_issue_id": "IV-1",
                "owner_agent_id": "integrator-1",
            },
        }
        recovery_lease = copy.deepcopy(recovery["lease"])
        recovery["recovery"] = {"incident_id": "INC-1", "recovery_id": "recover-1"}
        recovery = apply_integration_result(
            recovery, incidents.integration_review_transition(recovery, "recover")
        )
        recovery["lease"] = copy.deepcopy(recovery_lease["target"])
        recovery = apply_integration_result(
            recovery, incidents.integration_review_transition(recovery, "start")
        )
        recovery["handoff"] = handoff_evidence()
        recovery = apply_integration_result(
            recovery, incidents.integration_review_transition(recovery, "handoff")
        )
        current = recovery["issue"]["integration_review_role_record"]
        recovery["lease"] = recovery_lease
        repeated_recovery = incidents.integration_review_transition(recovery, "recover")
        self.assertTrue(repeated_recovery["allowed"])
        self.assertEqual(repeated_recovery["outcome"], "already_recovered")
        self.assertEqual(
            repeated_recovery["metadata_updates"]["integration_review_role_record"],
            current,
        )

    def test_integration_review_rejects_missing_duplicate_archived_or_colliding_roles(self):
        cases = {}
        missing = integration_snapshot()
        missing["context"]["roster"] = missing["context"]["roster"][:1]
        cases["missing reviewer"] = missing
        duplicate = integration_snapshot()
        duplicate["context"]["roster"].append(
            {
                "agent_id": "reviewer-2",
                "member_type": "agent",
                "role_key": "code_reviewer",
                "active": True,
                "archived": False,
            }
        )
        cases["duplicate reviewer"] = duplicate
        archived = integration_snapshot()
        archived["context"]["roster"][1]["archived"] = True
        cases["archived reviewer"] = archived
        incomplete = integration_snapshot()
        incomplete["context"]["roster_complete"] = False
        cases["incomplete roster inventory"] = incomplete
        collision = integration_snapshot()
        collision["context"]["roster"][1]["agent_id"] = "integrator-1"
        cases["owner reviewer collision"] = collision
        for name, snapshot in cases.items():
            with self.subTest(name=name):
                result = incidents.integration_review_transition(snapshot, "prepare")
                self.assertFalse(result["allowed"])
                self.assertEqual(result["metadata_updates"], {})
                self.assertNotIn("approval", " ".join(result["block_metadata_updates"]))

    def test_integration_review_rejects_roster_plan_digest_sha_dependency_and_lease_drift(self):
        base = integration_snapshot()
        base = apply_integration_result(
            base, incidents.integration_review_transition(base, "prepare")
        )
        cases = []
        roster = copy.deepcopy(base)
        roster["context"]["roster"].append(
            {
                "agent_id": "developer-1",
                "member_type": "agent",
                "role_key": "developer",
                "active": True,
                "archived": False,
            }
        )
        cases.append(("roster", roster))
        for key, value in (
            ("plan_revision", 3),
            ("delivery_policy_digest", "a" * 64),
            ("base_commit_sha", "b" * 40),
            ("reviewed_commit_sha", "c" * 40),
        ):
            changed = copy.deepcopy(base)
            changed["issue"][key] = value
            cases.append((key, changed))
        dependency = copy.deepcopy(base)
        dependency["dependency"]["contract"] = "done:T-2"
        cases.append(("dependency", dependency))
        lease = copy.deepcopy(base)
        lease["lease"]["owner_agent_id"] = "reviewer-1"
        cases.append(("lease", lease))
        for name, snapshot in cases:
            with self.subTest(name=name):
                result = incidents.integration_review_transition(snapshot, "start")
                self.assertFalse(result["allowed"])
                self.assertEqual(result["metadata_updates"], {})

    def test_review_handoff_requires_confirmed_trigger_with_bounded_retry(self):
        for outcome in incidents.HANDOFF_OUTCOMES:
            with self.subTest(outcome=outcome):
                snapshot = integration_snapshot()
                snapshot = apply_integration_result(
                    snapshot,
                    incidents.integration_review_transition(snapshot, "prepare"),
                )
                snapshot = apply_integration_result(
                    snapshot,
                    incidents.integration_review_transition(snapshot, "start"),
                )
                snapshot["handoff"] = handoff_evidence(outcome=outcome)
                result = incidents.integration_review_transition(snapshot, "handoff")
                self.assertTrue(result["allowed"])

        snapshot["handoff"] = handoff_evidence(attempt=1)
        snapshot["handoff"]["trigger_outcomes"] = []
        retry = incidents.integration_review_transition(snapshot, "handoff")
        self.assertFalse(retry["allowed"])
        self.assertTrue(retry["retry_required"])
        self.assertEqual(retry["block_metadata_updates"], {})

        snapshot["handoff"] = handoff_evidence(attempt=3)
        snapshot["handoff"]["trigger_outcomes"] = []
        exhausted = incidents.integration_review_transition(snapshot, "handoff")
        self.assertFalse(exhausted["allowed"])
        self.assertTrue(exhausted["retries_exhausted"])
        self.assertEqual(exhausted["block_status_write"], "blocked")
        self.assertGreater(len(exhausted["block_writes"]), 1)
        self.assertEqual(exhausted["block_writes"][1]["kind"], "status")
        self.assertEqual(exhausted["block_writes"][1]["value"], "blocked")

        reset = copy.deepcopy(snapshot)
        reset["handoff"] = handoff_evidence(outcome="busy", attempt=1)
        reset["handoff"]["previous_attempts"] = [
            {"comment_id": "prior-1", "trigger_run_id": "prior-run-1"}
        ]
        rejected = incidents.integration_review_transition(reset, "handoff")
        self.assertFalse(rejected["allowed"])
        self.assertIn(
            "handoff attempt does not follow canonical retry history",
            rejected["reasons"],
        )

        blocked = apply_integration_block_writes(snapshot, exhausted["block_writes"])
        blocked["handoff"] = handoff_evidence(outcome="queued", attempt=1)
        recovered = incidents.integration_review_transition(blocked, "handoff")
        self.assertTrue(recovered["allowed"])
        self.assertEqual(recovered["block_status_write"], "in_progress")
        self.assertEqual(
            recovered["block_metadata_updates"],
            {
                "waiting_on": "",
                "blocked_reason": "",
                "integration_review_previous_status": "",
            },
        )
        self.assertEqual(
            exhausted["block_metadata_updates"]["waiting_on"],
            "integration_review_trigger",
        )

        snapshot["issue"]["workflow_blocked_by_incident_id"] = "INC-1"
        preserved = incidents.integration_review_transition(snapshot, "handoff")
        self.assertTrue(preserved["incident_blocker_preserved"])
        self.assertEqual(preserved["block_metadata_updates"], {})
        self.assertIsNone(preserved["block_status_write"])
        self.assertEqual(preserved["block_writes"], [])

        skipped_start = integration_snapshot()
        skipped_start = apply_integration_result(
            skipped_start,
            incidents.integration_review_transition(skipped_start, "prepare"),
        )
        skipped_start["handoff"] = handoff_evidence()
        rejected = incidents.integration_review_transition(skipped_start, "handoff")
        self.assertFalse(rejected["allowed"])
        self.assertIn("state is invalid", " ".join(rejected["reasons"]))

        duplicate_outcome = copy.deepcopy(snapshot)
        duplicate_outcome["issue"]["workflow_blocked_by_incident_id"] = ""
        duplicate_outcome["handoff"] = handoff_evidence()
        duplicate_outcome["handoff"]["trigger_outcomes"].append(
            {
                "recipient_id": "reviewer-1",
                "status": "busy",
                "run_id": "run-1",
            }
        )
        rejected = incidents.integration_review_transition(
            duplicate_outcome, "handoff"
        )
        self.assertFalse(rejected["allowed"])
        self.assertIn("duplicate or conflicting", " ".join(rejected["reasons"]))

        incomplete_history = copy.deepcopy(snapshot)
        incomplete_history["issue"]["workflow_blocked_by_incident_id"] = ""
        incomplete_history["handoff"] = handoff_evidence(outcome="busy", attempt=2)
        incomplete_history["handoff"]["previous_attempts_complete"] = False
        rejected = incidents.integration_review_transition(
            incomplete_history, "handoff"
        )
        self.assertFalse(rejected["allowed"])
        self.assertIn("declared complete", " ".join(rejected["reasons"]))

    def test_review_block_and_restore_retry_every_write_prefix_idempotently(self):
        prepared = integration_snapshot()
        prepared = apply_integration_result(
            prepared, incidents.integration_review_transition(prepared, "prepare")
        )
        prepared = apply_integration_result(
            prepared, incidents.integration_review_transition(prepared, "start")
        )
        failed = copy.deepcopy(prepared)
        failed["handoff"] = handoff_evidence(attempt=3)
        failed["handoff"]["trigger_outcomes"] = []
        blocked_result = incidents.integration_review_transition(failed, "handoff")
        self.assertFalse(blocked_result["allowed"])
        full_block_writes = blocked_result["block_writes"]
        self.assertGreater(len(full_block_writes), 0)

        for cut in range(len(full_block_writes) + 1):
            with self.subTest(direction="block", cut=cut):
                partial = apply_integration_block_writes(
                    failed, full_block_writes[:cut]
                )
                resumed = incidents.integration_review_transition(partial, "handoff")
                self.assertFalse(resumed["allowed"])
                self.assertEqual(resumed["metadata_updates"], {})
                self.assertEqual(resumed["block_writes"], full_block_writes[cut:])
                complete = apply_integration_block_writes(
                    partial, resumed["block_writes"]
                )
                self.assertEqual(complete["issue"]["status"], "blocked")
                self.assertEqual(
                    complete["issue"]["waiting_on"], "integration_review_trigger"
                )
                self.assertEqual(
                    complete["issue"]["integration_review_previous_status"],
                    "in_progress",
                )

        blocked = apply_integration_block_writes(failed, full_block_writes)
        routed = copy.deepcopy(blocked)
        routed["handoff"] = handoff_evidence(outcome="queued", attempt=1)
        restore_result = incidents.integration_review_transition(routed, "handoff")
        self.assertTrue(restore_result["allowed"])
        full_restore_writes = restore_result["block_writes"]
        self.assertGreater(len(full_restore_writes), 0)
        self.assertEqual(full_restore_writes[-2]["kind"], "status")
        self.assertEqual(full_restore_writes[-2]["value"], "in_progress")

        for cut in range(len(full_restore_writes) + 1):
            with self.subTest(direction="restore", cut=cut):
                partial = apply_integration_block_writes(
                    routed, full_restore_writes[:cut]
                )
                resumed = incidents.integration_review_transition(partial, "handoff")
                if 0 < cut < len(full_restore_writes):
                    self.assertFalse(resumed["allowed"])
                    self.assertEqual(
                        resumed["outcome"], "block_transition_resume_required"
                    )
                    self.assertEqual(resumed["metadata_updates"], {})
                else:
                    self.assertTrue(resumed["allowed"])
                self.assertEqual(resumed["block_writes"], full_restore_writes[cut:])
                complete = apply_integration_block_writes(
                    partial, resumed["block_writes"]
                )
                self.assertEqual(complete["issue"]["status"], "in_progress")
                self.assertEqual(complete["issue"]["waiting_on"], "")
                self.assertEqual(complete["issue"]["blocked_reason"], "")
                self.assertEqual(
                    complete["issue"]["integration_review_previous_status"], ""
                )

    def test_review_action_finishes_an_opposite_partial_block_before_main_writes(self):
        prepared = integration_snapshot()
        prepared = apply_integration_result(
            prepared, incidents.integration_review_transition(prepared, "prepare")
        )
        prepared = apply_integration_result(
            prepared, incidents.integration_review_transition(prepared, "start")
        )
        failed = copy.deepcopy(prepared)
        failed["handoff"] = handoff_evidence(attempt=3)
        failed["handoff"]["trigger_outcomes"] = []
        block_result = incidents.integration_review_transition(failed, "handoff")
        partial = apply_integration_block_writes(failed, block_result["block_writes"][:2])
        partial["handoff"] = handoff_evidence(outcome="queued", attempt=1)

        resumed = incidents.integration_review_transition(partial, "handoff")
        self.assertFalse(resumed["allowed"])
        self.assertEqual(resumed["outcome"], "block_transition_resume_required")
        self.assertEqual(resumed["metadata_updates"], {})
        completed_block = apply_integration_block_writes(partial, resumed["block_writes"])

        rerun = incidents.integration_review_transition(completed_block, "handoff")
        self.assertTrue(rerun["allowed"])
        self.assertIn("integration_review_role_record", rerun["metadata_updates"])
        self.assertGreater(len(rerun["block_writes"]), 0)

    def test_malformed_review_block_record_fails_closed_without_legacy_writes(self):
        snapshot = integration_snapshot()
        snapshot["issue"][incidents.INTEGRATION_BLOCK_RECORD_KEY] = "v1.invalid"
        snapshot["issue"]["metadata_keys"].append(incidents.INTEGRATION_BLOCK_RECORD_KEY)
        snapshot["context"]["roster"] = snapshot["context"]["roster"][:1]
        before = copy.deepcopy(snapshot)

        result = incidents.integration_review_transition(snapshot, "prepare")

        self.assertFalse(result["allowed"])
        self.assertEqual(result["metadata_updates"], {})
        self.assertEqual(result["block_metadata_updates"], {})
        self.assertIsNone(result["block_status_write"])
        self.assertEqual(result["block_writes"], [])
        self.assertTrue(any("block" in reason for reason in result["reasons"]))
        self.assertEqual(snapshot, before)

    def test_review_epoch_rejects_old_late_duplicate_and_unrouted_comments(self):
        snapshot = integration_snapshot()
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "prepare")
        )
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "start")
        )
        snapshot["handoff"] = handoff_evidence()
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "handoff")
        )
        role = incidents.decode_metadata_record(
            snapshot["issue"]["integration_review_role_record"],
            "role",
            "integration_review_role",
        )
        valid_review = {
            "issue_id": "IV-1",
            "author_type": "agent",
            "author_id": "reviewer-1",
            "comment_id": "review-current",
            "created_at": "2026-08-11T10:01:00Z",
            "verdict": "APPROVED",
            "review_epoch_id": role["review_epoch_id"],
            "trigger_comment_id": role["handoff_comment_id"],
            "source_run_id": role["trigger_run_id"],
        }
        cases = {}
        old = copy.deepcopy(valid_review)
        old["review_epoch_id"] = "old-epoch"
        cases["old epoch"] = old
        late = copy.deepcopy(valid_review)
        late["created_at"] = "2026-08-11T09:59:59Z"
        cases["predates handoff"] = late
        unrouted = copy.deepcopy(valid_review)
        unrouted["trigger_comment_id"] = "other-handoff"
        cases["no matching handoff"] = unrouted
        wrong_run = copy.deepcopy(valid_review)
        wrong_run["source_run_id"] = "late-run"
        cases["late run"] = wrong_run
        for name, review in cases.items():
            with self.subTest(name=name):
                candidate = copy.deepcopy(snapshot)
                candidate["review"] = review
                result = incidents.integration_review_transition(candidate, "approve")
                self.assertFalse(result["allowed"])
                self.assertEqual(result["metadata_updates"], {})

        consumed = copy.deepcopy(snapshot)
        consumed["review"] = valid_review
        consumed["used_review_comment_ids"] = ["review-current"]
        result = incidents.integration_review_transition(consumed, "approve")
        self.assertFalse(result["allowed"])
        self.assertIn("already consumed", " ".join(result["reasons"]))

        incomplete_history = copy.deepcopy(snapshot)
        incomplete_history["review"] = valid_review
        incomplete_history["used_review_comment_ids_complete"] = False
        result = incidents.integration_review_transition(incomplete_history, "approve")
        self.assertFalse(result["allowed"])
        self.assertIn("declared complete", " ".join(result["reasons"]))

        snapshot["review"] = valid_review
        approved = incidents.integration_review_transition(snapshot, "approve")
        self.assertTrue(approved["allowed"])
        snapshot = apply_integration_result(snapshot, approved)
        duplicate = incidents.integration_review_transition(snapshot, "approve")
        self.assertTrue(duplicate["allowed"])
        self.assertEqual(duplicate["outcome"], "already_approved")
        forged_replay = copy.deepcopy(snapshot)
        forged_replay["review"]["author_id"] = "integrator-1"
        rejected = incidents.integration_review_transition(forged_replay, "approve")
        self.assertFalse(rejected["allowed"])
        self.assertEqual(rejected["metadata_updates"], {})

        replacement_handoff = copy.deepcopy(snapshot)
        replacement_handoff["handoff"] = handoff_evidence(
            comment="handoff-after-approval", run="run-after-approval"
        )
        rejected = incidents.integration_review_transition(
            replacement_handoff, "handoff"
        )
        self.assertFalse(rejected["allowed"])
        self.assertIn("cannot replace", " ".join(rejected["reasons"]))
        self.assertEqual(rejected["metadata_updates"], {})
        snapshot["review"]["comment_id"] = "review-late-duplicate"
        replacement = incidents.integration_review_transition(snapshot, "approve")
        self.assertFalse(replacement["allowed"])

    def test_legacy_recovery_binds_incident_and_forces_a_new_review_epoch(self):
        snapshot = integration_snapshot()
        snapshot["issue"].update(
            {
                "status": "blocked",
                "assignee_id": "reviewer-1",
                "original_owner_id": "reviewer-1",
                "reviewer_id": "reviewer-1",
                "workflow_blocked_by_incident_id": "INC-1",
                "workflow_blocked_previous_status": "in_progress",
                "waiting_on": "workflow_fix",
                "blocked_reason": "workflow Incident INC-1",
            }
        )
        snapshot["lease"] = {
            "current": {
                "scope": "requirement",
                "state": "released",
                "owner_issue_id": "",
                "owner_agent_id": "",
            },
            "target": {
                "scope": "requirement",
                "state": "held",
                "owner_issue_id": "IV-1",
                "owner_agent_id": "integrator-1",
            },
        }
        snapshot["recovery"] = {
            "recovery_id": "recovery-1",
            "incident_id": "INC-1",
        }
        recovered = incidents.integration_review_transition(snapshot, "recover")
        self.assertTrue(recovered["allowed"])
        self.assertTrue(recovered["incident_blocker_preserved"])
        self.assertIsNone(recovered["status_write"])
        self.assertEqual(recovered["assignee_write"], "integrator-1")
        snapshot = apply_integration_result(snapshot, recovered)
        snapshot["lease"] = copy.deepcopy(snapshot["lease"]["target"])
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "start")
        )
        snapshot["handoff"] = handoff_evidence(comment="recovery-handoff", run="recovery-run")
        handed_off = incidents.integration_review_transition(snapshot, "handoff")
        self.assertTrue(handed_off["allowed"])
        role = incidents.decode_metadata_record(
            handed_off["metadata_updates"]["integration_review_role_record"],
            "role",
            "integration_review_role",
        )
        self.assertIn("recovery_record_digest", role)
        self.assertNotEqual(role["review_epoch_id"], "old-epoch")

    def test_incident_close_binding_requires_the_fresh_recovery_review_and_lease(self):
        snapshot = integration_snapshot()
        snapshot["issue"].update(
            {
                "status": "blocked",
                "assignee_id": "reviewer-1",
                "original_owner_id": "reviewer-1",
                "reviewer_id": "reviewer-1",
                "workflow_blocked_by_incident_id": "INC-1",
                "workflow_blocked_previous_status": "in_progress",
                "waiting_on": "workflow_fix",
                "blocked_reason": "workflow Incident INC-1",
            }
        )
        lease_evidence = {
            "current": {
                "scope": "requirement",
                "state": "released",
                "owner_issue_id": "",
                "owner_agent_id": "",
            },
            "target": {
                "scope": "requirement",
                "state": "held",
                "owner_issue_id": "IV-1",
                "owner_agent_id": "integrator-1",
            },
        }
        snapshot["lease"] = copy.deepcopy(lease_evidence)
        snapshot["recovery"] = {"incident_id": "INC-1", "recovery_id": "recover-1"}
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "recover")
        )
        snapshot["lease"] = copy.deepcopy(lease_evidence["target"])
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "start")
        )
        snapshot["handoff"] = handoff_evidence()
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "handoff")
        )
        role = incidents.decode_metadata_record(
            snapshot["issue"]["integration_review_role_record"],
            "role",
            "integration_review_role",
        )
        snapshot["review"] = {
            "issue_id": "IV-1",
            "author_type": "agent",
            "author_id": "reviewer-1",
            "comment_id": "review-1",
            "created_at": "2026-08-11T10:01:00Z",
            "verdict": "APPROVED",
            "review_epoch_id": role["review_epoch_id"],
            "trigger_comment_id": "handoff-1",
            "source_run_id": "run-1",
        }
        snapshot = apply_integration_result(
            snapshot, incidents.integration_review_transition(snapshot, "approve")
        )
        lease_blocker = {
            key: snapshot["issue"].get(key, "")
            for key in (
                "status",
                "waiting_on",
                "blocked_reason",
                "workflow_blocked_by_incident_id",
                "workflow_blocked_previous_status",
            )
        }
        lease_plan = {
            "schema_version": 1,
            "record_type": "workspace_lease_transition",
            "workspace_id": "workspace-test",
            "squad_id": "squad-test",
            "roster_digest": role["roster_digest"],
            "plan_revision": 2,
            "delivery_policy_digest": "6" * 64,
            "guard_digest": "9" * 64,
            "authority_issue_id": "REQ-1",
            "mirror_issue_id": "IV-1",
            "direction": "acquire",
            "initial_authority": {
                "workspace_lease_state": "released",
                "workspace_lease_owner_issue_id": "",
                "workspace_lease_owner_agent_id": "",
                "workspace_lease_transition_record": "",
            },
            "initial_mirror": {
                "workspace_lease_state": "released",
                "workspace_lease_owner_issue_id": "",
                "workspace_lease_owner_agent_id": "",
                "workspace_lease_transition_record": "",
            },
            "desired": {
                "workspace_lease_state": "held",
                "workspace_lease_owner_issue_id": "IV-1",
                "workspace_lease_owner_agent_id": "integrator-1",
            },
            "mirror_blocker": lease_blocker,
            "mirror_blocker_digest": incidents.sha256_value(lease_blocker),
            "other_leases": [],
            "other_leases_digest": incidents.sha256_value([]),
        }
        lease_record = incidents.encode_metadata_record(
            {**lease_plan, "completed": 6}
        )
        source_metadata = {
            **snapshot["issue"],
            "workflow_object_type": "integration_validation",
            "workspace_lease_scope": "requirement",
            "workspace_lease_state": "held",
            "workspace_lease_owner_issue_id": "IV-1",
            "workspace_lease_owner_agent_id": "integrator-1",
            "workspace_lease_transition_record": lease_record,
        }
        self.cli.metadata["REQ-1"].update(
            {
                "workspace_lease_scope": "requirement",
                "workspace_lease_state": "held",
                "workspace_lease_owner_issue_id": "IV-1",
                "workspace_lease_owner_agent_id": "integrator-1",
                "workspace_lease_transition_record": lease_record,
            }
        )
        self.cli.add_issue("IV-1", status="blocked")
        self.cli.metadata["IV-1"].update(source_metadata)
        binding = incidents._live_block_binding(
            self.cli, source_metadata, "a" * 40, "b" * 64
        )
        self.assertTrue(binding["integration_review_recovery_approved"])
        self.assertEqual(binding["integration_review_recovery_incident_id"], "INC-1")

        self.cli.metadata["REQ-1"]["workspace_lease_transition_record"] = (
            incidents.encode_metadata_record({**lease_plan, "completed": 5})
        )
        partial = incidents._live_block_binding(
            self.cli, source_metadata, "a" * 40, "b" * 64
        )
        self.assertFalse(partial["workspace_lease_transition_complete"])
        self.assertFalse(partial["integration_review_recovery_approved"])
        self.cli.metadata["REQ-1"]["workspace_lease_transition_record"] = lease_record

        stale_role = incidents.decode_metadata_record(
            source_metadata["integration_review_role_record"],
            "role",
            "integration_review_role",
        )
        stale_role["state"] = "started"
        source_metadata["integration_review_role_record"] = incidents.encode_metadata_record(
            stale_role
        )
        stale = incidents._live_block_binding(
            self.cli, source_metadata, "a" * 40, "b" * 64
        )
        self.assertFalse(stale["integration_review_recovery_approved"])

    def test_integration_review_capacity_preflight_is_fresh_and_zero_write(self):
        snapshot = integration_snapshot()
        snapshot["issue"]["metadata_keys"] = [f"key_{index}" for index in range(49)]
        before = copy.deepcopy(snapshot)
        result = incidents.integration_review_transition(snapshot, "prepare")
        self.assertFalse(result["allowed"])
        self.assertEqual(result["metadata_updates"], {})
        self.assertEqual(snapshot, before)

        handed_off = integration_snapshot()
        handed_off = apply_integration_result(
            handed_off, incidents.integration_review_transition(handed_off, "prepare")
        )
        handed_off = apply_integration_result(
            handed_off, incidents.integration_review_transition(handed_off, "start")
        )
        handed_off["handoff"] = handoff_evidence()
        handed_off = apply_integration_result(
            handed_off, incidents.integration_review_transition(handed_off, "handoff")
        )
        handed_off["issue"]["metadata_keys"] = ["duplicate", "duplicate"]
        repeated = incidents.integration_review_transition(handed_off, "handoff")
        self.assertFalse(repeated["allowed"])
        self.assertIn("metadata key inventory is invalid", repeated["reasons"][0])

    def test_preflight_cli_does_not_require_multica_or_mutate_the_snapshot(self):
        snapshot = integration_snapshot()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "snapshot.json"
            original = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
            path.write_text(original, encoding="utf-8")
            env = os.environ.copy()
            env.pop("MULTICA_WORKSPACE_ID", None)
            env["MULTICA_BIN"] = str(Path(directory) / "missing-multica")
            result = subprocess.run(
                [
                    sys.executable,
                    str(INCIDENTS_PATH),
                    "integration-review",
                    "--action",
                    "prepare",
                    "--snapshot",
                    str(path),
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                env=env,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_block_transition_preserves_successor_blocker_at_every_retry_prefix(self):
        snapshot = {
            "incident": {"issue_id": "INC-1", "incident_status": "in_fix"},
            "source": {
                "issue_id": "IV-1",
                "status": "blocked",
                "metadata_keys": [f"key_{index}" for index in range(42)],
                "workflow_blocked_by_incident_id": "INC-1",
                "workflow_blocked_previous_status": "in_progress",
                "waiting_on": "workflow_fix",
                "blocked_reason": "workflow Incident INC-1",
                "workflow_block_transition_record": "",
            },
            "target": {
                "status": "blocked",
                "waiting_on": "plan_revision",
                "blocked_reason": "reviewed baseline changed after workflow deployment",
            },
            "binding": {
                "workflow_instance_id": "workspace-test",
                "plan_revision": 1,
                "delivery_policy_digest": "6" * 64,
                "base_commit_sha": "1" * 40,
                "reviewed_commit_sha": "2" * 40,
                "integration_review_role_record_digest": "3" * 64,
                "integration_review_recovery_record_digest": "4" * 64,
                "workspace_lease_transition_record_digest": "7" * 64,
                "workspace_lease_transition_complete": True,
                "integration_review_recovery_approved": True,
                "integration_review_recovery_incident_id": "INC-1",
                "fixed_source_commit": "5" * 40,
                "deployment_plan_digest": "6" * 64,
            },
        }
        first = incidents.block_transition_preflight(snapshot)
        self.assertTrue(first["allowed"])
        unreviewed = copy.deepcopy(snapshot)
        unreviewed["binding"]["integration_review_recovery_approved"] = False
        with self.assertRaisesRegex(
            incidents.IncidentError, "fresh approved recovery Review"
        ):
            incidents.block_transition_preflight(unreviewed)
        full_writes = first["writes"]
        for cut in range(len(full_writes) + 1):
            with self.subTest(cut=cut):
                candidate = copy.deepcopy(snapshot)
                for write in full_writes[:cut]:
                    candidate = apply_block_write_to_snapshot(candidate, write)
                    self.assertEqual(candidate["source"]["status"], "blocked")
                resumed = incidents.block_transition_preflight(candidate)
                self.assertTrue(resumed["allowed"])
                self.assertEqual(resumed["progress"], cut)
                if resumed["complete"]:
                    self.assertEqual(candidate["source"]["waiting_on"], "plan_revision")
                    self.assertEqual(
                        candidate["source"]["workflow_blocked_by_incident_id"], ""
                    )

        completed = copy.deepcopy(snapshot)
        for write in full_writes:
            completed = apply_block_write_to_snapshot(completed, write)
        completed["source"]["waiting_on"] = "dependency"
        completed["source"]["blocked_reason"] = "successor blocker advanced"
        resumed = incidents.block_transition_preflight(completed)
        self.assertTrue(resumed["complete"])
        self.assertEqual(resumed["writes"], [])

        next_incident = copy.deepcopy(completed)
        next_incident["incident"] = {"issue_id": "INC-2", "incident_status": "in_fix"}
        next_incident["source"].update(
            {
                "workflow_blocked_by_incident_id": "INC-2",
                "workflow_blocked_previous_status": "in_progress",
                "waiting_on": "workflow_fix",
                "blocked_reason": "workflow Incident INC-2",
            }
        )
        next_incident["target"] = {
            "status": "in_progress",
            "waiting_on": "",
            "blocked_reason": "",
        }
        next_incident["binding"] = {
            **next_incident["binding"],
            "integration_review_recovery_record_digest": "",
            "workspace_lease_transition_record_digest": "",
            "workspace_lease_transition_complete": False,
        }
        restarted = incidents.block_transition_preflight(next_incident)
        self.assertTrue(restarted["allowed"])
        self.assertFalse(restarted["complete"])

    def test_integration_review_never_overwrites_an_incomplete_incident_transition(self):
        snapshot = integration_snapshot()
        snapshot["issue"].update(
            {
                "status": "blocked",
                "workflow_blocked_by_incident_id": "",
                "workflow_block_transition_record": incidents.encode_metadata_record(
                    {
                        "schema_version": 1,
                        "record_type": "workflow_block_transition",
                        "incident_id": "INC-1",
                        "source_issue_id": "IV-1",
                        "initial": {
                            "status": "blocked",
                            "workflow_blocked_by_incident_id": "INC-1",
                            "workflow_blocked_previous_status": "in_progress",
                            "waiting_on": "workflow_fix",
                            "blocked_reason": "workflow Incident INC-1",
                            "workflow_block_transition_record": "",
                        },
                        "target": {
                            "status": "in_progress",
                            "waiting_on": "",
                            "blocked_reason": "",
                        },
                        "binding": {
                            "workflow_instance_id": "workspace-test",
                            "fixed_source_commit": "5" * 40,
                            "deployment_plan_digest": "6" * 64,
                        },
                        "binding_digest": incidents.sha256_value(
                            {
                                "workflow_instance_id": "workspace-test",
                                "fixed_source_commit": "5" * 40,
                                "deployment_plan_digest": "6" * 64,
                            }
                        ),
                        "completed": 2,
                    }
                ),
            }
        )
        snapshot["context"]["roster"] = snapshot["context"]["roster"][:1]
        rejected = incidents.integration_review_transition(snapshot, "prepare")
        self.assertFalse(rejected["allowed"])
        self.assertEqual(rejected["block_metadata_updates"], {})
        self.assertIsNone(rejected["block_status_write"])
        self.assertTrue(rejected["block_transition_preserved"])

    def test_block_transition_rejects_unrecorded_successor_drift(self):
        snapshot = {
            "incident": {"issue_id": "INC-1", "incident_status": "in_fix"},
            "source": {
                "issue_id": "IV-1",
                "status": "blocked",
                "metadata_keys": [],
                "workflow_blocked_by_incident_id": "INC-1",
                "workflow_blocked_previous_status": "in_progress",
                "waiting_on": "plan_revision",
                "blocked_reason": "new blocker",
                "workflow_block_transition_record": "",
            },
            "target": {"status": "in_progress", "waiting_on": "", "blocked_reason": ""},
            "binding": {},
        }
        with self.assertRaisesRegex(incidents.IncidentError, "initial waiting_on"):
            incidents.block_transition_preflight(snapshot)

    def test_close_retries_every_block_transition_write_idempotently(self):
        def configured_cli():
            cli = FakeCLI()
            cli.add_issue("REQ-1")
            incidents.bind_workflow_issue(cli, bind_args("REQ-1"))
            cli.add_issue("TASK-A", status="todo", parent_issue_id="REQ-1")
            incidents.bind_workflow_issue(
                cli, bind_args("TASK-A", "development_task")
            )
            created = incidents.report_incident(
                cli, report_args("TASK-A", block_source=True)
            )
            cli.add_issue("REQ-FIX", status="done")
            incidents.bind_workflow_issue(cli, bind_args("REQ-FIX"))
            incidents.link_fix(cli, link_args(created["incident_id"], "REQ-FIX"))
            cli.transition_write_count = 0
            cli.incident_close_write_count = 0
            return cli, created["incident_id"]

        probe, incident_id = configured_cli()
        snapshot = incidents._live_block_snapshot(
            probe,
            incident_id,
            probe.metadata[incident_id],
            "TASK-A",
            "a" * 40,
            "b" * 64,
        )
        total = incidents.block_transition_preflight(snapshot)["total_writes"]
        for failure_point in range(1, total + 1):
            with self.subTest(failure_point=failure_point):
                cli, current_incident = configured_cli()
                cli.fail_on_transition_write = failure_point
                with self.assertRaisesRegex(
                    incidents.IncidentError, "injected transition write failure"
                ):
                    incidents.close_incident(cli, close_args(current_incident))
                cli.fail_on_transition_write = None
                result = incidents.close_incident(cli, close_args(current_incident))
                self.assertTrue(result["closed"])
                self.assertEqual(cli.issues["TASK-A"]["status"], "todo")
                self.assertEqual(cli.metadata[current_incident]["incident_status"], "closed")

    def test_close_retries_every_incident_terminal_write_idempotently(self):
        def configured_cli():
            cli = FakeCLI()
            cli.add_issue("REQ-1")
            incidents.bind_workflow_issue(cli, bind_args("REQ-1"))
            created = incidents.report_incident(cli, report_args("REQ-1"))
            cli.add_issue("REQ-FIX", status="done")
            incidents.bind_workflow_issue(cli, bind_args("REQ-FIX"))
            incidents.link_fix(cli, link_args(created["incident_id"], "REQ-FIX"))
            cli.incident_close_write_count = 0
            return cli, created["incident_id"]

        probe, probe_incident = configured_cli()
        incidents.close_incident(probe, close_args(probe_incident))
        total = probe.incident_close_write_count
        self.assertGreater(total, 1)

        for failure_point in range(1, total + 1):
            with self.subTest(failure_point=failure_point):
                cli, incident_id = configured_cli()
                cli.fail_on_incident_close_write = failure_point
                with self.assertRaisesRegex(
                    incidents.IncidentError, "injected Incident close write failure"
                ):
                    incidents.close_incident(cli, close_args(incident_id))
                cli.fail_on_incident_close_write = None
                result = incidents.close_incident(cli, close_args(incident_id))
                self.assertTrue(result["closed"])
                self.assertEqual(cli.metadata[incident_id]["incident_status"], "closed")
                self.assertEqual(cli.metadata[incident_id]["waiting_on"], "")
                self.assertEqual(
                    cli.metadata[incident_id]["fixed_source_commit"], "a" * 40
                )
                self.assertEqual(
                    cli.metadata[incident_id]["deployment_plan_digest"], "b" * 64
                )
                self.assertEqual(cli.issues[incident_id]["status"], "done")


if __name__ == "__main__":
    unittest.main()
