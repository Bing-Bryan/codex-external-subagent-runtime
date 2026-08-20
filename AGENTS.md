# Codex Project Agent Runtime

- Python 3.9+ standard library only; do not add dependencies.
- Run `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s tests -p 'test_*.py' -v` after behavior changes.
- Keep `templates/agents-runtime-block.md` concise because it is injected into every Codex task.
- Never add Provider URLs, credentials, tokens, environment values, or user smoke evidence to the repository.
- Runtime installation may modify only its marked block in the selected `AGENTS.md`; preserve all surrounding content.
- Normal routing reads state and fails closed. It must not repair configuration, retry, or fall back automatically.
