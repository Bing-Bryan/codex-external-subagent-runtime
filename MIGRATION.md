# Migration record

This repository was extracted from
[`Bing-Bryan/skills-by-bing`](https://github.com/Bing-Bryan/skills-by-bing),
source `main` commit
`20a12ea2c2348f2ae460af796533bcf1355a674d`.

The former directory was distributed through a Skill-shaped wrapper. The
standalone repository removes Skill discovery metadata and keeps only the
Codex Desktop runtime, launcher, route contract, examples, tests, and CI.

## Local state cutover

The old state directory is not moved or deleted automatically. Use the runtime
migration helper in dry-run mode first, then apply only after reviewing the
file names, sizes, and hashes:

```bash
python3 scripts/migrate_legacy_state.py
python3 scripts/migrate_legacy_state.py --apply \
  --confirm-source codex-external-subagent-bridge
```

Only `projects.json`, `providers.json`, and `smoke-evidence.json` are eligible.
The old directory remains as a rollback copy. Global Codex configuration,
Agent TOML, Keychain data, credentials, and environment values are never part
of this migration.

## Source cleanup

After the standalone repository, CI, local state cutover, and Desktop
acceptance are complete, the old directory, its monorepo tests, workflow, and
Skill index entries can be removed in a separate pull request. The source
repository history and previous pull requests remain intact.
