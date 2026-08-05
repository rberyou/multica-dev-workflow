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
        raise RuntimeError("--repo is not a multica-dev-workflow source")
    for candidate in [Path.cwd(), *Path.cwd().parents]:
        if (candidate / "workflow.json").is_file() and (candidate / "scripts/workflow.py").is_file():
            return candidate
    configured = os.environ.get("MULTICA_WORKFLOW_REPO")
    if configured:
        return locate_repo(configured)
    raise RuntimeError("workflow source was not found; pass --repo or set MULTICA_WORKFLOW_REPO")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status"])
    parser.add_argument("--repo")
    parser.add_argument("--multica-bin")
    parser.add_argument("--profile")
    parser.add_argument("--workspace")
    args = parser.parse_args()
    if os.environ.get("MULTICA_AGENT_ID") or os.environ.get("MULTICA_TASK_ID"):
        print(
            "ERROR: workflow-console is host-only and unavailable inside Agent runtimes",
            file=sys.stderr,
        )
        return 2
    try:
        root = locate_repo(args.repo)
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    base = [sys.executable, str(root / "scripts/workflow.py")]
    context = []
    if args.multica_bin:
        context.extend(["--multica-bin", args.multica_bin])
    if args.profile:
        context.extend(["--profile", args.profile])
    if args.workspace:
        context.extend(["--workspace", args.workspace])
    doctor = subprocess.run([*base, "doctor", *context], cwd=root)
    if doctor.returncode != 0:
        return doctor.returncode
    return subprocess.run([*base, "drift", *context], cwd=root).returncode


if __name__ == "__main__":
    raise SystemExit(main())
