import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[3]
    / "codex-external-subagent-bridge"
    / "scripts"
    / "bridge_runtime.py"
)
SPEC = importlib.util.spec_from_file_location("bridge_runtime", MODULE_PATH)
runtime = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(runtime)


class RuntimeAllowlistTest(unittest.TestCase):
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
        (self.home / "config.toml").write_text(
            """[features]
multi_agent = true
multi_agent_v2 = false
[mcp_servers.public_x]
command = "safe-wrapper"
""",
            encoding="utf-8",
        )
        self.providers = self.home / "providers.json"
        self.evidence = self.home / "smoke-evidence.json"
        self.providers.write_text(
            json.dumps(
                {
                    "version": 2,
                    "providers": [
                        {
                            "providerId": "vendor-child",
                            "label": "Private label",
                            "transport": "responses-direct",
                            "enabled": True,
                            "agentType": "worker",
                            "notes": "Never inject this note",
                        },
                        {
                            "providerId": "public-x",
                            "label": "Tool label",
                            "transport": "mcp-tool",
                            "enabled": True,
                            "mcpServer": "public_x",
                            "toolName": "search_public_x",
                            "readOnly": True,
                            "notes": "Never inject this note either",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp.cleanup()

    def write_passed_evidence(self):
        agent_fingerprint = runtime.agent_config_fingerprint(self.home, "worker")
        mcp_fingerprint = runtime.mcp_config_fingerprint(self.home, "public_x")
        self.evidence.write_text(
            json.dumps(
                {
                    "version": 1,
                    "evidence": [
                        {
                            "providerId": "vendor-child",
                            "status": "passed",
                            "deliveryKind": "v1-child",
                            "configFingerprint": agent_fingerprint,
                            "testedAt": "2026-08-17T00:00:00Z",
                        },
                        {
                            "providerId": "public-x",
                            "status": "passed",
                            "deliveryKind": "mcp-tool",
                            "configFingerprint": mcp_fingerprint,
                            "testedAt": "2026-08-17T00:00:00Z",
                        },
                    ],
                }
            ),
            encoding="utf-8",
        )

    def test_only_enabled_smoked_fingerprint_matching_routes_are_allowed(self):
        self.write_passed_evidence()
        result = runtime.build_runtime_allowlist(
            self.home, self.providers, self.evidence
        )
        self.assertEqual(
            [route["providerId"] for route in result["allowed"]],
            ["public-x", "vendor-child"],
        )
        serialized = json.dumps(result["allowed"], sort_keys=True)
        self.assertNotIn("provider.example", serialized)
        self.assertNotIn("Private label", serialized)
        self.assertNotIn("Never inject", serialized)

    def test_config_drift_disables_route_without_repair(self):
        self.write_passed_evidence()
        agent = self.home / "agents" / "worker.toml"
        before = agent.read_bytes()
        agent.write_text(agent.read_text() + "# drift\n", encoding="utf-8")
        drifted = agent.read_bytes()
        result = runtime.build_runtime_allowlist(
            self.home, self.providers, self.evidence
        )
        self.assertNotIn("vendor-child", [item["providerId"] for item in result["allowed"]])
        rejected = {item["providerId"]: item["reason"] for item in result["rejected"]}
        self.assertEqual(rejected["vendor-child"], "config_fingerprint_mismatch")
        self.assertEqual(agent.read_bytes(), drifted)
        self.assertNotEqual(before, drifted)

    def test_missing_smoke_evidence_fails_closed(self):
        result = runtime.build_runtime_allowlist(
            self.home, self.providers, self.evidence
        )
        self.assertEqual(result["allowed"], [])
        self.assertEqual(
            {item["reason"] for item in result["rejected"]},
            {"local_smoke_required"},
        )

    def test_agent_directory_symlink_is_rejected(self):
        outside = self.home / "outside-agents"
        outside.mkdir()
        shutil.copy2(self.home / "agents" / "worker.toml", outside / "worker.toml")
        shutil.rmtree(self.home / "agents")
        os.symlink(outside, self.home / "agents")
        with self.assertRaises(runtime.BridgeRuntimeError) as ctx:
            runtime.agent_config_fingerprint(self.home, "worker")
        self.assertEqual(ctx.exception.code, "route_config_invalid")


if __name__ == "__main__":
    unittest.main()
