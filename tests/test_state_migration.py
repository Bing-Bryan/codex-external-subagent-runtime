import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "migrate_legacy_state.py"
SPEC = importlib.util.spec_from_file_location("migrate_legacy_state", SCRIPT)
migration = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(migration)


class StateMigrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        self.source = self.home / migration.LEGACY_NAME
        self.source.mkdir(mode=0o700)
        self.payloads = {
            "projects.json": {"version": 1, "projects": []},
            "providers.json": {"version": 2, "providers": []},
            "smoke-evidence.json": {"version": 1, "evidence": []},
        }
        for name, payload in self.payloads.items():
            (self.source / name).write_text(json.dumps(payload), encoding="utf-8")
            os.chmod(self.source / name, 0o600)

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args):
        return subprocess.run(
            ["python3", str(SCRIPT), "--home", str(self.home), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_dry_run_does_not_write_and_reports_hashes(self):
        completed = self.invoke()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["mode"], "dry-run")
        self.assertFalse((self.home / migration.RUNTIME_NAME).exists())
        self.assertEqual(
            {item["name"] for item in result["files"]},
            set(migration.STATE_FILES),
        )
        for item in result["files"]:
            self.assertRegex(item["sha256"], r"^[0-9a-f]{64}$")

    def test_apply_copies_allowlisted_files_and_preserves_source(self):
        before = {
            name: (self.source / name).read_bytes() for name in migration.STATE_FILES
        }
        completed = self.invoke("--apply", "--confirm-source", migration.LEGACY_NAME)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        destination = self.home / migration.RUNTIME_NAME
        self.assertTrue(destination.is_dir())
        self.assertEqual(destination.stat().st_mode & 0o777, 0o700)
        for name in migration.STATE_FILES:
            target = destination / name
            self.assertEqual(target.read_bytes(), before[name])
            self.assertEqual(target.stat().st_mode & 0o777, 0o600)
            self.assertEqual((self.source / name).read_bytes(), before[name])

    def test_apply_requires_exact_confirmation(self):
        completed = self.invoke("--apply", "--confirm-source", "wrong")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stderr)["error"], "source_confirmation_required")
        self.assertFalse((self.home / migration.RUNTIME_NAME).exists())

    def test_existing_destination_and_symlink_fail_closed(self):
        destination = self.home / migration.RUNTIME_NAME
        destination.mkdir()
        completed = self.invoke()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stderr)["error"], "destination_already_exists")

        destination.rmdir()
        (self.source / "providers.json").unlink()
        os.symlink(self.source / "projects.json", self.source / "providers.json")
        completed = self.invoke()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stderr)["error"], "state_file_invalid")

    def test_active_lock_stops_without_creating_destination(self):
        (self.source / "launch.lock").write_text("active", encoding="utf-8")
        completed = self.invoke()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stderr)["error"], "active_runtime_lock")
        self.assertFalse((self.home / migration.RUNTIME_NAME).exists())


if __name__ == "__main__":
    unittest.main()
