#!/usr/bin/env python3
"""Create an optional GitHub Release from a clean main checkout."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
import zipfile

from package_skills import FIXED_ZIP_TIME, build_archive
from workflow_lib import WorkflowError, read_json, repo_root, utc_now, validate_repository, write_json


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class ReleaseError(RuntimeError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def run(args: list[str], root: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        args,
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if check and result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip()
        raise ReleaseError(f"command failed ({result.returncode}): {' '.join(args)}\n{detail}")
    return result


def git_head(root: Path) -> str:
    return run(["git", "rev-parse", "HEAD"], root).stdout.strip()


def git_branch(root: Path) -> str:
    return run(["git", "branch", "--show-current"], root).stdout.strip()


def git_dirty(root: Path) -> bool:
    return bool(run(["git", "status", "--porcelain"], root).stdout.strip())


def skill_version_text(text: str) -> str:
    match = re.search(r"(?m)^\s*version:\s*(\S+)\s*$", text)
    return match.group(1) if match else ""


def skill_version(path: Path) -> str:
    return skill_version_text((path / "SKILL.md").read_text(encoding="utf-8"))


def verify_versions(root: Path, version: str) -> list[str]:
    if not SEMVER_RE.fullmatch(version):
        raise ReleaseError("version must be a semantic version")
    manifest = read_json(root / "workflow.json")
    checked = ["VERSION", "workflow.json"]
    if (root / "VERSION").read_text(encoding="utf-8").strip() != version:
        raise ReleaseError("VERSION does not match the requested release version")
    if str((manifest.get("workflow") or {}).get("version") or "") != version:
        raise ReleaseError("workflow.version does not match the requested release version")
    for skill in manifest.get("skills") or []:
        path = root / str(skill["path"])
        relative = f"{skill['path']}/SKILL.md"
        if skill_version(path) != version:
            raise ReleaseError(f"{relative} does not match the requested release version")
        checked.append(relative)
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    if not re.search(rf"(?m)^## {re.escape(version)}\s*$", changelog):
        raise ReleaseError(f"CHANGELOG.md has no section for {version}")
    checked.append("CHANGELOG.md")
    return checked


def expected_assets_from_manifest(manifest: dict[str, Any], version: str) -> list[str]:
    names = [f"{skill['name']}-v{version}.zip" for skill in manifest.get("skills") or []]
    return sorted([*names, f"multica-dev-workflow-v{version}.zip", "checksums.txt"])


def expected_assets(root: Path, version: str) -> list[str]:
    return expected_assets_from_manifest(read_json(root / "workflow.json"), version)


def build_plan(root: Path, version: str) -> dict[str, Any]:
    validate_repository(root, "quality")
    checked = verify_versions(root, version)
    branch = git_branch(root)
    source_commit = git_head(root)
    draft = git_dirty(root)
    if branch != "main":
        raise ReleaseError("formal releases must be planned from main")
    plan = {
        "schema_version": 1,
        "created_at": utc_now(),
        "version": version,
        "tag": f"v{version}",
        "branch": branch,
        "source_commit": source_commit,
        "draft": draft,
        "version_files": checked,
        "expected_assets": expected_assets(root, version),
    }
    plan["release_plan_digest"] = digest(plan)
    return plan


def save_plan(root: Path, plan: dict[str, Any]) -> Path:
    path = root / ".multica/release-plans" / (
        f"{plan['version']}-{str(plan['release_plan_digest'])[:12]}.json"
    )
    write_json(path, plan)
    return path


def load_plan(path: str | Path) -> dict[str, Any]:
    value = read_json(Path(path))
    if not isinstance(value, dict):
        raise ReleaseError("release Plan must be an object")
    return value


def verify_plan(root: Path, plan: dict[str, Any]) -> str:
    expected = str(plan.get("release_plan_digest") or "")
    payload = {key: value for key, value in plan.items() if key != "release_plan_digest"}
    if not expected or digest(payload) != expected:
        raise ReleaseError("release Plan digest is invalid or the Plan was modified")
    if plan.get("draft"):
        raise ReleaseError("a release Plan from a dirty checkout cannot be applied")
    if git_dirty(root):
        raise ReleaseError("working tree is dirty")
    if git_head(root) != plan.get("source_commit"):
        raise ReleaseError("Git HEAD changed after release planning")
    if git_branch(root) != plan.get("branch"):
        raise ReleaseError("Git branch changed after release planning")
    current = build_plan(root, str(plan.get("version") or ""))
    for key in ["version", "tag", "branch", "source_commit", "version_files", "expected_assets"]:
        if current.get(key) != plan.get(key):
            raise ReleaseError(f"release input changed after planning: {key}")
    return expected


def sha256_file(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_repository_archive(
    root: Path, plan: dict[str, Any], output: Path
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as temp:
        source_archive = Path(temp) / "source.zip"
        run(
            [
                "git",
                "archive",
                "--format=zip",
                "--output",
                str(source_archive),
                str(plan["source_commit"]),
            ],
            root,
        )
        with zipfile.ZipFile(source_archive) as archive:
            entries = [
                (item, archive.read(item.filename))
                for item in archive.infolist()
            ]
    file_hashes = {
        item.filename: sha256_bytes(data)
        for item, data in entries
        if not item.is_dir()
    }
    try:
        workflow = json.loads(
            next(data for item, data in entries if item.filename == "workflow.json")
        )
    except (StopIteration, json.JSONDecodeError) as exc:
        raise ReleaseError("repository archive has no valid workflow.json") from exc
    workflow_version = str((workflow.get("workflow") or {}).get("version") or "")
    if workflow_version != str(plan.get("version") or ""):
        raise ReleaseError(
            "repository archive workflow version differs from the release Plan"
        )
    if str(plan.get("tag") or "") != f"v{workflow_version}":
        raise ReleaseError("repository archive tag differs from its workflow version")
    release_manifest = {
        "schema_version": 1,
        "workflow_id": str((workflow.get("workflow") or {}).get("id") or ""),
        "workflow_version": workflow_version,
        "release_tag": str(plan["tag"]),
        "source_commit": str(plan["source_commit"]),
        "files": dict(sorted(file_hashes.items())),
    }
    release_manifest["bundle_digest"] = digest(release_manifest)
    manifest_bytes = (
        json.dumps(release_manifest, ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for item, data in entries:
            archive.writestr(item, data)
        info = zipfile.ZipInfo("release-manifest.json", FIXED_ZIP_TIME)
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o100644 & 0xFFFF) << 16
        archive.writestr(info, manifest_bytes)
    return release_manifest


def package_release(root: Path, plan: dict[str, Any], directory: Path) -> list[Path]:
    version = str(plan["version"])
    tag = str(plan["tag"])
    manifest = read_json(root / "workflow.json")
    directory.mkdir(parents=True, exist_ok=True)
    assets = []
    for skill in manifest.get("skills") or []:
        output = directory / f"{skill['name']}-{tag}.zip"
        build_archive(root / str(skill["path"]), output)
        assets.append(output)
    repository_asset = directory / f"multica-dev-workflow-{tag}.zip"
    build_repository_archive(root, plan, repository_asset)
    assets.append(repository_asset)
    checksums = directory / "checksums.txt"
    checksums.write_text(
        "".join(f"{sha256_file(path)}  {path.name}\n" for path in sorted(assets)),
        encoding="utf-8",
    )
    assets.append(checksums)
    actual = sorted(path.name for path in assets)
    if actual != sorted(plan["expected_assets"]):
        raise ReleaseError(f"release asset set differs from the Plan: {actual}")
    return assets


def command_doctor(args: argparse.Namespace, root: Path) -> int:
    validate_repository(root, "quality")
    run(["git", "--version"], root)
    run(["gh", "--version"], root)
    run(["gh", "auth", "status"], root)
    print(
        json.dumps(
            {
                "branch": git_branch(root),
                "source_commit": git_head(root),
                "dirty": git_dirty(root),
                "doctor": "ok",
            },
            indent=2,
        )
    )
    return 0


def command_plan(args: argparse.Namespace, root: Path) -> int:
    plan = build_plan(root, args.version)
    path = save_plan(root, plan)
    print(json.dumps(plan, ensure_ascii=False, indent=2))
    print(f"Release Plan file: {path}")
    if plan["draft"]:
        print("DRAFT: commit the reviewed files and generate a new release Plan")
        return 2
    return 0


def command_package(args: argparse.Namespace, root: Path) -> int:
    plan = load_plan(args.plan)
    verify_plan(root, plan)
    directory = Path(args.directory).expanduser().resolve()
    assets = package_release(root, plan, directory)
    print(json.dumps({"directory": str(directory), "assets": [path.name for path in assets]}, indent=2))
    return 0


def command_publish(args: argparse.Namespace, root: Path) -> int:
    if os.environ.get("MULTICA_AGENT_ID") or os.environ.get("MULTICA_TASK_ID"):
        raise ReleaseError(
            "refusing to publish from a daemon-managed Agent identity; "
            "run publish from the authenticated human host"
        )
    plan = load_plan(args.plan)
    verify_plan(root, plan)
    directory = Path(args.directory).expanduser().resolve()
    assets = package_release(root, plan, directory)
    command = [
        "gh",
        "release",
        "create",
        str(plan["tag"]),
        *[str(path) for path in assets],
        "--target",
        str(plan["source_commit"]),
        "--title",
        str(plan["tag"]),
        "--generate-notes",
    ]
    if args.prerelease or "-" in str(plan.get("version") or ""):
        command.append("--prerelease")
    run(command, root)
    print(json.dumps({"published": plan["tag"], "source_commit": plan["source_commit"]}, indent=2))
    return 0


def command_verify_tag(args: argparse.Namespace, root: Path) -> int:
    tag = args.tag
    if not tag.startswith("v"):
        raise ReleaseError("release tag must start with v")
    commit = run(["git", "rev-list", "-n", "1", tag], root).stdout.strip()
    if not commit:
        raise ReleaseError(f"tag not found: {tag}")
    version = tag[1:]
    if not SEMVER_RE.fullmatch(version):
        raise ReleaseError("release tag version must be a semantic version")
    version_text = run(["git", "show", f"{tag}:VERSION"], root).stdout.strip()
    if version_text != version:
        raise ReleaseError("tag VERSION does not match the tag")
    try:
        tag_manifest = json.loads(
            run(["git", "show", f"{tag}:workflow.json"], root).stdout
        )
    except json.JSONDecodeError as exc:
        raise ReleaseError("tag workflow.json is invalid") from exc
    if str((tag_manifest.get("workflow") or {}).get("version") or "") != version:
        raise ReleaseError("tag workflow.version does not match the tag")
    for skill in tag_manifest.get("skills") or []:
        skill_text = run(
            ["git", "show", f"{tag}:{skill['path']}/SKILL.md"], root
        ).stdout
        if skill_version_text(skill_text) != version:
            raise ReleaseError(f"tag Skill version does not match: {skill['name']}")
    changelog = run(["git", "show", f"{tag}:CHANGELOG.md"], root).stdout
    if not re.search(rf"(?m)^## {re.escape(version)}\s*$", changelog):
        raise ReleaseError("tag CHANGELOG.md has no matching version section")
    try:
        release = json.loads(
            run(
                ["gh", "release", "view", tag, "--json", "tagName,assets"], root
            ).stdout
        )
    except json.JSONDecodeError as exc:
        raise ReleaseError("GitHub Release response is invalid") from exc
    if release.get("tagName") != tag:
        raise ReleaseError("GitHub Release tag does not match")
    expected = expected_assets_from_manifest(tag_manifest, version)
    actual = sorted(
        str(asset.get("name") or "") for asset in release.get("assets") or []
    )
    if actual != expected:
        raise ReleaseError(f"GitHub Release assets differ from expected: {actual}")
    print(
        json.dumps(
            {"tag": tag, "version": version, "commit": commit, "assets": actual},
            indent=2,
        )
    )
    return 0


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description=__doc__)
    sub = root.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor")
    doctor.set_defaults(func=command_doctor)
    plan = sub.add_parser("plan")
    plan.add_argument("--version", required=True)
    plan.set_defaults(func=command_plan)
    package = sub.add_parser("package")
    package.add_argument("--plan", required=True)
    package.add_argument("--directory", default="build/release")
    package.set_defaults(func=command_package)
    publish = sub.add_parser("publish")
    publish.add_argument("--plan", required=True)
    publish.add_argument("--directory", default="build/release")
    publish.add_argument("--prerelease", action="store_true")
    publish.set_defaults(func=command_publish)
    verify = sub.add_parser("verify-tag")
    verify.add_argument("--tag", required=True)
    verify.set_defaults(func=command_verify_tag)
    return root


def main() -> int:
    args = parser().parse_args()
    try:
        root = repo_root(Path(__file__).resolve().parent.parent)
        return int(args.func(args, root))
    except (ReleaseError, WorkflowError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
