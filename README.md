# Codex External Subagent Runtime

[简体中文](README.zh-CN.md)

> [!IMPORTANT]
> **Architecture changed in 1.0.0.** This repository now provides the
> always-on project routing Runtime. It no longer ships the pinned `new`
> launcher, creates a special Luna task, switches that task to Sol, or calls
> the App Server. Existing launcher users should read
> [MIGRATION.md](MIGRATION.md) before updating.

An always-on orchestration policy and deterministic route gate for using custom
native Agents, specialist Skills, and MCP tools inside ordinary Codex project
tasks.

This is **not an Agent Skill**. Users do not invoke it with `$...`, a keyword,
or a pinned launcher. Codex loads a managed policy block from the global
`AGENTS.md`; the main Agent then decides whether to handle each prompt directly
or split it into bounded delegated work.

```text
user prompt
    │
    ▼
main Agent analyzes and decomposes
    ├── direct work ───────────────────────────────┐
    └── delegated work                            │
          │                                       │
          ▼                                       │
      registry + smoke + fingerprint gate         │
          ├── native-agent → real subtask card    │
          └── skill-tool  → main-task tool call   │
                         │                         │
                         ▼                         │
                main Agent verifies and summarizes
```

## What it changes

The installer changes one bounded surface:

- a marked block in `~/.codex/AGENTS.md`.

It does not edit `config.toml`, named-Agent TOML, Provider configuration, MCP
configuration, Keychain, model defaults, or authentication. Before changing
`AGENTS.md`, it takes a lock, creates a backup, and writes atomically.

Runtime state is user-owned and separate:

```text
~/.codex/project-agent-runtime/
├── routes.json
└── smoke-evidence.json
```

Route selection only reads the registry and evidence. A `runtime-job` route
atomically creates a mode-`0600` task packet under the current user's canonical
system temporary directory, in `codex-project-agent-runtime-jobs-UID/`, then
deletes it after the child is closed. The jobs directory is mode `0700`;
expired crash leftovers are reclaimed by the next prepare operation. Keeping
ephemeral jobs out of `~/.codex` allows a normal project sandbox to use the
runtime without a global-write approval. The runtime never installs a Provider,
repairs configuration, creates evidence, changes priority, or silently falls
back.

## Install

Requirements: Python 3.9+, macOS or another POSIX host (the Runtime uses
`fcntl` and POSIX file permissions), and Codex with the desired native Agents,
Skills, and MCP tools already configured. Windows is not currently supported.

```bash
git clone https://github.com/Bing-Bryan/codex-external-subagent-runtime.git \
  ~/.codex/tools/codex-project-agent-runtime

cd ~/.codex/tools/codex-project-agent-runtime
python3 scripts/runtime_admin.py plan
python3 scripts/runtime_admin.py install --allow-agents-write
```

`plan` is read-only. `install` requires the explicit write flag, backs up the
current global `AGENTS.md`, and installs or updates one managed block.

For a first-time setup only, copy the disabled examples and edit them locally:

```bash
mkdir -p ~/.codex/project-agent-runtime
cp examples/routes.example.json ~/.codex/project-agent-runtime/routes.json
cp examples/smoke-evidence.example.json \
  ~/.codex/project-agent-runtime/smoke-evidence.json
```

Do not overwrite existing state with the examples.

## Host Multi-Agent version

This runtime does not switch or require a global Multi-Agent version. It runs
inside an ordinary project task and uses the native Agent interface exposed by
the current Codex host:

- A V1 host exposes `fork_context=false`; `message` is the standard transport
  because the bounded packet is delivered directly to the fresh child.
- A V2 host exposes `fork_turns="none"`; `runtime-job` is the compatibility
  path when an external Provider cannot read the host's encrypted delegation
  payload or when the task name is hidden.
- Skill and MCP routes are outside the native V1/V2 child lifecycle. They
  remain main-task tool calls and do not produce fake Agent cards.

The repository contains V2 compatibility mechanics, but that is not evidence
that every external Provider works on V2. Each route still requires fresh,
local smoke evidence for its exact transport and configuration. The Runtime
never edits `multi_agent` or `multi_agent_v2` in `config.toml`.

## Route model

Two route kinds are supported:

- `native-agent`: invokes a real Codex `agent_type`; the UI shows a subtask
  card. The execution hint is the host's native `spawn_agent` capability.
- `skill-tool`: invokes `$skill-name` or an exact `mcp__server__tool`; the UI
  shows a tool call in the main task.

Users may define any target that conforms to this contract. DeepSeek, Kimi,
Luna, and Grok are examples, not a fixed Provider list.

Each route declares:

```text
id
kind
target
capabilities
enabled
configFingerprint
smokeTtlSeconds
writeMode
transport
```

`smokeTtlSeconds` must be between 60 and 31,536,000 seconds. A task packet's
`timeoutSeconds` must be between 1 and 3,600 seconds, and its uppercase
`acceptanceMarker` must be 3–128 characters. The serialized task packet may not
exceed 65,536 bytes.

A route is eligible only when it is enabled and has fresh, passed local smoke
evidence with the exact current configuration fingerprint. Author claims and
example files never substitute for user-local evidence.

## Deterministic checks

Validate state:

```bash
python3 scripts/router_registry.py validate
```

Resolve exactly one route:

```bash
python3 scripts/router_registry.py resolve --capability code
python3 scripts/router_registry.py resolve \
  --capability public-x \
  --route-id grok-public-x
```

Fingerprint non-secret behavior files:

```bash
python3 scripts/router_registry.py fingerprint \
  /absolute/path/to/agent.toml \
  /absolute/path/to/specialist-policy.md
```

Never fingerprint `.env`, Keychain exports, credential stores, tokens, or
secret-bearing files.

Every delegation uses only this task packet:

```text
objective
canonicalCwd
inputs
allowedFiles
writePermission
expectedOutput
acceptanceMarker
timeoutSeconds
```

Any absolute file path in `inputs` must be covered by one of the explicit
`allowedFiles` roots. An external reference file is allowed only when it is
listed explicitly; `allowedFiles` is not implicitly restricted to the project
directory.

The CLI accepts an explicit JSON file, stdin, or lowercase UTF-8 hex. The
Desktop runtime encodes the in-memory packet as hex inside `functions.exec`.
That value is shell-safe and avoids both an unpaired stdin command and a
temporary input-file approval. The policy does not depend on `TextEncoder`,
which is absent in some Desktop JavaScript isolates; it first JSON-escapes
non-ASCII UTF-16 code units and then hex-encodes the resulting ASCII JSON:

```bash
python3 scripts/router_registry.py validate-packet \
  --packet-hex PACKET_HEX \
  --route-write-mode read-only
```

Normalized absolute paths may contain long random temporary-directory names.
They are not rejected merely for looking high-entropy, while explicit token,
key, credential-assignment, and URL-userinfo signatures remain blocked.

For a `runtime-job` native route, the main Agent uses a unique task name and
the same validated packet:

```bash
python3 scripts/router_registry.py prepare-job \
  --task-name deepseek_worker_0123456789abcdef \
  --route-id deepseek-worker \
  --capability code \
  --packet-hex PACKET_HEX

python3 scripts/router_registry.py read-job \
  --task-name deepseek_worker_0123456789abcdef

python3 scripts/router_registry.py claim-job

python3 scripts/router_registry.py cleanup-job \
  --task-name deepseek_worker_0123456789abcdef
```

The main Agent prepares and cleans up. When V2 exposes the task name, the child
uses `read-job`; when even that header is unavailable, the child uses
`claim-job`. The claim succeeds only when exactly one valid runtime job exists.
Both commands atomically bind the first claim to a SHA-256 digest of the
caller's `CODEX_THREAD_ID`; another thread receives `job_already_claimed`.
The raw thread ID is neither stored nor returned. Cleanup is restricted to the
parent thread that created the job.
Every runtime-job requires a host-provided `CODEX_THREAD_ID` in both parent and
child tool processes. If it is absent or malformed, the Runtime fails closed
with `job_identity_unavailable`. Treat the route as unverified until a real
host smoke proves that identity, claim, and cleanup all work; do not silently
replace it with `message`.
Task names must end in 12–32 lowercase hexadecimal characters and should
use cryptographically random values to avoid cross-task collisions. The
Desktop policy obtains the suffix with
`python3 -c 'import secrets; print(secrets.token_hex(8))'` and validates the
16-character lowercase-hex result before use.
Hex is transport encoding, not encryption; packets must still exclude secrets.
Only one uncleaned `runtime-job` may exist globally. No other native Agent may
run between job preparation and terminal verification plus cleanup. Skill/MCP
routes do not read runtime jobs, but should not start during the claim window.
The short-lived runtime job lives outside the project and remains only until
child acceptance and cleanup. It is control-plane metadata, not a project
modification. A read-only delegation may therefore use it when the user says
not to modify project files; an explicit prohibition on all local control-plane
writes still blocks that route.

## Runtime behavior

- The runtime policy is always available, but delegation is not mandatory.
- The main Agent owns decomposition, selection, permissions, waiting,
  acceptance, and the final answer.
- Native Agents receive no full conversation fork and remain leaf nodes.
- Native Agents never inherit the main task history. The Runtime uses the
  no-history option actually exposed by the host: `fork_context=false` on V1
  or `fork_turns="none"` on V2. `message` sends the complete validated packet
  directly. `runtime-job` writes a short-lived local packet keyed by a unique
  task name, which the child reads before doing work; the parent cleans it only
  after verifying the child's terminal status.
- If V2 omits both readable payload and task name, the child calls `claim-job`.
  Zero or multiple pending jobs fail closed; the runtime never guesses which
  packet belongs to a child.
- `read-job` and `claim-job` atomically record the first claiming thread by a
  one-way `CODEX_THREAD_ID` hash. A different thread cannot replay the claim,
  and only the creating parent thread can clean the job.
- Spawn success is not completion; actual output and the acceptance marker must
  pass.
- Every independent delegation creates a fresh leaf Agent. Every
  `runtime-job` additionally uses a unique task name. External `runtime-job`
  routes must not reuse completed Agents through
  `followup_task` or `send_message`: V2 encrypts those messages, so an external
  Provider may miss the new task and replay a stale result.
- On hosts that expose `close_agent`, the main Agent closes the leaf after
  success or failure. If a host exposes only `interrupt_agent`, that operation
  only interrupts a turn and leaves the Agent available, so it must not be
  described as a close. The main Agent uses the host's list capability to
  verify terminal state, queues no message to completed Agents, and lets the
  host reclaim ordinary terminal residents.
- If a fresh spawn reaches the thread limit, lifecycle state is uncertain, or
  terminal status cannot be verified, the route fails as
  `agent_lifecycle_unavailable` without retry or fallback.
- A timed-out non-terminal child keeps its job until TTL expiry. The parent must
  not clean it early and prepare another job that a delayed child could claim.
  Atomic writes prevent half-written final jobs; the next locked gate removes
  malformed or orphaned private queue artifacts without executing them.
- Default concurrency is 3 and the hard ceiling is 5; only one writer may
  operate in a workspace.
- Any route failure stops that route. There is no automatic retry or fallback.

## Security boundary

`router_registry.py` deterministically validates route state, evidence,
fingerprints, and task packets. The global `AGENTS.md` block makes the policy
always available to the main Agent, but it is not a host-level prompt
interceptor or security sandbox. If a host ignores `AGENTS.md`, the runtime
cannot force it to call the gate.

Secret-pattern detection is defense in depth, not a complete credential
scanner. The policy still forbids passing secrets, tokens, environment values,
or credential files even if an unknown format is not recognized heuristically.

Current Multi-Agent V2 can represent a cross-Provider delegation message as
`encrypted_content`. An external Provider may receive an empty readable
payload. `runtime-job` is the compatibility transport for that case: it keeps
the real native-Agent card while moving only the validated task packet through
a local, short-lived file. It is local plaintext, not end-to-end encryption.
The external child does not inherit the main conversation; the main Agent must
put all necessary, non-sensitive context into the eight-field packet.

Specialist routes keep their own security contracts. For example, a frontend
Skill may write only inside an approved workspace, while a public-X MCP route
may be read-only.

## Update, status, and uninstall

```bash
git pull --ff-only
python3 scripts/runtime_admin.py plan
python3 scripts/runtime_admin.py install --allow-agents-write
python3 scripts/runtime_admin.py status
```

Remove only the managed policy block:

```bash
python3 scripts/runtime_admin.py uninstall --allow-agents-write
```

Uninstall leaves route state, Agent configuration, Provider configuration, and
credentials untouched.

Users coming from the removed pinned launcher should follow
[MIGRATION.md](MIGRATION.md). The last launcher-only revision remains available
at commit `2b39a7f` for rollback.

## Verification

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s tests -p 'test_*.py' -v
PYTHONDONTWRITEBYTECODE=1 python3 -m compileall -q scripts tests
git diff --check
```

## License

[MIT](LICENSE)
