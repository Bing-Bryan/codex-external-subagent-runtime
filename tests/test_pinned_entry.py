import subprocess
import unittest
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
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
        self.assertEqual(completed.stdout, "ENTRY_READY\n")
        self.assertEqual(completed.stderr, "")

    def test_exact_new_crosses_message_gate_without_launch(self):
        for value in ("new", " new "):
            with self.subTest(value=value):
                completed = self.invoke("--message", value)
                self.assertEqual(completed.returncode, 1)
                self.assertEqual(completed.stdout, "")
                self.assertEqual(
                    completed.stderr,
                    '{"ok":false,"error":"pinned_binding_missing"}\n',
                )

    def test_every_other_message_is_rejected_without_launch(self):
        for value in ("新建", "New", "NEW", "new task", "new.", "please new", ""):
            with self.subTest(value=value):
                completed = self.invoke("--message", value)
                self.assertEqual(completed.returncode, 0)
                self.assertEqual(completed.stdout, "ONLY_ACCEPTS_NEW\n")
                self.assertEqual(completed.stderr, "")


if __name__ == "__main__":
    unittest.main()
