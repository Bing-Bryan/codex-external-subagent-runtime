# Changelog

## [1.0.0] - 2026-08-20

### Changed

- Replace the project-pinned Luna-to-Sol launcher with an always-on project
  routing Runtime loaded from a managed global `AGENTS.md` block.
- Let the main Agent analyze, decompose, route, verify, and summarize ordinary
  project prompts without a Skill trigger or special top-level task.
- Support honest `native-agent` cards and `skill-tool` calls behind registry,
  smoke-evidence, fingerprint, permission, and acceptance-marker gates.
- Document that the host currently selects V1 or V2; the Runtime never changes
  those global flags. V2 external-Agent delivery uses the bounded
  `runtime-job` compatibility transport when needed.

### Removed

- Remove `new`, pinned-entry, Luna bootstrap, Sol settings switching, App
  Server, Desktop project-binding, and legacy provider-registry launcher code.

### Migration

- See `MIGRATION.md`. The last launcher-only revision is `2b39a7f`.

## [0.1.1] - 2026-08-20

### Fixed

- Generate packet hex without relying on the unavailable Desktop
  `TextEncoder` global.
- Allow normalized high-entropy temporary paths while retaining explicit
  token, key, credential-assignment, and URL-userinfo rejection.

## [0.1.0] - 2026-08-20

### Added

- Always-on global `AGENTS.md` runtime policy with no Skill activation step.
- Deterministic route, local smoke-evidence, configuration-fingerprint, and task-packet validation.
- Honest native-Agent card and Skill/MCP tool-call execution surfaces.
- Locking, backup, atomic install/update, status, and uninstall commands.
- Native-Agent delivery through `message` or a short-lived `runtime-job`
  transport for Multi-Agent V2 external Agents
  that cannot read `encrypted_content` delegation payloads.
- Lowercase UTF-8 hex packet transport for Desktop orchestration, avoiding both
  an unpaired stdin gate and a temporary input-file approval.
- Clarify that private runtime jobs are control-plane metadata rather than
  project modifications, while honoring an explicit no-control-write rule.
- Store ephemeral runtime jobs in the current user's secure system temporary
  directory so project sandboxes do not require global Codex-directory writes.
- Handle the current V2 lifecycle honestly: verify terminal Agents, never
  confuse `interrupt_agent` with `close_agent`, and use a fresh Agent for each
  external task because encrypted follow-up messages are not reliably readable
  by third-party Providers.
- Add fail-closed `claim-job` discovery for V2 hosts that omit both readable
  payload and task name, with a globally serialized external runtime-job queue.
- Bind claims and cleanup to hashed Codex thread identities, write jobs
  atomically, and recover malformed private queue artifacts under lock.
