import importlib.util
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "codex-external-subagent-bridge"
    / "scripts"
    / "launch_bridge.py"
)
SPEC = importlib.util.spec_from_file_location("launch_v1_sol", MODULE_PATH)
launcher = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(launcher)


def write_config(
    home,
    model="gpt-5.6-luna",
    effort="max",
    multi_agent=True,
    multi_agent_v2=False,
):
    lines = []
    if model is not None:
        lines.append(f'model = "{model}"')
    if effort is not None:
        lines.append(f'model_reasoning_effort = "{effort}"')
    lines.append("[features]")
    lines.append(f"multi_agent = {'true' if multi_agent else 'false'}")
    lines.append(f"multi_agent_v2 = {'true' if multi_agent_v2 else 'false'}")
    (home / "config.toml").write_text("\n".join(lines) + "\n", encoding="utf-8")


class GlobalV1PreflightTest(unittest.TestCase):
    def test_luna_max_global_defaults_pass(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            write_config(home, "gpt-5.6-luna", "max")
            launcher.require_global_v1(home)

    def test_sol_ultra_global_defaults_pass(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            write_config(home, "gpt-5.6-sol", "ultra")
            launcher.require_global_v1(home)

    def test_other_model_effort_pass(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            write_config(home, "some-model", "low")
            launcher.require_global_v1(home)

    def test_missing_model_effort_pass(self):
        with tempfile.TemporaryDirectory() as raw:
            home = Path(raw)
            write_config(home, model=None, effort=None)
            launcher.require_global_v1(home)

    def test_v1_feature_non_compliance_rejected(self):
        cases = ({"multi_agent": False}, {"multi_agent_v2": True})
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with tempfile.TemporaryDirectory() as raw:
                    home = Path(raw)
                    write_config(home, **kwargs)
                    with self.assertRaises(launcher.LauncherError) as ctx:
                        launcher.require_global_v1(home)
                    self.assertEqual(ctx.exception.code, "global_v1_required")


class NoTurnAppServerIntegrationTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.home = self.root / "codex-home"
        self.project = self.root / "project"
        self.runtime = self.home / "codex-external-subagent-bridge"
        self.home.mkdir()
        self.project.mkdir()
        self.runtime.mkdir()
        write_config(self.home)
        self.project_id = "019c1234-5678-7abc-8def-0123456789ab"
        (self.runtime / "projects.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "projects": [
                        {
                            "projectId": self.project_id,
                            "label": "Fixture",
                            "cwd": str(self.project),
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.log = self.root / "requests.jsonl"
        self.fake = Path(__file__).with_name("fake_app_server.py")
        self.fake.chmod(0o755)

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, mode="ok", extra_args=()):
        env = os.environ.copy()
        env.update(
            {
                "CODEX_HOME": str(self.home),
                "CODEX_APP_CLI": str(self.fake),
                "FAKE_APP_LOG": str(self.log),
                "FAKE_APP_MODE": mode,
            }
        )
        return subprocess.run(
            [
                "python3",
                str(MODULE_PATH),
                "--project-id",
                self.project_id,
                "--cwd",
                str(self.project),
                *extra_args,
            ],
            capture_output=True,
            text=True,
            env=env,
            timeout=10,
        )

    def methods(self):
        return [
            item.get("method")
            for item in (
                json.loads(line)
                for line in self.log.read_text(encoding="utf-8").splitlines()
            )
        ]

    def test_zero_bootstrap_turns_and_settings_switch(self):
        completed = self.invoke()
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["bootstrapTurns"], 0)
        self.assertTrue(payload["settingsVerified"])
        methods = self.methods()
        self.assertNotIn("turn/start", methods)
        self.assertEqual(
            [name for name in methods if name != "initialized"],
            [
                "initialize",
                "model/list",
                "thread/start",
                "thread/settings/update",
                "thread/read",
            ],
        )
        requests = [
            json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()
        ]
        started = next(item for item in requests if item.get("method") == "thread/start")
        self.assertEqual(started["params"]["model"], "gpt-5.6-luna")
        self.assertFalse(started["params"]["allowProviderModelFallback"])
        self.assertIn("developerInstructions", started["params"])
        switched = next(
            item for item in requests if item.get("method") == "thread/settings/update"
        )
        self.assertEqual(switched["params"]["model"], "gpt-5.6-sol")
        self.assertEqual(switched["params"]["effort"], "ultra")

    def test_model_catalog_rejection_happens_before_thread_creation(self):
        completed = self.invoke("missing_sol")
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stderr)["error"], "required_model_unavailable")
        self.assertNotIn("thread/start", self.methods())

    def test_settings_failure_reports_thread_and_releases_lock(self):
        completed = self.invoke("settings_error")
        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stderr)
        self.assertEqual(payload["error"], "settings_update_failed")
        self.assertEqual(payload["threadId"], self.project_id)
        self.assertFalse((self.runtime / "launch.lock").exists())

    def test_settings_phase_timeout_is_bounded(self):
        completed = self.invoke(
            "settings_timeout", ("--settings-timeout-seconds", "1")
        )
        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stderr)
        self.assertEqual(payload["error"], "settings_timeout")
        self.assertEqual(payload["threadId"], self.project_id)

    def test_existing_lock_stops_before_app_server(self):
        (self.runtime / "launch.lock").write_text("locked", encoding="utf-8")
        completed = self.invoke()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stderr)["error"], "already_running")
        self.assertFalse(self.log.exists())

    def test_projects_registry_symlink_is_rejected(self):
        original = self.runtime / "projects.json"
        outside = self.root / "outside-projects.json"
        outside.write_bytes(original.read_bytes())
        original.unlink()
        os.symlink(outside, original)
        completed = self.invoke()
        self.assertNotEqual(completed.returncode, 0)
        self.assertEqual(json.loads(completed.stderr)["error"], "projects_registry_invalid")
        self.assertFalse(self.log.exists())


if __name__ == "__main__":
    unittest.main()
