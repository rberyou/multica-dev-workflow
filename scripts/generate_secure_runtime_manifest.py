#!/usr/bin/env python3
"""Generate the hash allowlist consumed by the Secure Agent Launcher."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import stat
import subprocess


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "secure-runtime/policy/workflow-bundle.manifest.json"
SOURCE_ROOTS = ("skills",)
SOURCE_FILES = ("secure-runtime/policy/requirements.template.toml",)
REGULAR_GIT_MODES = frozenset({"100644", "100755"})
PUBLISHABLE_SUFFIXES = frozenset({".json", ".md", ".py", ".toml", ".yaml", ".yml"})
HARD_EXCLUDED_DIRECTORIES = frozenset({"__pycache__"})
HARD_EXCLUDED_SUFFIXES = frozenset({".pyc", ".pyo"})
UNTRACKED_EXCLUDED_DIRECTORIES = frozenset(
    {
        ".cache",
        ".mypy_cache",
        ".nox",
        ".pytest_cache",
        ".ruff_cache",
        ".tox",
        ".venv",
        "bin",
        "build",
        "cache",
        "coverage",
        "dist",
        "htmlcov",
        "node_modules",
        "obj",
        "out",
        "target",
        "temp",
        "tmp",
        "venv",
    }
)
UNTRACKED_EXCLUDED_FILENAMES = frozenset(
    {".coverage", ".ds_store", "desktop.ini", "thumbs.db"}
)
UNTRACKED_EXCLUDED_SUFFIXES = frozenset(
    {
        ".bak",
        ".log",
        ".orig",
        ".pyc",
        ".pyo",
        ".rej",
        ".swp",
        ".swo",
        ".temp",
        ".tmp",
    }
)


class ManifestError(RuntimeError):
    pass


def run_git(
    root: Path,
    args: list[str],
    *,
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess[bytes]:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        input=input_bytes,
        capture_output=True,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise ManifestError(detail or f"git {' '.join(args)} failed")
    return result


def decode_paths(value: bytes) -> list[str]:
    return sorted(
        item.decode("utf-8", "surrogateescape").replace("\\", "/")
        for item in value.split(b"\0")
        if item
    )


def tracked_paths(root: Path) -> dict[str, str]:
    result = run_git(
        root,
        [
            "ls-files",
            "-z",
            "--stage",
            "--cached",
            "--",
            *SOURCE_ROOTS,
            *SOURCE_FILES,
        ],
    )
    paths = {}
    for item in result.stdout.split(b"\0"):
        if not item:
            continue
        metadata, raw_path = item.split(b"\t", 1)
        mode, _, stage = metadata.split(b" ", 2)
        if stage != b"0":
            path = raw_path.decode("utf-8", "surrogateescape")
            raise ManifestError(f"unmerged source path is not publishable: {path}")
        path = raw_path.decode("utf-8", "surrogateescape").replace("\\", "/")
        paths[path] = mode.decode("ascii")
    return dict(sorted(paths.items()))


def repository_ignored_tracked_paths(root: Path) -> set[str]:
    result = run_git(
        root,
        [
            "ls-files",
            "-z",
            "--cached",
            "--ignored",
            "--exclude-per-directory=.gitignore",
            "--",
            *SOURCE_ROOTS,
            *SOURCE_FILES,
        ],
    )
    return set(decode_paths(result.stdout))


def deleted_tracked_paths(root: Path) -> set[str]:
    result = run_git(
        root,
        [
            "ls-files",
            "-z",
            "--deleted",
            "--",
            *SOURCE_ROOTS,
            *SOURCE_FILES,
        ],
    )
    return set(decode_paths(result.stdout))


def untracked_paths(root: Path) -> list[str]:
    result = run_git(
        root,
        [
            "ls-files",
            "-z",
            "--others",
            "--exclude-per-directory=.gitignore",
            "--",
            *SOURCE_ROOTS,
            *SOURCE_FILES,
        ],
    )
    return decode_paths(result.stdout)


def candidate_paths(root: Path) -> list[tuple[str, str | None]]:
    ignored_tracked = repository_ignored_tracked_paths(root)
    deleted_tracked = deleted_tracked_paths(root)
    tracked = [
        (path, mode)
        for path, mode in tracked_paths(root).items()
        if path not in ignored_tracked and path not in deleted_tracked
    ]
    untracked = [(path, None) for path in untracked_paths(root)]
    return sorted([*tracked, *untracked], key=lambda item: item[0])


def is_publishable_source(relative: str, *, tracked: bool) -> bool:
    path = PurePosixPath(relative)
    lowered_parts = {part.lower() for part in path.parts[:-1]}
    lowered_name = path.name.lower()
    suffix = path.suffix.lower()
    if (
        lowered_parts & HARD_EXCLUDED_DIRECTORIES
        or suffix in HARD_EXCLUDED_SUFFIXES
        or suffix not in PUBLISHABLE_SUFFIXES
    ):
        return False
    if tracked:
        return True
    return not (
        lowered_parts & UNTRACKED_EXCLUDED_DIRECTORIES
        or lowered_name in UNTRACKED_EXCLUDED_FILENAMES
        or suffix in UNTRACKED_EXCLUDED_SUFFIXES
        or lowered_name.endswith("~")
    )


def is_link_or_reparse(path: Path) -> bool:
    info = path.lstat()
    attributes = getattr(info, "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & reparse_flag)


def validate_git_mode(relative: str, git_mode: str | None) -> None:
    if git_mode is not None and git_mode not in REGULAR_GIT_MODES:
        raise ManifestError(f"unsupported Git mode {git_mode} for source path: {relative}")


def resolve_source(root: Path, relative: str, git_mode: str | None) -> Path:
    validate_git_mode(relative, git_mode)
    relative_path = PurePosixPath(relative)
    if relative_path.is_absolute() or ".." in relative_path.parts:
        raise ManifestError(f"source path escapes the repository: {relative}")
    root_path = root.resolve(strict=True)
    current = root_path
    for part in relative_path.parts:
        current = current / part
        try:
            if is_link_or_reparse(current):
                raise ManifestError(f"linked source path is not publishable: {relative}")
        except FileNotFoundError as error:
            raise ManifestError(f"source path disappeared during generation: {relative}") from error
    resolved = current.resolve(strict=True)
    try:
        resolved.relative_to(root_path)
    except ValueError as error:
        raise ManifestError(f"source path escapes the repository: {relative}") from error
    if not stat.S_ISREG(resolved.stat().st_mode):
        raise ManifestError(f"source path is not a regular file: {relative}")
    return resolved


def ensure_git_filters_preserve_bytes(root: Path, relative: str, content: bytes) -> None:
    raw = run_git(
        root,
        ["hash-object", "--stdin", "--no-filters"],
        input_bytes=content,
    ).stdout.strip()
    filtered = run_git(
        root,
        ["hash-object", "--stdin", f"--path={relative}"],
        input_bytes=content,
    ).stdout.strip()
    if raw != filtered:
        raise ManifestError(f"Git filters would change source bytes: {relative}")


def build_manifest(root: Path) -> dict:
    files = {}
    for relative, git_mode in candidate_paths(root):
        validate_git_mode(relative, git_mode)
        if not is_publishable_source(relative, tracked=git_mode is not None):
            continue
        path = resolve_source(root, relative, git_mode)
        content = path.read_bytes()
        ensure_git_filters_preserve_bytes(root, relative, content)
        files[relative] = hashlib.sha256(content).hexdigest()
    return {"schema_version": 1, "files": dict(sorted(files.items()))}


def render_manifest(root: Path) -> bytes:
    content = json.dumps(build_manifest(root), ensure_ascii=False, indent=2) + "\n"
    return content.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = render_manifest(ROOT)
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != expected:
            raise SystemExit("secure runtime workflow bundle manifest is stale")
        return 0
    OUTPUT.write_bytes(expected)
    print(OUTPUT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
