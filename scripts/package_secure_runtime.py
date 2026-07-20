#!/usr/bin/env python3
"""Build the reviewed self-contained Windows secure runtime release asset."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import subprocess
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
FIXED_ZIP_TIME = (2020, 1, 1, 0, 0, 0)
PROJECTS = {
    "secure-agent-launcher.exe": "secure-runtime/src/SecureAgentLauncher/SecureAgentLauncher.csproj",
    "workflow-token-broker.exe": "secure-runtime/src/WorkflowTokenBroker/WorkflowTokenBroker.csproj",
    "workflow-secure-daemon-host.exe": "secure-runtime/src/WorkflowSecureDaemonHost/WorkflowSecureDaemonHost.csproj",
    "workflow-console.exe": "secure-runtime/src/WorkflowConsole/WorkflowConsole.csproj",
}


def publish_projects(root: Path, directory: Path, runtime: str) -> dict[str, Path]:
    outputs = {}
    for executable, project in PROJECTS.items():
        project_output = directory / executable.removesuffix(".exe")
        subprocess.run(
            [
                "dotnet",
                "publish",
                str(root / project),
                "--configuration",
                "Release",
                "--runtime",
                runtime,
                "--self-contained",
                "true",
                "-p:PublishSingleFile=true",
                "-p:IncludeNativeLibrariesForSelfExtract=true",
                "-p:DebugType=None",
                "-p:DebugSymbols=false",
                "--output",
                str(project_output),
            ],
            cwd=root,
            check=True,
        )
        path = project_output / executable
        if not path.is_file():
            raise FileNotFoundError(f"dotnet publish did not create {path}")
        outputs[executable] = path
    return outputs


def archive_entries(root: Path, executables: dict[str, Path]) -> list[tuple[str, Path]]:
    entries = [(f"bin/{name}", path) for name, path in executables.items()]
    for directory in [
        root / "secure-runtime/install/windows",
        root / "secure-runtime/policy",
    ]:
        for path in sorted(directory.rglob("*")):
            if path.is_file():
                entries.append(
                    (
                        path.relative_to(root / "secure-runtime").as_posix(),
                        path,
                    )
                )
    entries.append(("VERSION", root / "VERSION"))
    return sorted(entries, key=lambda item: item[0])


def build_archive(root: Path, executables: dict[str, Path], output: Path) -> str:
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(
        output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for relative, path in archive_entries(root, executables):
            info = zipfile.ZipInfo(relative, FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o100644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes())
    return hashlib.sha256(output.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--runtime", default="win-x64")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        published = publish_projects(ROOT, Path(temporary), args.runtime)
        value = build_archive(ROOT, published, args.output.resolve())
    print(value)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
