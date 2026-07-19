#!/usr/bin/env python3
"""Generate the hash allowlist consumed by the Secure Agent Launcher."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "secure-runtime/policy/workflow-bundle.manifest.json"


def build_manifest(root: Path) -> dict:
    files = {}
    sources = [
        *sorted((root / "skills").glob("**/*")),
        root / "secure-runtime/policy/requirements.template.toml",
    ]
    for path in sources:
        if not path.is_file():
            continue
        relative = path.relative_to(root).as_posix()
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {"schema_version": 1, "files": dict(sorted(files.items()))}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = json.dumps(build_manifest(ROOT), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_text(encoding="utf-8") != expected:
            raise SystemExit("secure runtime workflow bundle manifest is stale")
        return 0
    OUTPUT.write_text(expected, encoding="utf-8")
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
