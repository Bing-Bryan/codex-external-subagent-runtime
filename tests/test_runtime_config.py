import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "runtime_config.py"
)


class ConfigPlanApplyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name) / "codex-home"
        self.home.mkdir()
        (self.home / "config.toml").write_text(
            "model = \"existing\"\n[features]\nmulti_agent = true\n",
            encoding="utf-8",
        )
        self.intent = Path(self.temp.name) / "intent.json"
        self.desired = """name = "worker"
model = "vendor-model"
model_provider = "vendor"
[model_providers.vendor]
base_url = "https://secret-host.example/v1"
wire_api = "responses"
"""
        self.intent.write_text(
            json.dumps(
                {
                    "version": 1,
                    "files": [
                        {"path": "agents/worker.toml", "content": self.desired}
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, *args):
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.home)
        return subprocess.run(
            ["python3", str(SCRIPT), *args, "--intent", str(self.intent)],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

    def test_plan_is_zero_write_and_redacted(self):
        before = (self.home / "config.toml").read_bytes()
        completed = self.invoke("plan")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertTrue(payload["ok"])
        self.assertRegex(payload["planSha"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["changes"][0]["path"], "agents/worker.toml")
        self.assertNotIn("secret-host", completed.stdout)
        self.assertFalse((self.home / "agents" / "worker.toml").exists())
        self.assertEqual((self.home / "config.toml").read_bytes(), before)

    def test_apply_requires_both_guard_and_matching_plan_sha(self):
        plan = json.loads(self.invoke("plan").stdout)
        denied = self.invoke("apply", "--plan-sha", plan["planSha"])
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(json.loads(denied.stderr)["error"], "global_write_not_approved")
        self.assertFalse((self.home / "agents" / "worker.toml").exists())

        applied = self.invoke(
            "apply",
            "--plan-sha",
            plan["planSha"],
            "--allow-global-config-write",
        )
        self.assertEqual(applied.returncode, 0, applied.stderr)
        self.assertEqual(
            (self.home / "agents" / "worker.toml").read_text(encoding="utf-8"),
            self.desired,
        )

    def test_conflict_stops_without_overwrite(self):
        plan = json.loads(self.invoke("plan").stdout)
        target = self.home / "agents" / "worker.toml"
        target.parent.mkdir()
        target.write_text("user change\n", encoding="utf-8")
        completed = self.invoke(
            "apply",
            "--plan-sha",
            plan["planSha"],
            "--allow-global-config-write",
        )
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stderr)["error"], "config_conflict")
        self.assertEqual(target.read_text(encoding="utf-8"), "user change\n")

    def test_agent_directory_symlink_is_rejected_without_write(self):
        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        os.symlink(outside, self.home / "agents")
        completed = self.invoke("plan")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stderr)["error"], "config_target_invalid")
        self.assertFalse((outside / "worker.toml").exists())

    def test_existing_file_is_backed_up_before_atomic_replace(self):
        target = self.home / "agents" / "worker.toml"
        target.parent.mkdir()
        original = 'name = "worker"\nmodel = "old"\n'
        target.write_text(original, encoding="utf-8")
        plan = json.loads(self.invoke("plan").stdout)
        completed = self.invoke(
            "apply",
            "--plan-sha",
            plan["planSha"],
            "--allow-global-config-write",
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        backup = Path(payload["backupRoot"]) / "agents" / "worker.toml"
        self.assertEqual(backup.read_text(encoding="utf-8"), original)
        self.assertEqual(target.read_text(encoding="utf-8"), self.desired)


if __name__ == "__main__":
    unittest.main()
