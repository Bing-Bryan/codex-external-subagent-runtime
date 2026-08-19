import unicodedata
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ARCHITECTURES = {
    "zh-CN": (ROOT / "README.zh-CN.md", "## 架构 / 终端流程", "## 路由契约"),
    "en": (ROOT / "README.md", "## Architecture / terminal flow", "## Route contract"),
}


def architecture_text(path, start_marker, end_marker):
    readme = path.read_text(encoding="utf-8")
    start = readme.index(start_marker) + len(start_marker)
    end = readme.index(end_marker, start)
    section = readme[start:end]
    return section.split("```text\n", 1)[1].split("\n```", 1)[0]


def display_width(line):
    width = 0
    for char in line:
        if unicodedata.combining(char):
            continue
        width += 2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
    return width


class ArchitectureTuiTest(unittest.TestCase):
    def test_readmes_embed_both_terminal_views(self):
        image = ROOT / "docs" / "runtime-overview.png"
        self.assertTrue(image.is_file())
        self.assertIn("standalone runtime repository", ARCHITECTURES["en"][0].read_text(encoding="utf-8"))
        self.assertIn("独立仓库", ARCHITECTURES["zh-CN"][0].read_text(encoding="utf-8"))
        for path, start_marker, end_marker in ARCHITECTURES.values():
            readme = path.read_text(encoding="utf-8")
            self.assertIn("(docs/runtime-overview.png)", readme)
            self.assertIn(start_marker, readme)
            self.assertIn(end_marker, readme)

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
            "experimentalApi=true",
            "bootstrapTurns = 0",
            "launch.lock",
        )
        for language, (path, start_marker, end_marker) in ARCHITECTURES.items():
            with self.subTest(language=language):
                text = architecture_text(path, start_marker, end_marker)
                for marker in required:
                    self.assertIn(marker, text)
                self.assertEqual(text.count("VIEW A"), 1)
                self.assertEqual(text.count("VIEW B"), 1)

    def test_views_do_not_expand_runtime_route_classes(self):
        for language, (path, start_marker, end_marker) in ARCHITECTURES.items():
            with self.subTest(language=language):
                text = architecture_text(path, start_marker, end_marker)
                self.assertNotIn("responses-direct", text)
                self.assertNotIn("responses-adapter-dedicated", text)
                self.assertNotIn("mcp-tool", text)

    def test_views_preserve_literal_entry_contract(self):
        for language, (path, start_marker, end_marker) in ARCHITECTURES.items():
            with self.subTest(language=language):
                text = architecture_text(path, start_marker, end_marker)
                self.assertIn("ENTRY_READY", text)
                self.assertIn("new", text)
                self.assertIn("ONLY_ACCEPTS_NEW", text)
                self.assertIn('trim(input) == "new"', text)
                self.assertNotIn("新建", text)

    def test_views_fit_an_eighty_column_terminal(self):
        for language, (path, start_marker, end_marker) in ARCHITECTURES.items():
            with self.subTest(language=language):
                lines = architecture_text(path, start_marker, end_marker).splitlines()
                widest = max(display_width(line) for line in lines)
                self.assertLessEqual(widest, 80)
                self.assertFalse(any("\t" in line for line in lines))

    def test_views_do_not_embed_operator_specific_paths(self):
        for language, (path, start_marker, end_marker) in ARCHITECTURES.items():
            with self.subTest(language=language):
                text = architecture_text(path, start_marker, end_marker)
                self.assertNotIn("/Users/", text)
                self.assertNotIn("apiKey", text)


if __name__ == "__main__":
    unittest.main()
