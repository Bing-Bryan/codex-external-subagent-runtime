import unicodedata
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
SKILL = ROOT / "codex-external-subagent-bridge"
ARCHITECTURES = {
    "zh-CN": SKILL / "references" / "architecture.zh-CN.txt",
    "en": SKILL / "references" / "architecture.en.txt",
}


def display_width(line):
    width = 0
    for char in line:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


class ArchitectureTuiTest(unittest.TestCase):
    def test_skill_links_both_terminal_views(self):
        skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
        for path in ARCHITECTURES.values():
            self.assertIn(f"references/{path.name}", skill)

    def test_views_have_matching_journey_and_operation_topology(self):
        required = (
            "VIEW A",
            "VIEW B",
            "[A]",
            "[B]",
            "[C]",
            "[D]",
            "[E]",
            "[1]",
            "[2]",
            "[3]",
            "[4]",
            "[5]",
            "gpt-5.6-luna",
            "gpt-5.6-sol",
            "bootstrapTurns = 0",
            "launch.lock",
        )
        for language, path in ARCHITECTURES.items():
            with self.subTest(language=language):
                text = path.read_text(encoding="utf-8")
                for marker in required:
                    self.assertIn(marker, text)
                self.assertEqual(text.count("VIEW A"), 1)
                self.assertEqual(text.count("VIEW B"), 1)

    def test_views_do_not_expand_runtime_route_classes(self):
        for language, path in ARCHITECTURES.items():
            with self.subTest(language=language):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("responses-direct", text)
                self.assertNotIn("responses-adapter-dedicated", text)
                self.assertNotIn("mcp-tool", text)

    def test_views_preserve_literal_entry_contract(self):
        for language, path in ARCHITECTURES.items():
            with self.subTest(language=language):
                text = path.read_text(encoding="utf-8")
                self.assertIn("ENTRY_READY", text)
                self.assertIn("new", text)
                self.assertIn("ONLY_ACCEPTS_NEW", text)
                self.assertIn('trim(input) == "new"', text)
                self.assertNotIn("新建", text)

    def test_views_fit_an_eighty_column_terminal(self):
        for language, path in ARCHITECTURES.items():
            with self.subTest(language=language):
                lines = path.read_text(encoding="utf-8").splitlines()
                widest = max(display_width(line) for line in lines)
                self.assertLessEqual(widest, 80)
                self.assertFalse(any("\t" in line for line in lines))

    def test_views_do_not_embed_operator_specific_paths(self):
        for language, path in ARCHITECTURES.items():
            with self.subTest(language=language):
                text = path.read_text(encoding="utf-8")
                self.assertNotIn("/Users/", text)
                self.assertNotIn("apiKey", text)


if __name__ == "__main__":
    unittest.main()
