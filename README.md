# Codex External Subagent Runtime

[简体中文](README.zh-CN.md)

This standalone runtime repository packages a Bootstrap, Launcher, and Runtime
Route Contract for Codex Desktop.

The runtime creates a project-bound Multi-Agent V1 task with GPT-5.6 Luna,
switches the same task to GPT-5.6 Sol Ultra without a bootstrap turn, and
passes only a sanitized allowlist of locally verified routes. It does not ship
provider credentials, endpoints, adapters, private MCP implementations, or
automatic configuration repair.

## Install and pin an entry

Clone this repository to a stable local path. Keep code and operator state
separate:

```bash
git clone https://github.com/Bing-Bryan/codex-external-subagent-runtime.git
cd codex-external-subagent-runtime
python3 scripts/pinned_entry.py --ready
```

Configure one Codex Desktop pinned entry with a fixed project ID and canonical
working directory. Use the template in
[`templates/pinned-entry.developer-instructions.md`](templates/pinned-entry.developer-instructions.md).
The entry must call `scripts/pinned_entry.py` with those fixed values; the
operator must not provide them in the task message.

The entry protocol is intentionally literal:

* initialization prints exactly `ENTRY_READY`;
* only trimmed, lowercase `new` crosses the gate;
* every other input prints exactly `ONLY_ACCEPTS_NEW`;
* a live launch lock fails closed and is never silently retried.

## Runtime state

Operator-owned state lives outside the repository at
`$CODEX_HOME/codex-external-subagent-runtime/`:

* `projects.json`, based on `examples/projects.example.json`;
* `providers.json`, based on `examples/providers.example.json`;
* `smoke-evidence.json`, written only after a user-approved real smoke test.

Validate registry structure without invoking a provider:

```bash
python3 scripts/validate_registry.py
```

The default mode is `adopt-existing`: the runtime reads enough existing named
agent and MCP metadata to validate shape and compute a fingerprint, but never
reads credential values or repairs, replaces, or switches configuration.

## Launch contract

The App Server sequence is:

```text
initialize
model/list
thread/start(model=gpt-5.6-luna, V1, developerInstructions=allowlist)
thread/settings/update(model=gpt-5.6-sol, effort=ultra)
thread/read(includeTurns=true)
```

There is no Luna prompt, Sol handoff prompt, or `turn/start`. Success requires
V1, zero bootstrap turns, Sol/Ultra settings verification, exact project
binding, and a UUID thread ID. The Multi-Agent V1 boundary is deliberate; see
the next section for the V2 limitation.

After a successful launch, use Desktop controls to set a distinguishable
title, locate the returned thread, verify project ID and canonical cwd, and
navigate only after those checks pass.

## Why this runtime uses Multi-Agent V1

V1 is the compatibility boundary for this implementation because the complete
launch and verification chain has been implemented and tested only for V1:

* Global preflight requires `[features] multi_agent = true` and
  `multi_agent_v2 = false`. When V2 is enabled, the runtime stops before the
  App Server with `global_v1_required`; it does not toggle the global flag or
  silently fall back.
* The App Server request pins the V1 feature set, starts the task as
  `gpt-5.6-luna`, switches that same task to `gpt-5.6-sol` with `ultra`, and
  verifies the resulting settings. There is no V2-specific start, update, or
  read adapter in this repository.
* Zero bootstrap turns, exact project binding, route-allowlist injection, and
  no-fallback behavior are all checked against this V1 sequence. V1 evidence
  cannot be reused as evidence for a V2 lifecycle or route contract.
* Supporting V2 would require a separate implementation and a separate
  Desktop acceptance track. Until that work exists, a V2-enabled setup stops
  safely instead of creating a task with unverified semantics.

## Route contract

Read [`docs/provider-routing.md`](docs/provider-routing.md) before enabling a
route. A route is exposed only when all of these are true:

1. the registry entry is enabled;
2. local evidence records a passed delivery of the intended kind; and
3. the current configuration fingerprint matches the recorded fingerprint.

The supported classes are `responses-direct`,
`responses-adapter-dedicated`, and bounded read-only `mcp-tool`. A tool is not
a child model, even when it wraps a CLI or SDK. There is no provider priority,
repair, shared-proxy switching, or silent fallback.

Every real external smoke call requires separate user confirmation. The
recorder only records an already observed result and never performs the call:

```bash
python3 scripts/record_smoke_evidence.py \
  --provider-id PROVIDER_ID \
  --confirm-observed-delivery
```

## Configuration helper

`scripts/runtime_config.py plan` is read-only. `apply` is a separate,
explicitly guarded whole-file operation requiring a fresh plan SHA and
`--allow-global-config-write`. It is never called by launch or smoke
recording, and tests use a temporary `CODEX_HOME`.

## Documentation

* [`docs/architecture.en.txt`](docs/architecture.en.txt) — English terminal flow.
* [`docs/architecture.zh-CN.txt`](docs/architecture.zh-CN.txt) — Chinese terminal flow.
* [`docs/provider-routing.md`](docs/provider-routing.md) — route and evidence boundary.
* [`MIGRATION.md`](MIGRATION.md) — extraction from the former monorepo layout.

## Tests

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q scripts tests
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v
```

The CI matrix covers Python 3.9 and 3.11. Static tests and a fake App Server
do not prove real Desktop project binding or provider delivery; those remain
separate acceptance gates.

## License

[MIT](LICENSE)
