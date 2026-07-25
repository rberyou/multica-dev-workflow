#!/usr/bin/env python3
"""Generate the portable Observer desired-state audit contract."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re

from workflow_lib import agent_spec, instruction_body, package_hash, project_spec, sha256_value


def portable_observer_source_hash(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    excluded = "references/control-plane-contract.json"
    files = sorted(
        (
            path
            for path in skill_dir.rglob("*")
            if path.is_file()
            and path.relative_to(skill_dir).as_posix() != excluded
            and not any(part in {"__pycache__", ".git"} for part in path.parts)
        ),
        key=lambda path: path.relative_to(skill_dir).as_posix(),
    )
    for path in files:
        relative_text = path.relative_to(skill_dir).as_posix()
        data = path.read_bytes()
        if path.suffix.lower() in {".md", ".py", ".json", ".yaml", ".yml", ".txt"}:
            text = data.decode("utf-8").replace("\r\n", "\n")
            if relative_text == "SKILL.md":
                text = re.sub(r"(?m)^\s*package_hash:\s*\S+\s*\n", "", text)
            data = text.encode("utf-8")
        relative = relative_text.encode("utf-8")
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def build_contract(root: Path) -> dict:
    manifest = json.loads((root / "workflow.json").read_text(encoding="utf-8"))
    profiles = {}
    for path in sorted((root / "deployment-profiles").glob("*.json")):
        profile = json.loads(path.read_text(encoding="utf-8"))
        profiles[profile["name"]] = profile["bindings"]

    contract = {
        "schema_version": 1,
        "workflow_version": manifest["workflow"]["version"],
        "workflow_phase": manifest["workflow"]["phase"],
        "protocol_revision": manifest["workflow"]["protocol_revision"],
        "observer_source_hash": portable_observer_source_hash(
            root / "skills/multica-workflow-observer"
        ),
        "agents": {},
        "profiles": profiles,
        "skills": {},
        "squad": {},
        "projects": {},
        "autopilots": {},
        "operations": {},
    }
    for agent in manifest["agents"]:
        body = instruction_body(root, agent["instruction_files"])
        desired = {
            key: agent.get(key, "")
            for key in [
                "name",
                "description",
                "runtime_binding",
                "max_concurrent_tasks",
                "permission_mode",
            ]
        }
        desired["instructions_sha256"] = sha256_value(body)
        desired["spec_hashes"] = {}
        for profile_name, bindings in profiles.items():
            binding = bindings[agent["runtime_binding"]]
            desired["spec_hashes"][profile_name] = sha256_value(
                agent_spec(
                    agent["key"],
                    agent["name"],
                    agent.get("description", ""),
                    body,
                    binding.get("provider", ""),
                    binding.get("model", ""),
                    binding.get("thinking_level", ""),
                    int(agent.get("max_concurrent_tasks", 1)),
                    agent.get("permission_mode", "private"),
                )
            )
        contract["agents"][agent["key"]] = desired

    for skill in manifest["skills"]:
        if "workspace" not in skill.get("targets", []):
            continue
        contract["skills"][skill["name"]] = {
            "key": skill["key"],
            "attach_to": skill.get("attach_to", []),
            "package_hash": (
                None
                if skill["key"] == "workflow-observer"
                else package_hash(root / skill["path"])
            ),
        }

    squad = manifest["squad"]
    contract["squad"] = {
        key: squad.get(key, "") for key in ["key", "name", "description", "leader"]
    }
    contract["squad"]["instructions_sha256"] = sha256_value(
        instruction_body(root, squad["instruction_files"])
    )
    contract["squad"]["spec_hash"] = sha256_value(
        {
            "key": squad["key"],
            "name": squad["name"],
            "description": squad.get("description", ""),
            "instructions": instruction_body(root, squad["instruction_files"]),
            "leader": squad["leader"],
        }
    )
    contract["squad"]["agent_members"] = squad["agent_members"]
    contract["squad"]["human_members"] = squad["human_members"]

    for project in manifest.get("projects", []):
        contract["projects"][project["key"]] = {
            key: project.get(key, "")
            for key in ["title", "description", "lead", "status", "icon"]
        }
        contract["projects"][project["key"]]["spec_hash"] = sha256_value(
            project_spec(
                project["key"],
                project["title"],
                str(project.get("description") or "").strip() + "\n",
                project["lead"],
                project["status"],
                str(project.get("icon") or ""),
            )
        )

    for autopilot in manifest.get("autopilots", []):
        desired = {
            key: autopilot.get(key, "")
            for key in [
                "title",
                "description",
                "agent",
                "mode",
                "project",
                "status",
                "issue_title_template",
            ]
        }
        desired["subscribers"] = autopilot.get("subscribers", [])
        desired["triggers"] = autopilot.get("triggers", [])
        contract["autopilots"][autopilot["key"]] = desired
    operations = manifest["operations"]
    observer_skill = next(
        item for item in manifest["skills"] if item["key"] == operations["observer_skill"]
    )
    managed_autopilot = next(
        item for item in manifest["autopilots"] if item["key"] == operations["autopilot"]
    )
    managed_autopilots = {
        item["key"]: item
        for item in manifest["autopilots"]
        if item["key"]
        in {operations["autopilot"], operations["full_scan_autopilot"]}
    }
    contract["operations"] = {
        **operations,
        "observer_skill_name": observer_skill["name"],
        "modes": {
            "enabled": {
                "autopilot_status": managed_autopilot["status"],
                "autopilot_statuses": {
                    key: item["status"] for key, item in managed_autopilots.items()
                },
                "observer_skill_attach_to": observer_skill.get("attach_to", []),
            },
            "disabled": {
                "autopilot_status": "paused",
                "autopilot_statuses": {
                    key: "paused" for key in managed_autopilots
                },
                "observer_skill_attach_to": observer_skill.get("attach_to", []),
            },
        },
    }
    return contract


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = root / "skills/multica-workflow-observer/references/control-plane-contract.json"
    rendered = json.dumps(build_contract(root), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not output.is_file() or output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("Observer control-plane contract is stale; regenerate it")
        print("Observer control-plane contract: OK")
        return 0
    output.write_text(rendered, encoding="utf-8", newline="\n")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
