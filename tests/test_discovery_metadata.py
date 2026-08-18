import json
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "codex-external-subagent-bridge"


class DiscoveryMetadataTest(unittest.TestCase):
    def test_canonical_metadata_is_bilingual_and_explicit_only(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        metadata = (SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")
        self.assertIn("name: codex-external-subagent-bridge", skill)
        self.assertIn("当用户明确要求", skill)
        self.assertIn("Use this Codex Desktop-only", skill)
        self.assertIn("allow_implicit_invocation: false", metadata)

    def test_v2_boundary_is_explicit(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        normalized = " ".join(skill.split())
        self.assertIn("GPT cannot reliably deliver an executable task", normalized)
        self.assertIn("agent_message", normalized)
        self.assertIn("encrypted_content", normalized)
        self.assertIn("global_v1_required", normalized)
        self.assertIn("multi_agent_v2 = false", normalized)

    def test_dynamic_discovery_matrix_has_all_case_kinds(self):
        matrix = json.loads(
            Path(__file__).with_name("discovery_prompts.json").read_text(encoding="utf-8")
        )
        self.assertEqual(
            {item["kind"] for item in matrix["cases"]},
            {"positive", "near-negative", "ambiguous"},
        )
        self.assertTrue(all(item["observed"] and item["rationale"] for item in matrix["cases"]))


if __name__ == "__main__":
    unittest.main()
