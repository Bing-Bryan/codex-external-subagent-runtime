#!/usr/bin/env python3
"""Plan or explicitly apply whole-file Codex configuration changes."""

from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile
from typing import Any


MAX_FILE_BYTES = 1_000_000


class ConfigError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ConfigError("invalid_arguments")


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".codex").resolve()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def parse_toml(raw: bytes) -> dict[str, Any]:
    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = None
    try:
        text = raw.decode("utf-8")
        if tomllib is not None:
            value = tomllib.loads(text)
        else:
            value = parse_restricted_toml(text)
    except (UnicodeError, ValueError) as exc:
        raise ConfigError("intent_toml_invalid") from exc
    if not isinstance(value, dict):
        raise ConfigError("intent_toml_invalid")
    return value


def parse_restricted_toml(text: str) -> dict[str, Any]:
    """Parse the simple scalar/table subset used by compatibility tests."""
    root: dict[str, Any] = {}
    current = root
    assignment = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s*(.+?)\s*$")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            table = line[1:-1].strip()
            if not table or any(not part for part in table.split(".")):
                raise ConfigError("intent_toml_invalid")
            current = root
            for part in table.split("."):
                child = current.setdefault(part, {})
                if not isinstance(child, dict):
                    raise ConfigError("intent_toml_invalid")
                current = child
            continue
        match = assignment.fullmatch(line)
        if match is None or match.group(1) in current:
            raise ConfigError("intent_toml_invalid")
        raw_value = match.group(2)
        if raw_value == "true":
            parsed: Any = True
        elif raw_value == "false":
            parsed = False
        else:
            try:
                parsed = ast.literal_eval(raw_value)
            except (SyntaxError, ValueError) as exc:
                raise ConfigError("intent_toml_invalid") from exc
        if not isinstance(parsed, (str, int, float, bool, list)):
            raise ConfigError("intent_toml_invalid")
        current[match.group(1)] = parsed
    return root


def load_intent(path: Path) -> list[dict[str, Any]]:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise ConfigError("intent_unreadable")
        raw = path.read_bytes()
        if len(raw) > MAX_FILE_BYTES * 5:
            raise ConfigError("intent_unreadable")
        data = json.loads(raw.decode("utf-8"))
    except ConfigError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError("intent_unreadable") from exc
    if not isinstance(data, dict) or set(data) != {"version", "files"} or data.get("version") != 1:
        raise ConfigError("intent_schema_invalid")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise ConfigError("intent_schema_invalid")

    seen: set[str] = set()
    result: list[dict[str, Any]] = []
    for item in files:
        if not isinstance(item, dict) or set(item) != {"path", "content"}:
            raise ConfigError("intent_schema_invalid")
        relative = item.get("path")
        content = item.get("content")
        if not isinstance(relative, str) or not isinstance(content, str):
            raise ConfigError("intent_schema_invalid")
        if relative != "config.toml":
            path_obj = Path(relative)
            valid_agent = (
                len(path_obj.parts) == 2
                and path_obj.parts[0] == "agents"
                and path_obj.suffix == ".toml"
                and path_obj.name not in {".toml", "..toml"}
                and all(char.isalnum() or char in "._-" for char in path_obj.stem)
            )
            if not valid_agent:
                raise ConfigError("intent_target_rejected")
        if relative in seen:
            raise ConfigError("intent_schema_invalid")
        seen.add(relative)
        encoded = content.encode("utf-8")
        if not encoded or len(encoded) > MAX_FILE_BYTES:
            raise ConfigError("intent_schema_invalid")
        parsed = parse_toml(encoded)
        if relative.startswith("agents/") and not isinstance(parsed.get("name"), str):
            raise ConfigError("intent_toml_invalid")
        result.append({"path": relative, "content": content, "parsed": parsed})
    return sorted(result, key=lambda value: value["path"])


def flatten_keys(value: Any, prefix: str = "") -> dict[str, Any]:
    if not isinstance(value, dict):
        return {prefix: type(value).__name__}
    result: dict[str, Any] = {}
    for key in sorted(value):
        path = f"{prefix}.{key}" if prefix else str(key)
        child = value[key]
        if isinstance(child, dict):
            result.update(flatten_keys(child, path))
        else:
            result[path] = child
    return result


def changed_keys(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    old = flatten_keys(before)
    new = flatten_keys(after)
    return sorted(key for key in set(old) | set(new) if old.get(key) != new.get(key))


def validate_target_path(home: Path, relative: str) -> Path:
    target = home / relative
    try:
        if relative.startswith("agents/"):
            agents_dir = home / "agents"
            if agents_dir.is_symlink():
                raise ConfigError("config_target_invalid")
            if agents_dir.exists() and agents_dir.resolve(strict=True) != agents_dir.absolute():
                raise ConfigError("config_target_invalid")
        if target.is_symlink():
            raise ConfigError("config_target_invalid")
        if target.exists():
            target.resolve(strict=True).relative_to(home.resolve(strict=True))
    except ConfigError:
        raise
    except (OSError, ValueError) as exc:
        raise ConfigError("config_target_invalid") from exc
    return target


def current_file(home: Path, relative: str) -> tuple[bytes | None, dict[str, Any]]:
    target = validate_target_path(home, relative)
    try:
        if not target.exists():
            return None, {}
        if not target.resolve(strict=True).is_file():
            raise ConfigError("config_target_invalid")
        raw = target.read_bytes()
    except ConfigError:
        raise
    except OSError as exc:
        raise ConfigError("config_target_invalid") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise ConfigError("config_target_invalid")
    try:
        parsed = parse_toml(raw)
    except ConfigError:
        parsed = {"__unparsed_existing_file__": sha256(raw)}
    return raw, parsed


def build_plan(home: Path, intent_path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    items = load_intent(intent_path)
    material: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    for item in items:
        before_raw, before_parsed = current_file(home, item["path"])
        desired_raw = item["content"].encode("utf-8")
        before_sha = sha256(before_raw) if before_raw is not None else None
        after_sha = sha256(desired_raw)
        material.append(
            {
                "path": item["path"],
                "beforeSha256": before_sha,
                "afterSha256": after_sha,
                "content": item["content"],
            }
        )
        if before_raw != desired_raw:
            changes.append(
                {
                    "path": item["path"],
                    "operation": "create" if before_raw is None else "replace",
                    "beforeSha256": before_sha,
                    "afterSha256": after_sha,
                    "changedKeys": changed_keys(before_parsed, item["parsed"]),
                }
            )
    digest_payload = {
        "version": 1,
        "home": str(home),
        "files": material,
    }
    plan_sha = sha256(
        json.dumps(
            digest_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )
    public = {
        "ok": True,
        "mode": "plan",
        "writesPerformed": False,
        "planSha": plan_sha,
        "changes": changes,
    }
    return public, material


class ApplyLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None
        self.identity: tuple[int, int] | None = None

    def __enter__(self) -> "ApplyLock":
        try:
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            state = os.fstat(self.fd)
            self.identity = (state.st_dev, state.st_ino)
            os.write(self.fd, b"locked\n")
        except FileExistsError as exc:
            raise ConfigError("config_apply_already_running") from exc
        except OSError as exc:
            self.__exit__(None, None, None)
            raise ConfigError("config_apply_lock_failed") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
        try:
            state = self.path.lstat()
            if (
                self.identity is not None
                and not stat.S_ISLNK(state.st_mode)
                and (state.st_dev, state.st_ino) == self.identity
            ):
                self.path.unlink()
        except OSError:
            pass


def apply_plan(home: Path, intent: Path, expected_sha: str, approved: bool) -> dict[str, Any]:
    if not approved:
        raise ConfigError("global_write_not_approved")
    if len(expected_sha) != 64 or any(char not in "0123456789abcdef" for char in expected_sha):
        raise ConfigError("plan_sha_invalid")
    try:
        home.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        raise ConfigError("codex_home_unavailable") from exc

    with ApplyLock(home / ".codex-external-subagent-runtime-config.lock"):
        public, material = build_plan(home, intent)
        if public["planSha"] != expected_sha:
            raise ConfigError("config_conflict")
        changed = {item["path"] for item in public["changes"]}
        if not changed:
            return {
                "ok": True,
                "mode": "apply",
                "writesPerformed": False,
                "planSha": expected_sha,
                "changedFiles": [],
                "warning": "global Codex configuration was not changed",
            }

        backup_base = home / "backups"
        runtime_backup_base = backup_base / "codex-external-subagent-runtime"
        if backup_base.is_symlink() or runtime_backup_base.is_symlink():
            raise ConfigError("config_backup_target_invalid")
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup_root = runtime_backup_base / timestamp
        staged: list[tuple[Path, Path, int]] = []
        try:
            for item in material:
                if item["path"] not in changed:
                    continue
                target = validate_target_path(home, item["path"])
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                current_raw, _ = current_file(home, item["path"])
                current_sha = sha256(current_raw) if current_raw is not None else None
                if current_sha != item["beforeSha256"]:
                    raise ConfigError("config_conflict")
                if current_raw is not None:
                    backup = backup_root / item["path"]
                    backup.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                fd, raw_temp = tempfile.mkstemp(
                    prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
                )
                temp_path = Path(raw_temp)
                mode = stat.S_IMODE(target.stat().st_mode) if target.exists() else 0o600
                with os.fdopen(fd, "wb") as handle:
                    handle.write(item["content"].encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temp_path, mode)
                staged.append((temp_path, target, mode))
            for temp_path, target, _ in staged:
                os.replace(temp_path, target)
        except ConfigError:
            raise
        except OSError as exc:
            raise ConfigError("config_apply_failed") from exc
        finally:
            for temp_path, _, _ in staged:
                try:
                    temp_path.unlink()
                except OSError:
                    pass
    return {
        "ok": True,
        "mode": "apply",
        "writesPerformed": True,
        "planSha": expected_sha,
        "changedFiles": sorted(changed),
        "backupRoot": str(backup_root),
        "warning": "global Codex configuration was modified with explicit approval",
    }


def parse_args() -> argparse.Namespace:
    parser = JsonArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    plan = subparsers.add_parser("plan")
    plan.add_argument("--intent", type=Path, required=True)
    apply = subparsers.add_parser("apply")
    apply.add_argument("--intent", type=Path, required=True)
    apply.add_argument("--plan-sha", required=True)
    apply.add_argument("--allow-global-config-write", action="store_true")
    return parser.parse_args()


def run() -> dict[str, Any]:
    args = parse_args()
    home = codex_home()
    if args.command == "plan":
        public, _ = build_plan(home, args.intent)
        return public
    return apply_plan(
        home,
        args.intent,
        args.plan_sha,
        args.allow_global_config_write,
    )


def main() -> int:
    try:
        payload = run()
    except ConfigError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, separators=(",", ":")), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('{"ok":false,"error":"interrupted"}', file=sys.stderr)
        return 130
    except Exception:
        print('{"ok":false,"error":"internal_error"}', file=sys.stderr)
        return 1
    print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
