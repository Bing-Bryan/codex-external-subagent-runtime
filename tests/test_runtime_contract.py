"""Contract tests for the always-on project Agent Runtime."""

import os
import unittest


ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
TEMPLATE = os.path.join(ROOT, "templates", "agents-runtime-block.md")


class RuntimeContractTest(unittest.TestCase):
    def read_template(self):
        with open(TEMPLATE, "r", encoding="utf-8") as handle:
            return handle.read()

    def test_runtime_is_always_on_but_delegation_is_not_mandatory(self):
        text = self.read_template()
        self.assertIn("不需要 Skill 激活词", text)
        self.assertIn("先判断由主 Agent 直接完成，还是拆成有边界的子任务", text)
        self.assertIn("无需委派时由主 Agent 直接完成", text)

    def test_main_agent_owns_decomposition_and_acceptance(self):
        text = self.read_template()
        for phrase in (
            "主 Agent 负责分析、拆分、选路、下发、等待、验收和汇总",
            "spawn 成功不算完成",
            "若宿主暴露 `close_agent`",
            "每个独立委派都创建一个新的叶子 Agent",
            "禁止用 `followup_task` 或 `send_message` 复用",
            "不能冒充关闭",
            "agent_lifecycle_unavailable",
        ):
            self.assertIn(phrase, text)

        self.assertNotIn("`followup_task` 复用", text)

    def test_native_agent_transport_is_explicit_and_v2_safe(self):
        text = self.read_template()
        for phrase in (
            "`transport=message`",
            "`transport=runtime-job`",
            "prepare-job",
            "read-job",
            "claim-job",
            "cleanup-job",
            "`fork_turns=\"none\"`",
            "`fork_context=false`",
            "按宿主实际暴露的参数",
            "encrypted_content",
            "任务文件权限固定为 `0600`",
            "同一时刻全局只允许一个未清理的 `runtime-job`",
            "`CODEX_THREAD_ID` 哈希",
            "不得并发运行任何其他原生 Agent",
        ):
            self.assertIn(phrase, text)

    def test_non_terminal_timeout_keeps_runtime_job_until_ttl(self):
        text = self.read_template()
        self.assertIn("仅在子 Agent 已进入终态时清理", text)
        self.assertIn("非终态超时不得清理", text)
        self.assertNotIn("每次委派无论成功或失败都先清理", text)

    def test_registry_and_task_packet_are_mandatory_route_gates(self):
        text = self.read_template()
        self.assertIn("{{RUNTIME_ROOT}}/scripts/router_registry.py", text)
        self.assertIn(
            "validate-packet --packet-hex PACKET_HEX",
            text,
        )
        for field in (
            "objective",
            "canonicalCwd",
            "inputs",
            "allowedFiles",
            "writePermission",
            "expectedOutput",
            "acceptanceMarker",
            "timeoutSeconds",
        ):
            self.assertIn(field, text)
        for phrase in (
            "--packet-hex PACKET_HEX",
            "const packetJsonAscii",
            "JSON.stringify(packet).replace",
            "character.charCodeAt(0)",
            "不创建临时输入文件",
            "不得单独启动 `--packet -`",
            "十六进制只是安全传输编码，不是加密",
            "secrets.token_hex(8)",
            "规范化系统临时目录",
            "不写 `~/.codex`",
            "不算修改用户项目",
            "明确禁止任何本机控制面写入",
        ):
            self.assertIn(phrase, text)

        self.assertNotIn("new TextEncoder()", text)

    def test_route_surfaces_and_failure_policy_are_honest(self):
        text = self.read_template()
        for phrase in (
            "native-agent",
            "skill-tool",
            "真实子任务卡片",
            "主任务内工具调用",
            "禁止静默 fallback",
            "禁止自动重试",
            "不把 Skill/MCP 包装成假 Agent",
        ):
            self.assertIn(phrase, text)

    def test_kimi_tool_arguments_are_validated_before_the_non_idempotent_call(self):
        text = self.read_template()
        for phrase in (
            "`requestId` 必须是规范 UUID",
            "`allowedWriteRoots` 只接受注册项目 `cwd` 内的相对路径",
            "禁止传绝对路径或项目外系统临时目录",
            "不得把临时目录后缀冒充 UUID",
            "仅验证 MCP 界面与连通性",
            "零写入 transport canary",
            "不得要求创建文件",
        ):
            self.assertIn(phrase, text)

    def test_old_launcher_protocol_is_absent(self):
        text = self.read_template()
        for forbidden in (
            "ENTRY_READY",
            "ONLY_ACCEPTS_NEW",
            "TASK_READY",
            "thread/start",
            "thread/settings/update",
            "Luna",
            "Sol Ultra",
            "置顶入口",
        ):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
