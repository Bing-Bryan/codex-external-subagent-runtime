"""Behavior tests for installing the always-on AGENTS runtime block."""

import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest


REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
CLI_PATH = os.path.join(REPO_ROOT, "scripts", "runtime_admin.py")

BEGIN = "<!-- BEGIN CODEX PROJECT AGENT RUNTIME -->"
END = "<!-- END CODEX PROJECT AGENT RUNTIME -->"


class RuntimeAdminTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = self.temp_dir.name
        self.agents_file = os.path.join(self.root, "AGENTS.md")
        self.template = os.path.join(self.root, "runtime-block.md")
        self.runtime_root = os.path.join(self.root, "runtime")
        self.backup_dir = os.path.join(self.root, "backups")
        os.makedirs(os.path.join(self.runtime_root, "scripts"))
        with open(
            os.path.join(self.runtime_root, "scripts", "router_registry.py"),
            "w",
            encoding="utf-8",
        ) as handle:
            handle.write("# fixture\n")
        with open(self.template, "w", encoding="utf-8") as handle:
            handle.write(
                "# Project Agent Runtime\n\n"
                "Use `{{RUNTIME_ROOT}}/scripts/router_registry.py`.\n"
            )

    def run_cli(self, command, *extra, expect_code=0):
        args = [
            sys.executable,
            CLI_PATH,
            command,
            "--agents-file",
            self.agents_file,
            "--template",
            self.template,
            "--runtime-root",
            self.runtime_root,
            "--backup-dir",
            self.backup_dir,
        ]
        args.extend(extra)
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        self.assertEqual(
            proc.returncode,
            expect_code,
            "stdout=%r\nstderr=%r" % (proc.stdout, proc.stderr),
        )
        self.assertEqual(proc.stderr, "")
        self.assertEqual(proc.stdout.count("\n"), 1)
        return json.loads(proc.stdout)

    def write_agents(self, text="# Existing rules\n"):
        with open(self.agents_file, "w", encoding="utf-8") as handle:
            handle.write(text)

    def read_agents(self):
        with open(self.agents_file, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_plan_reports_install_without_writing(self):
        self.write_agents()
        before = self.read_agents()
        result = self.run_cli("plan")
        self.assertEqual(
            result,
            {"ok": True, "action": "install", "changed": True},
        )
        self.assertEqual(self.read_agents(), before)
        self.assertFalse(os.path.exists(self.backup_dir))

    def test_install_can_create_a_missing_agents_file(self):
        self.assertFalse(os.path.exists(self.agents_file))
        result = self.run_cli("install", "--allow-agents-write")
        self.assertEqual(
            result,
            {"ok": True, "action": "install", "changed": True},
        )
        installed = self.read_agents()
        self.assertEqual(installed.count(BEGIN), 1)
        self.assertEqual(installed.count(END), 1)
        self.assertFalse(os.path.exists(self.backup_dir))

    def test_invalid_arguments_emit_only_the_stable_json_error(self):
        proc = subprocess.run(
            [sys.executable, CLI_PATH, "not-a-command"],
            capture_output=True,
            text=True,
            env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
        )
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stderr, "")
        self.assertEqual(proc.stdout.count("\n"), 1)
        self.assertEqual(
            json.loads(proc.stdout),
            {"ok": False, "error": "usage_error"},
        )

    def test_install_requires_explicit_write_approval(self):
        self.write_agents()
        result = self.run_cli("install", expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "write_approval_required"})
        self.assertEqual(self.read_agents(), "# Existing rules\n")

    def test_install_appends_one_rendered_block_and_creates_backup(self):
        original = "# Existing rules\n\nKeep this.\n"
        self.write_agents(original)
        os.chmod(self.agents_file, 0o640)
        result = self.run_cli("install", "--allow-agents-write")
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["action"], "install")
        self.assertEqual(result["changed"], True)
        updated = self.read_agents()
        self.assertTrue(updated.startswith(original))
        self.assertEqual(updated.count(BEGIN), 1)
        self.assertEqual(updated.count(END), 1)
        self.assertIn(self.runtime_root + "/scripts/router_registry.py", updated)
        self.assertEqual(stat.S_IMODE(os.stat(self.agents_file).st_mode), 0o640)

        backups = []
        for directory, _subdirs, files in os.walk(self.backup_dir):
            for filename in files:
                backups.append(os.path.join(directory, filename))
        self.assertEqual(len(backups), 1)
        self.assertEqual(stat.S_IMODE(os.stat(backups[0]).st_mode), 0o600)
        with open(backups[0], "r", encoding="utf-8") as handle:
            self.assertEqual(handle.read(), original)

    def test_install_is_idempotent(self):
        self.write_agents()
        first = self.run_cli("install", "--allow-agents-write")
        installed = self.read_agents()
        second = self.run_cli("install", "--allow-agents-write")
        self.assertEqual(first["changed"], True)
        self.assertEqual(second, {"ok": True, "action": "none", "changed": False})
        self.assertEqual(self.read_agents(), installed)

    def test_install_updates_managed_block_and_preserves_outside_content(self):
        self.write_agents()
        self.run_cli("install", "--allow-agents-write")
        with open(self.template, "w", encoding="utf-8") as handle:
            handle.write("# Updated Runtime\n\nPath: `{{RUNTIME_ROOT}}`.\n")
        result = self.run_cli("install", "--allow-agents-write")
        self.assertEqual(result["action"], "update")
        updated = self.read_agents()
        self.assertTrue(updated.startswith("# Existing rules\n"))
        self.assertIn("# Updated Runtime", updated)
        self.assertNotIn("# Project Agent Runtime", updated)
        self.assertEqual(updated.count(BEGIN), 1)

    def test_status_distinguishes_current_and_outdated_blocks(self):
        self.write_agents()
        missing = self.run_cli("status")
        self.assertEqual(
            missing,
            {"ok": True, "installed": False, "current": False},
        )
        self.run_cli("install", "--allow-agents-write")
        current = self.run_cli("status")
        self.assertEqual(current, {"ok": True, "installed": True, "current": True})
        with open(self.template, "w", encoding="utf-8") as handle:
            handle.write("# Changed\n{{RUNTIME_ROOT}}\n")
        outdated = self.run_cli("status")
        self.assertEqual(outdated, {"ok": True, "installed": True, "current": False})

    def test_uninstall_requires_approval_then_preserves_other_rules(self):
        self.write_agents("# Before\n")
        self.run_cli("install", "--allow-agents-write")
        denied = self.run_cli("uninstall", expect_code=2)
        self.assertEqual(denied, {"ok": False, "error": "write_approval_required"})
        result = self.run_cli("uninstall", "--allow-agents-write")
        self.assertEqual(result["action"], "uninstall")
        self.assertEqual(result["changed"], True)
        self.assertEqual(self.read_agents(), "# Before\n")

    def test_malformed_or_duplicate_markers_fail_closed(self):
        fixtures = (
            "# Rules\n" + BEGIN + "\nmissing end\n",
            BEGIN + "\none\n" + END + "\n" + BEGIN + "\ntwo\n" + END + "\n",
        )
        for fixture in fixtures:
            with self.subTest(fixture=fixture):
                self.write_agents(fixture)
                result = self.run_cli(
                    "install", "--allow-agents-write", expect_code=2
                )
                self.assertEqual(
                    result, {"ok": False, "error": "managed_block_conflict"}
                )
                self.assertEqual(self.read_agents(), fixture)

    def test_symlink_agents_file_is_rejected(self):
        target = os.path.join(self.root, "real-agents.md")
        with open(target, "w", encoding="utf-8") as handle:
            handle.write("# Real\n")
        os.symlink(target, self.agents_file)
        result = self.run_cli("plan", expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "agents_file_unsafe"})

    def test_existing_lock_fails_without_writing(self):
        self.write_agents()
        lock_path = self.agents_file + ".codex-project-agent-runtime.lock"
        with open(lock_path, "w", encoding="utf-8") as handle:
            handle.write("busy\n")
        result = self.run_cli(
            "install", "--allow-agents-write", expect_code=2
        )
        self.assertEqual(result, {"ok": False, "error": "runtime_lock_busy"})
        self.assertEqual(self.read_agents(), "# Existing rules\n")

    def test_stale_dead_process_lock_is_recovered(self):
        self.write_agents()
        lock_path = self.agents_file + ".codex-project-agent-runtime.lock"
        with open(lock_path, "w", encoding="utf-8") as handle:
            handle.write("99999999\n")
        old = 1_600_000_000
        os.utime(lock_path, (old, old))
        result = self.run_cli("install", "--allow-agents-write")
        self.assertEqual(
            result,
            {"ok": True, "action": "install", "changed": True},
        )
        self.assertFalse(os.path.exists(lock_path))

    def test_missing_template_or_runtime_helper_fails_closed(self):
        self.write_agents()
        os.unlink(self.template)
        missing_template = self.run_cli("plan", expect_code=2)
        self.assertEqual(
            missing_template, {"ok": False, "error": "runtime_contract_missing"}
        )
        with open(self.template, "w", encoding="utf-8") as handle:
            handle.write("{{RUNTIME_ROOT}}\n")
        os.unlink(os.path.join(self.runtime_root, "scripts", "router_registry.py"))
        missing_helper = self.run_cli("plan", expect_code=2)
        self.assertEqual(
            missing_helper, {"ok": False, "error": "runtime_contract_missing"}
        )


if __name__ == "__main__":
    unittest.main()
