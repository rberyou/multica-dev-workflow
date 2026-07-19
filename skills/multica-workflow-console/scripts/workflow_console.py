#!/usr/bin/env python3
"""Read-only workflow status entry point for a host user."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import sys


def locate_repo(explicit: str | None) -> Path:
    if explicit:
        root = Path(explicit).expanduser().resolve()
        if (root / "workflow.json").is_file() and (root / "scripts/workflow.py").is_file():
            return root
        raise RuntimeError("--repo is not a multica-dev-workflow checkout")
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / "workflow.json").is_file() and (candidate / "scripts/workflow.py").is_file():
            return candidate
    configured = os.environ.get("MULTICA_WORKFLOW_REPO")
    if configured:
        return locate_repo(configured)
    raise RuntimeError("workflow repository was not found; pass --repo or set MULTICA_WORKFLOW_REPO")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status"])
    parser.add_argument("--repo")
    parser.add_argument("--multica-bin")
    parser.add_argument("--profile")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    if os.environ.get("MULTICA_AGENT_ID") or os.environ.get("MULTICA_TASK_ID"):
        raise SystemExit("workflow-console is host-only and unavailable inside Agent runtimes")
    root = locate_repo(args.repo)
    command = [sys.executable, str(root / "scripts/workflow.py"), "audit", "--scope", "all", "--output", "json"]
    if args.multica_bin:
        command.extend(["--multica-bin", args.multica_bin])
    if args.profile:
        command.extend(["--profile", args.profile])
    if args.workspace:
        command.extend(["--workspace", args.workspace])
    return subprocess.run(command, cwd=root).returncode


if __name__ == "__main__":
    raise SystemExit(main())
