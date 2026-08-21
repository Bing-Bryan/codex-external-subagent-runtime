# Changelog

## [1.0.0] - 2026-08-20

### Added

- Provide an always-on project routing Runtime loaded from a managed global
  `AGENTS.md` block.
- Let the main Agent analyze, decompose, route, verify, and summarize ordinary
  project prompts without a Skill trigger or special top-level task.
- Support honest `native-agent` cards and `skill-tool` calls behind registry,
  smoke-evidence, fingerprint, permission, and acceptance-marker gates.
- Validate routes, local smoke evidence, configuration fingerprints, and task
  packets deterministically.
- Locking, backup, atomic install/update, status, and uninstall commands.
- Native-Agent delivery through `message` or a short-lived `runtime-job`
  transport, plus direct Skill and MCP tool calls.
- Lowercase UTF-8 hex packet transport for Desktop orchestration, avoiding both
  an unpaired stdin gate and a temporary input-file approval.
- Store ephemeral runtime jobs in the current user's secure system temporary
  directory so project sandboxes do not require global Codex-directory writes.
- Support the host's V1 and V2 Agent interfaces without changing global
  Multi-Agent settings.
- Bind runtime-job claims and cleanup to hashed Codex thread identities and
  fail closed when delivery or lifecycle acceptance cannot be verified.
