"""Repository-level release contract tests."""

import json
import os
import re
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


def read_text(relative_path):
    with open(os.path.join(ROOT, relative_path), "r", encoding="utf-8") as handle:
        return handle.read()


class RepositoryContractTest(unittest.TestCase):
    def test_repository_is_a_runtime_not_a_skill(self):
        self.assertFalse(os.path.exists(os.path.join(ROOT, "SKILL.md")))
        for relative_path in ("README.md", "README.zh-CN.md"):
            text = read_text(relative_path)
            self.assertNotIn("$codex-project-agent-router", text)
        self.assertIn("not an Agent Skill", read_text("README.md"))
        self.assertIn("不是 Agent Skill", read_text("README.zh-CN.md"))

    def test_public_docs_explain_v1_v2_host_boundaries(self):
        english = " ".join(read_text("README.md").split())
        chinese = " ".join(read_text("README.zh-CN.md").split())
        self.assertIn("does not switch or require a global Multi-Agent version", english)
        self.assertIn("Skill and MCP routes are outside the native V1/V2 child lifecycle", english)
        self.assertIn("不会切换或强制要求某个全局 Multi-Agent 版本", chinese)
        self.assertIn("Skill 与 MCP 路线不属于原生 V1/V2 子 Agent 生命周期", chinese)
        self.assertIn('V1 host exposes `fork_context=false`', english)
        self.assertIn('V2 host exposes `fork_turns="none"`', english)
        self.assertIn('V1 宿主暴露 `fork_context=false`', chinese)
        self.assertIn('V2 宿主暴露 `fork_turns="none"`', chinese)
        self.assertIn(
            "runtime-job requires a host-provided `CODEX_THREAD_ID`",
            english,
        )
        self.assertIn(
            "runtime-job` 要求宿主提供 `CODEX_THREAD_ID`",
            chinese,
        )

    def test_runtime_job_examples_use_a_valid_task_suffix(self):
        english = read_text("README.md")
        self.assertNotIn("deepseek_worker_TASK_UNIQUE", english)
        self.assertIn("deepseek_worker_0123456789abcdef", english)

    def test_repository_does_not_publish_personal_fixture_paths(self):
        for relative_path in (
            "tests/test_router_registry.py",
            "README.md",
            "README.zh-CN.md",
        ):
            text = read_text(relative_path)
            self.assertIsNone(
                re.search(r"/Users/(?!example(?:/|$))[^/\s]+", text),
            )
            self.assertIsNone(
                re.search(r"/var/folders/[a-z0-9]{2}/[a-z0-9]{20,}/T/", text),
            )

    def test_examples_are_valid_json_and_disabled_by_default(self):
        routes = json.loads(read_text("examples/routes.example.json"))
        evidence = json.loads(read_text("examples/smoke-evidence.example.json"))
        self.assertTrue(routes["routes"])
        self.assertTrue(all(route["enabled"] is False for route in routes["routes"]))
        self.assertEqual(
            {route["transport"] for route in routes["routes"]},
            {"message", "runtime-job", "direct"},
        )
        self.assertEqual(evidence["evidence"], [])

    def test_ci_covers_supported_python_versions(self):
        workflow = read_text(".github/workflows/ci.yml")
        self.assertIn('"3.9"', workflow)
        self.assertIn('"3.11"', workflow)
        self.assertIn("unittest discover", workflow)
        self.assertIn("compileall", workflow)
        self.assertIn("git show --check --format= HEAD", workflow)
        self.assertNotIn("actions/checkout@v4", workflow)
        self.assertNotIn("actions/setup-python@v5", workflow)

    def test_public_docs_state_the_posix_runtime_requirement(self):
        english = " ".join(read_text("README.md").split())
        chinese = " ".join(read_text("README.zh-CN.md").split())
        self.assertIn("macOS or another POSIX host", english)
        self.assertIn("macOS 或其他 POSIX 宿主", chinese)

    def test_public_docs_state_the_host_enforcement_limit(self):
        english = " ".join(read_text("README.md").split())
        chinese = " ".join(read_text("README.zh-CN.md").split())
        self.assertIn("not a host-level prompt interceptor", english)
        self.assertIn("不是 Codex 宿主层的 prompt 拦截器", chinese)

    def test_public_docs_keep_ephemeral_jobs_out_of_codex_state(self):
        english = " ".join(read_text("README.md").split())
        chinese = " ".join(read_text("README.zh-CN.md").split())
        self.assertIn("canonical system temporary directory", english)
        self.assertIn("without a global-write approval", english)
        self.assertIn("规范化系统临时目录", chinese)
        self.assertIn("无需申请全局写权限", chinese)


if __name__ == "__main__":
    unittest.main()
