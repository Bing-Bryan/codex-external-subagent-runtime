import subprocess
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[3]
    / "codex-external-subagent-bridge"
    / "scripts"
    / "pinned_entry.py"
)


class PinnedEntryTest(unittest.TestCase):
    def invoke(self, *args):
        return subprocess.run(
            ["python3", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=10,
        )

    def test_ready_token_is_exact(self):
        completed = self.invoke("--ready")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "入口已就绪\n")

    def test_near_match_is_rejected_without_launch(self):
        for value in ("new", "新 建", "请新建", "新建。"):
            with self.subTest(value=value):
                completed = self.invoke("--message", value)
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "只接受「新建」\n")


if __name__ == "__main__":
    unittest.main()
