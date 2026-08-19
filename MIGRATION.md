# Migration from the pinned launcher

Version 1.0.0 is a breaking architecture migration. The repository no longer
creates a special Luna task, switches it to Sol, or accepts the `new` entry
command. The replacement runs inside ordinary Codex project tasks and lets the
main Agent route bounded work to native Agents, specialist Skills, and MCP
tools.

There is no automatic state conversion. Existing pinned entries, launcher
state, Provider configuration, MCP configuration, credentials, and old tasks
are not modified or deleted.

## Before updating

1. Keep the old pinned entries until the new Runtime passes acceptance in each
   project.
2. Record the current checkout. The last launcher-only revision is `2b39a7f`.
3. Back up user-owned launcher state if you may need it later:

   ```text
   ~/.codex/codex-external-subagent-runtime/
   ```

Do not copy old `projects.json`, `providers.json`, or `smoke-evidence.json`
over the new state. Their schemas and responsibilities are different.

## Install the project routing Runtime

Clone the repository into the stable Runtime path, or update a clean checkout:

```bash
git clone https://github.com/Bing-Bryan/codex-external-subagent-runtime.git \
  ~/.codex/tools/codex-project-agent-runtime

cd ~/.codex/tools/codex-project-agent-runtime
python3 scripts/runtime_admin.py plan
python3 scripts/runtime_admin.py install --allow-agents-write
python3 scripts/runtime_admin.py status
```

`plan` is read-only. `install` modifies only the marked Runtime block in
`~/.codex/AGENTS.md`, after taking a lock and backup. It does not modify
`config.toml`, Agent TOML, Provider configuration, MCP configuration, Keychain,
model defaults, or authentication.

For a first-time state setup only:

```bash
mkdir -p ~/.codex/project-agent-runtime
cp examples/routes.example.json ~/.codex/project-agent-runtime/routes.json
cp examples/smoke-evidence.example.json \
  ~/.codex/project-agent-runtime/smoke-evidence.json
```

The examples are disabled. Do not overwrite existing new-Runtime state.
Enable a route only after its exact local smoke delivery and configuration
fingerprint have passed.

## V1 and V2

The Runtime does not enable, disable, or switch Multi-Agent versions. A host
configured for V1 remains V1. A host configured for V2 remains V2. Skill and
MCP routes are tool calls rather than native V1/V2 child Agents. External
native-Agent routes on V2 may require the short-lived `runtime-job` transport;
that compatibility code does not replace route-specific real smoke evidence.

## Cutover and rollback

After the Runtime passes in every project, unpin or rename the old entries
manually. Do not delete them until the rollback window is over.

To roll back:

1. From the 1.0.0 checkout, remove only the managed Runtime policy block:

   ```bash
   python3 scripts/runtime_admin.py uninstall --allow-agents-write
   python3 scripts/runtime_admin.py status
   ```

2. If uninstall cannot run, inspect the timestamped backup under
   `~/.codex/backups/codex-project-agent-runtime/`. Restore it only after
   comparing it with the current `~/.codex/AGENTS.md`; a blind copy could
   overwrite unrelated rules added after installation.

3. Inspect the final launcher-only source or create a separate rollback
   checkout at commit `2b39a7f`:

   ```bash
   git show 2b39a7f:README.md
   git worktree add ../codex-external-subagent-runtime-rollback 2b39a7f
   ```

4. Verify the rollback checkout and its existing launcher state, then restore
   the old pinned entries manually. Do not copy new Runtime state into the old
   launcher state directory.

The rollback worktree is for recovery only. Keep the main checkout on `main`
for future updates.
