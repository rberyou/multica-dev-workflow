from pathlib import Path
import tempfile
import unittest
import zipfile


from scripts import package_secure_runtime


ROOT = Path(__file__).resolve().parents[1]


class SecureRuntimePackageTests(unittest.TestCase):
    def test_archive_contains_only_reviewed_runtime_payload(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            executables = {}
            for name in package_secure_runtime.PROJECTS:
                path = directory / name
                path.write_bytes(name.encode("ascii"))
                executables[name] = path
            first = directory / "first.zip"
            second = directory / "second.zip"
            first_hash = package_secure_runtime.build_archive(ROOT, executables, first)
            second_hash = package_secure_runtime.build_archive(ROOT, executables, second)
            self.assertEqual(first_hash, second_hash)
            with zipfile.ZipFile(first) as archive:
                names = set(archive.namelist())
            self.assertEqual(
                {name for name in names if name.startswith("bin/")},
                {f"bin/{name}" for name in package_secure_runtime.PROJECTS},
            )
            self.assertIn("install/windows/install.ps1", names)
            self.assertIn("install/windows/doctor.ps1", names)
            self.assertIn("install/windows/uninstall.ps1", names)
            self.assertIn("policy/workflow-bundle.manifest.json", names)
            self.assertIn("VERSION", names)

    def test_windows_lifecycle_scripts_preserve_rollback_material(self):
        install = (ROOT / "secure-runtime/install/windows/install.ps1").read_text(
            encoding="utf-8"
        )
        update = (
            ROOT / "secure-runtime/install/windows/update-bindings.ps1"
        ).read_text(encoding="utf-8")
        uninstall = (
            ROOT / "secure-runtime/install/windows/uninstall.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("rollback-", install)
        self.assertIn("$backupCreated = $false", install)
        self.assertIn("if ($backupCreated)", install)
        self.assertIn("previous installation was restored", install)
        self.assertIn("$target.rollback", update)
        self.assertIn("$backupCreated = $false", update)
        self.assertIn("previous bindings were restored", update)
        self.assertIn("@('credentials', 'tasks')", uninstall)

    def test_security_findings_have_structural_guards(self):
        core = ROOT / "secure-runtime/src/WorkflowSecureRuntime.Core"
        broker = (core / "GitHubBrokerOperations.cs").read_text(encoding="utf-8")
        host = (
            ROOT / "secure-runtime/src/WorkflowSecureDaemonHost/Program.cs"
        ).read_text(encoding="utf-8")
        launcher = (
            ROOT / "secure-runtime/src/SecureAgentLauncher/Program.cs"
        ).read_text(encoding="utf-8")
        self.assertIn("CreatePushSnapshotAsync", broker)
        self.assertIn('"--git-dir", snapshot', broker)
        self.assertIn("ready?.Invoke()", host)
        self.assertIn("() => SetStatus(ServiceState.Running", host)
        self.assertIn("DeleteTaskRootAsync(prepared.TaskRoot)", launcher)


if __name__ == "__main__":
    unittest.main()
