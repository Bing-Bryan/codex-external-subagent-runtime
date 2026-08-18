# Provider route contract

The Runtime does not maintain a provider catalog. Each operator may define any
provider that satisfies one of the supported transport contracts and passes a
real local smoke test.

## Transport classes

| Transport | Runtime reference | Meaning |
| --- | --- | --- |
| `responses-direct` | Existing named `agentType` | The configured endpoint is proven to support the installed Codex Responses protocol and a V1 model-child task. |
| `responses-adapter-dedicated` | Existing named `agentType` | One immutable provider-specific adapter translates an upstream protocol to Responses. |
| `mcp-tool` | Existing `mcpServer` plus `toolName` | One bounded read-only capability. It remains a tool even when its backend uses a CLI or SDK. |

A product label such as “Code,” “Coding Plan,” or “OpenAI compatible” does not
prove a transport. A successful process spawn also does not prove task
delivery.

## Adopt existing configuration

The public registry references existing named agents and MCP servers; it does
not duplicate endpoints, models, auth commands, environment-variable names, or
credentials. At launch, the Runtime reads only enough Codex-owned configuration
to validate the route and compute its fingerprint.

The sanitized task allowlist contains only:

- provider ID and transport;
- named `agentType` for a model child; or
- MCP server, tool name, and `readOnly: true` for a tool.

Labels, notes, URLs, keys, environment values, and full config are never
injected into `developerInstructions`.

## Enablement gate

A route is usable only when:

1. Its registry entry has `enabled: true`.
2. Local evidence records `status: passed` for the correct delivery kind.
3. The current relevant config fingerprint exactly matches the smoke-test
   fingerprint.

Config drift disables the route. The Runtime does not repair the file, rerun a
smoke test, choose a different provider, or fall back to another route.

No registry ordering means priority. Sol chooses among suitable allowlisted
routes for the task; if none is suitable, it stops.

## Smoke-test acceptance

Every real external call requires separate user confirmation. A model-child
smoke test must prove:

1. the intended named agent actually started under Multi-Agent V1;
2. persisted or returned evidence identifies the intended provider and model;
3. a task-specific sentinel was delivered by the child;
4. timeout, auth failure, and malformed output stop without another route;
5. logs and errors contain no prompt, key, environment value, or raw event
   stream.

An MCP-tool smoke test must additionally prove that the bounded tool, not an
unrestricted shell fallback, produced the result. For public X context, require
verifiable raw `x.com/<user>/status/<id>` links when the tool contract calls for
them.

After observing a passed result, record its current fingerprint with
`record_smoke_evidence.py`. That script records evidence only; it performs no
external call.

## Shared proxy boundary

A shared mutable CC Switch port has one current upstream. It cannot provide
deterministic concurrent multi-provider routing. Use one of:

1. a provider's verified Responses endpoint;
2. one immutable dedicated adapter and port per provider; or
3. a bounded MCP tool.

A Kimi child can be valid while CC Switch is fixed to Kimi, but that evidence
does not prove concurrent switching or another installation's route.

## Author evidence

These are provenance notes, not built-in providers and not enablement evidence:

| Provider | Author-observed route | Boundary |
| --- | --- | --- |
| DeepSeek | V1 model child | Completed in the author's setup; each user must reverify their own endpoint, model, identity, and delivery. |
| Kimi | V1 model child | Completed only while the author's CC Switch route remained fixed to Kimi. |
| Grok | Read-only CLI/MCP tool | Completed as a bounded tool, not as a model child. Private wrapper and credentials are not shipped. |

The Runtime makes no claim about providers not listed above. They are neither
forbidden nor supported by author evidence: the user's local contract and
smoke result decide.

## Configuration writes

`runtime_config.py plan` is read-only and reports a redacted structural
difference. `apply` is a separate, explicit whole-file operation requiring a
fresh plan SHA and `--allow-global-config-write`. Launch and smoke recording
never import or invoke apply.
