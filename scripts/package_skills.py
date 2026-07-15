#!/usr/bin/env python3
"""Build deterministic Multica skill archives from repository sources."""

from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import re
import zipfile


FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)


def source_files(skill_dir: Path) -> list[Path]:
    files = []
    for path in skill_dir.rglob("*"):
        if not path.is_file():
            continue
        if any(part in {"__pycache__", ".git"} for part in path.parts):
            continue
        files.append(path)
    return sorted(files, key=lambda item: item.relative_to(skill_dir).as_posix())


def package_hash(skill_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in source_files(skill_dir):
        relative = path.relative_to(skill_dir).as_posix().encode("utf-8")
        data = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(data).to_bytes(8, "big"))
        digest.update(data)
    return digest.hexdigest()


def inject_package_hash(content: str, value: str) -> str:
    match = re.match(r"\A---\r?\n(?P<header>.*?)\r?\n---\r?\n", content, re.DOTALL)
    if not match:
        raise ValueError("SKILL.md must contain YAML frontmatter")
    header = match.group("header")
    lines = header.splitlines()
    metadata_index = next((i for i, line in enumerate(lines) if line == "metadata:"), None)
    if metadata_index is None:
        lines.extend(["metadata:", f"  package_hash: {value}"])
    else:
        end = metadata_index + 1
        while end < len(lines) and (lines[end].startswith("  ") or not lines[end].strip()):
            end += 1
        filtered = [line for line in lines[metadata_index + 1 : end] if not line.strip().startswith("package_hash:")]
        lines[metadata_index + 1 : end] = filtered + [f"  package_hash: {value}"]
    replacement = "---\n" + "\n".join(lines) + "\n---\n"
    return replacement + content[match.end() :]


def build_archive(skill_dir: Path, output: Path) -> str:
    skill_dir = skill_dir.resolve()
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.is_file():
        raise FileNotFoundError(f"missing {skill_md}")
    value = package_hash(skill_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in source_files(skill_dir):
            relative = path.relative_to(skill_dir).as_posix()
            data = path.read_bytes()
            if relative == "SKILL.md":
                data = inject_package_hash(data.decode("utf-8"), value).encode("utf-8")
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            archive.writestr(info, data)
    return value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("skill_dir", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    value = build_archive(args.skill_dir, args.output)
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
