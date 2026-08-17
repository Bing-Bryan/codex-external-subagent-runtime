---
name: codex-external-subagent-bridge
description: >-
  Use this Codex Desktop-only launcher and route-contract bridge when an
  approved project needs a fresh Multi-Agent V1 task that starts with GPT-5.6
  Luna, switches without a bootstrap turn to GPT-5.6 Sol Ultra, and exposes
  only locally smoke-tested external child or MCP routes. 当用户明确要求在 Codex
  Desktop 的项目置顶入口中输入精确“新建”，创建 V1 Sol Ultra 任务，并只使用本机已配置、
  已冒烟且配置指纹一致的外部子 Agent 或只读 MCP 工具时使用。不要用于普通对话、通用
  Agent、凭据配置、自动修复配置或未验证的 Provider 声明。
---

# Codex External Subagent Bridge

This is a Codex Desktop launcher plus a runtime route contract. It is not a
general external-model router and does not ship provider credentials,
endpoints, adapters, or private MCP implementations.

## Architecture

Read the terminal diagram that matches the operator's language before changing
launch or route boundaries:

- Chinese: `references/architecture.zh-CN.txt`
- English: `references/architecture.en.txt`

Both views show the post-install user launch journey and the internal operation
triggered by one exact `新建`. They do not depict installation or the new task's
later runtime routing. Treat optional configuration `apply` as an isolated
operator action, not part of either view.

## Do not use

Do not invoke this Skill for ordinary tasks, provider recommendations, Cursor,
Claude Code, pi, or another generic Agent host. Do not use it to install MCP
servers, switch CC Switch, access Keychain, change global defaults, or turn
author evidence into local enablement.

## Default configuration mode

Use `adopt-existing`:

- Read existing named-agent TOML and MCP configuration.
- Validate route shape and compute a configuration fingerprint.
- Never generate, overwrite, repair, or switch those configurations.
- Never read credential values. The runtime allowlist contains no URL, key,
  environment value, label, note, or raw configuration.

Place operator-owned registries under
`$CODEX_HOME/codex-external-subagent-bridge/`:

- `projects.json`, based on `references/projects.example.json`.
- `providers.json`, based on `references/providers.example.json`.
- `smoke-evidence.json`, created only after a real, user-approved smoke test.

Validate structure without invoking any provider:

```bash
python3 scripts/validate_registry.py
```

No provider priority exists. A route is exposed only when all three conditions
hold: `enabled: true`, local smoke evidence is `passed`, and its current
configuration fingerprint matches the recorded fingerprint. Otherwise the
route is rejected without fallback or repair.

## Deterministic pinned entry

Each approved Desktop project uses one pinned launcher task. The entry itself
must not process the user's real work.

1. On initial setup, output exactly `入口已就绪`.
2. Accept only a trimmed, exact `新建`.
3. For every other message, output exactly `只接受「新建」`.
4. On `新建`, call `scripts/pinned_entry.py` with the entry's fixed
   `projectId` and canonical `cwd`. Never ask the user to supply them.
5. A live global lock returns `already_running`; never retry silently.

The deterministic helper provides `--ready` and `--message`. Do not replace its
string check with an LLM interpretation.

## Launch protocol

The launcher performs this App Server sequence:

```text
initialize
model/list
thread/start(model=gpt-5.6-luna, V1, developerInstructions=allowlist)
thread/settings/update(model=gpt-5.6-sol, effort=ultra)
thread/read(includeTurns=true)
```

It sends no Luna prompt, no Sol handoff prompt, and no other `turn/start`.
`allowProviderModelFallback` is false. Before creating the task, `model/list`
must contain both required models and must report `ultra` for Sol.

The launcher uses separate bounded timeouts for startup, model discovery,
thread creation, settings update, and thread read. On a post-creation failure,
return the recoverable `threadId`; do not auto-create another task.

Accept launcher success only when the result has:

- `ok: true`
- the exact requested project ID and canonical cwd
- a UUID `threadId`
- `startModel: gpt-5.6-luna`
- `model: gpt-5.6-sol` and `effort: ultra`
- `multiAgentVersion: v1`
- `bootstrapTurns: 0`
- `settingsVerified: true`
- `projectBinding: desktop_required`

## Desktop completion

After launcher success, use Desktop task controls without sending a sync turn:

1. Set a distinguishable title such as
   `V1-Sol | <projectLabel>`.
2. List tasks and locate the returned `threadId`.
3. Verify the exact Desktop project ID and canonical cwd.
4. Navigate only after title and binding checks pass.

If verification or navigation fails, report the created `threadId`. Only the
Desktop lookup can turn `desktop_required` into a verified project binding.

## Route classes

- `responses-direct`: an existing named agent whose configured endpoint has
  passed the installed Codex Responses protocol and V1 child delivery test.
- `responses-adapter-dedicated`: an existing named agent backed by one
  immutable provider-specific Responses adapter.
- `mcp-tool`: one bounded, read-only MCP capability. A CLI behind MCP remains
  a tool, not a child model.

Sol Ultra remains the root planner and may use only the sanitized route
allowlist injected into the new task's fixed `developerInstructions`. It must
not infer another provider, alter config, switch a shared proxy, or silently
fall back.

Read `references/provider-routing.md` before enabling a route.

## Smoke evidence

A registry entry or author statement is not local delivery evidence. Obtain
the user's separate confirmation before each real external smoke call. Verify
task-specific delivery and provider identity, then record the current
fingerprint:

```bash
python3 scripts/record_smoke_evidence.py \
  --provider-id PROVIDER_ID \
  --confirm-observed-delivery
```

The recorder does not call the provider; it only records an already observed,
user-approved result. `launch` never runs a smoke call, and neither `launch`
nor smoke recording can invoke configuration `apply`.

Author evidence is descriptive only:

- DeepSeek completed a V1 model-child task in the author's setup.
- Kimi completed a V1 model-child task while the author's CC Switch route was
  fixed to Kimi.
- Grok completed a bounded read-only CLI/MCP tool flow; it was not a child.

Every installation must smoke-test its own routes.

## Optional public configuration helper

This helper is isolated from launch:

```bash
python3 scripts/bridge_config.py plan --intent CONFIG_INTENT.json
python3 scripts/bridge_config.py apply \
  --intent CONFIG_INTENT.json \
  --plan-sha PLAN_SHA \
  --allow-global-config-write
```

`plan` performs zero writes and emits only hashes, paths, operations, and
changed key names. `apply` requires both the exact fresh plan SHA and the
explicit global-write flag. It locks, backs up existing targets, stages files,
atomically replaces them, and stops on drift. It accepts only `config.toml` and
`agents/*.toml` targets.

Never call `apply` on behalf of this operator's local setup. Tests for `apply`
must use a temporary `CODEX_HOME`.

## Safety

- Support is limited to Codex Desktop and its bundled App Server.
- Keep project allowlists and all Codex state outside project directories.
- Do not install MCP, write Keychain, change authentication, switch CC Switch,
  or edit global model/Multi-Agent flags during launch or smoke testing.
- Keep prompts, project content, credentials, environment values, full URLs,
  and raw event streams out of registries, allowlists, and errors.
- Never claim a provider works from config parsing, process spawn, or
  documentation alone.
