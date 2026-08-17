import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SCRIPT = (
    ROOT
    / "codex-external-subagent-bridge"
    / "scripts"
    / "record_smoke_evidence.py"
)


class SmokeEvidenceRecorderTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.home = Path(self.temp.name)
        (self.home / "agents").mkdir()
        (self.home / "agents" / "worker.toml").write_text(
            """name = "worker"
model = "vendor-model"
model_provider = "vendor"
[model_providers.vendor]
base_url = "https://provider.example/v1"
wire_api = "responses"
""",
            encoding="utf-8",
        )
        self.runtime = self.home / "codex-external-subagent-bridge"
        self.runtime.mkdir()
        (self.runtime / "providers.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "providers": [
                        {
                            "providerId": "vendor-child",
                            "label": "Vendor",
                            "transport": "responses-direct",
                            "enabled": False,
                            "agentType": "worker",
                            "notes": "Locally defined",
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def invoke(self, confirmed=False):
        env = os.environ.copy()
        env["CODEX_HOME"] = str(self.home)
        args = ["python3", str(SCRIPT), "--provider-id", "vendor-child"]
        if confirmed:
            args.append("--confirm-observed-delivery")
        return subprocess.run(
            args, capture_output=True, text=True, env=env, timeout=10
        )

    def test_confirmation_is_required_and_recorder_performs_no_call(self):
        denied = self.invoke()
        self.assertNotEqual(denied.returncode, 0)
        self.assertEqual(
            json.loads(denied.stderr)["error"], "smoke_delivery_not_confirmed"
        )
        evidence = self.runtime / "smoke-evidence.json"
        self.assertFalse(evidence.exists())

        recorded = self.invoke(confirmed=True)
        self.assertEqual(recorded.returncode, 0, recorded.stderr)
        payload = json.loads(recorded.stdout)
        self.assertFalse(payload["externalCallPerformed"])
        saved = json.loads(evidence.read_text(encoding="utf-8"))
        self.assertEqual(saved["evidence"][0]["providerId"], "vendor-child")
        self.assertRegex(
            saved["evidence"][0]["configFingerprint"], r"^sha256:[0-9a-f]{64}$"
        )


if __name__ == "__main__":
    unittest.main()
