import json
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "codex-external-subagent-bridge" / "scripts" / "validate_registry.py"
PROJECTS = ROOT / "codex-external-subagent-bridge" / "references" / "projects.example.json"
PROVIDERS = ROOT / "codex-external-subagent-bridge" / "references" / "providers.example.json"


class RegistryCliTest(unittest.TestCase):
    def test_examples_validate_without_runtime_evaluation(self):
        completed = subprocess.run(
            [
                "python3",
                str(SCRIPT),
                "--projects",
                str(PROJECTS),
                "--providers",
                str(PROVIDERS),
                "--allow-missing-cwds",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["providers"]["version"], 2)
        self.assertEqual(payload["providers"]["enabled"], 0)

    def test_unknown_registry_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "providers.json"
            data = json.loads(PROVIDERS.read_text(encoding="utf-8"))
            data["providers"][0]["baseUrl"] = "https://must-not-live-here.example"
            path.write_text(json.dumps(data), encoding="utf-8")
            completed = subprocess.run(
                [
                    "python3",
                    str(SCRIPT),
                    "--projects",
                    str(PROJECTS),
                    "--providers",
                    str(path),
                    "--allow-missing-cwds",
                ],
                capture_output=True,
                text=True,
                timeout=10,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(
                json.loads(completed.stderr)["error"],
                "providers_registry_schema_invalid",
            )


if __name__ == "__main__":
    unittest.main()
