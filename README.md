# Codex External Subagent Runtime

[简体中文](README.zh-CN.md)

Codex Desktop bootstrap, launcher, and runtime route contract for gated
external subagents. This is a standalone runtime repository, not an Agent
Skill and not a general-purpose external-model router.

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
binding, and a UUID thread ID. Multi-Agent V2 is outside this runtime contract;
the runtime fails closed with `global_v1_required` when V2 is enabled.

After a successful launch, use Desktop controls to set a distinguishable
title, locate the returned thread, verify project ID and canonical cwd, and
navigate only after those checks pass.

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
