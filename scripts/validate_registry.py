#!/usr/bin/env python3
"""Validate runtime registries without exposing provider configuration values."""

from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import sys
from typing import Any
from uuid import UUID

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_contract import (
    RuntimeContractError,
    build_runtime_allowlist,
    load_provider_registry,
    load_smoke_evidence,
)


class RegistryError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise RegistryError("invalid_arguments")


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".codex").resolve()


def load_json_object(path: Path, code: str) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise RegistryError(code)
        value = json.loads(path.read_text(encoding="utf-8"))
    except RegistryError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryError(code) from exc
    if not isinstance(value, dict):
        raise RegistryError(code)
    return value


def require_text(value: Any, code: str, maximum: int = 240) -> str:
    if not isinstance(value, str):
        raise RegistryError(code)
    text = value.strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise RegistryError(code)
    return text


def validate_projects(path: Path, require_existing_cwds: bool = True) -> dict[str, int]:
    data = load_json_object(path, "projects_registry_unreadable")
    if set(data) != {"version", "projects"} or data.get("version") != 1:
        raise RegistryError("projects_registry_schema_invalid")
    projects = data.get("projects")
    if not isinstance(projects, list) or not projects:
        raise RegistryError("projects_registry_schema_invalid")
    seen_ids: set[str] = set()
    seen_cwds: set[str] = set()
    seen_labels: set[str] = set()
    for item in projects:
        if not isinstance(item, dict) or set(item) != {"projectId", "label", "cwd"}:
            raise RegistryError("projects_registry_schema_invalid")
        project_id = require_text(item.get("projectId"), "project_id_invalid", 64)
        try:
            parsed_id = UUID(project_id)
        except ValueError as exc:
            raise RegistryError("project_id_invalid") from exc
        if str(parsed_id) != project_id:
            raise RegistryError("project_id_invalid")
        label = require_text(item.get("label"), "project_label_invalid", 80)
        cwd_text = require_text(item.get("cwd"), "project_cwd_invalid", 4096)
        cwd = Path(cwd_text).expanduser()
        if not cwd.is_absolute() or cwd == Path(cwd.anchor):
            raise RegistryError("project_cwd_invalid")
        try:
            canonical = cwd.resolve(strict=require_existing_cwds)
        except OSError as exc:
            raise RegistryError("project_cwd_invalid") from exc
        if require_existing_cwds and not canonical.is_dir():
            raise RegistryError("project_cwd_invalid")
        canonical_text = str(canonical)
        if project_id in seen_ids or canonical_text in seen_cwds or label in seen_labels:
            raise RegistryError("projects_registry_duplicate")
        seen_ids.add(project_id)
        seen_cwds.add(canonical_text)
        seen_labels.add(label)
    return {"count": len(projects)}


def validate_providers(path: Path) -> dict[str, Any]:
    try:
        providers = load_provider_registry(path)
    except RuntimeContractError as exc:
        raise RegistryError(exc.code) from exc
    counts = Counter(item["transport"] for item in providers)
    return {
        "version": 2,
        "count": len(providers),
        "enabled": sum(item["enabled"] is True for item in providers),
        "transports": dict(sorted(counts.items())),
    }


def parse_args() -> argparse.Namespace:
    home = codex_home()
    runtime = home / "codex-external-subagent-runtime"
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--projects", type=Path, default=runtime / "projects.json")
    parser.add_argument("--providers", type=Path, default=runtime / "providers.json")
    parser.add_argument("--smoke-evidence", type=Path, default=runtime / "smoke-evidence.json")
    parser.add_argument("--allow-missing-cwds", action="store_true")
    parser.add_argument("--evaluate-runtime", action="store_true")
    return parser.parse_args()


def run() -> dict[str, Any]:
    args = parse_args()
    projects = validate_projects(args.projects, not args.allow_missing_cwds)
    providers = validate_providers(args.providers)
    evidence: dict[str, Any] | None = None
    if args.smoke_evidence.exists():
        try:
            loaded = load_smoke_evidence(args.smoke_evidence)
        except RuntimeContractError as exc:
            raise RegistryError(exc.code) from exc
        evidence = {"version": 1, "count": len(loaded)}
    runtime: dict[str, Any] | None = None
    if args.evaluate_runtime:
        try:
            evaluated = build_runtime_allowlist(
                codex_home(), args.providers, args.smoke_evidence
            )
        except RuntimeContractError as exc:
            raise RegistryError(exc.code) from exc
        runtime = {
            "allowed": len(evaluated["allowed"]),
            "rejected": evaluated["rejected"],
        }
    return {
        "ok": True,
        "projects": projects,
        "providers": providers,
        "smokeEvidence": evidence,
        "runtime": runtime,
    }


def main() -> int:
    try:
        payload = run()
    except RegistryError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, separators=(",", ":")), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('{"ok":false,"error":"interrupted"}', file=sys.stderr)
        return 130
    except Exception:
        print('{"ok":false,"error":"internal_error"}', file=sys.stderr)
        return 1
    print(json.dumps(payload, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
