#!/usr/bin/env python3
"""Safely copy legacy Runtime state into the standalone Runtime directory."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
from typing import Any


RUNTIME_NAME = "codex-external-subagent-runtime"
LEGACY_NAME = "codex-external-subagent-bridge"
STATE_FILES = ("projects.json", "providers.json", "smoke-evidence.json")
MAX_FILE_BYTES = 1_000_000


class MigrationError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def codex_home(raw: str | None = None) -> Path:
    value = raw if raw is not None else os.environ.get("CODEX_HOME")
    return Path(value).expanduser().resolve() if value else (Path.home() / ".codex").resolve()


def sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def read_state_file(path: Path) -> tuple[bytes, dict[str, Any]]:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise MigrationError("state_file_invalid")
        raw = path.read_bytes()
    except MigrationError:
        raise
    except OSError as exc:
        raise MigrationError("state_file_unreadable") from exc
    if len(raw) > MAX_FILE_BYTES:
        raise MigrationError("state_file_too_large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise MigrationError("state_file_invalid_json") from exc
    if not isinstance(value, dict):
        raise MigrationError("state_file_invalid_json")
    return raw, value


def active_lock(home: Path) -> bool:
    candidates = (
        home / LEGACY_NAME / "launch.lock",
        home / RUNTIME_NAME / "launch.lock",
        home / f".{LEGACY_NAME}-config.lock",
        home / f".{RUNTIME_NAME}-config.lock",
    )
    return any(path.exists() or path.is_symlink() for path in candidates)


class MigrationLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None

    def __enter__(self) -> "MigrationLock":
        try:
            self.fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.write(self.fd, b"state migration lock\n")
            os.fsync(self.fd)
        except FileExistsError as exc:
            raise MigrationError("migration_already_running") from exc
        except OSError as exc:
            raise MigrationError("migration_lock_failed") from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.fd is not None:
            os.close(self.fd)
        try:
            self.path.unlink()
        except OSError:
            pass


def collect(home: Path) -> tuple[Path, Path, list[dict[str, Any]]]:
    source = home / LEGACY_NAME
    destination = home / RUNTIME_NAME
    if source.is_symlink() or not source.resolve(strict=True).is_dir():
        raise MigrationError("legacy_state_missing")
    if destination.exists() or destination.is_symlink():
        raise MigrationError("destination_already_exists")
    if active_lock(home):
        raise MigrationError("active_runtime_lock")

    files: list[dict[str, Any]] = []
    for name in STATE_FILES:
        raw, _ = read_state_file(source / name)
        files.append({"name": name, "bytes": len(raw), "sha256": sha256(raw), "raw": raw})
    return source, destination, files


def public_result(mode: str, source: Path, destination: Path, files: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "ok": True,
        "mode": mode,
        "source": str(source),
        "destination": str(destination),
        "files": [
            {"name": item["name"], "bytes": item["bytes"], "sha256": item["sha256"]}
            for item in files
        ],
    }


def apply_copy(home: Path, source: Path, destination: Path, files: list[dict[str, Any]]) -> None:
    staging: Path | None = None
    try:
        staging = Path(tempfile.mkdtemp(prefix=f".{RUNTIME_NAME}.", dir=str(home)))
        os.chmod(staging, 0o700)
        for item in files:
            target = staging / item["name"]
            fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                os.write(fd, item["raw"])
                os.fsync(fd)
            finally:
                os.close(fd)
            os.chmod(target, 0o600)
            copied = target.read_bytes()
            if sha256(copied) != item["sha256"]:
                raise MigrationError("state_hash_mismatch")
        if destination.exists() or destination.is_symlink():
            raise MigrationError("destination_already_exists")
        os.replace(staging, destination)
        staging = None
        os.chmod(destination, 0o700)
    except MigrationError:
        raise
    except OSError as exc:
        raise MigrationError("state_copy_failed") from exc
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-source")
    parser.add_argument("--home", type=Path, help="test-only or explicitly selected CODEX_HOME")
    return parser.parse_args()


def run(args: argparse.Namespace) -> dict[str, Any]:
    home = codex_home(str(args.home) if args.home else None)
    source, destination, files = collect(home)
    if not args.apply:
        return public_result("dry-run", source, destination, files)
    if args.confirm_source != LEGACY_NAME:
        raise MigrationError("source_confirmation_required")
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    with MigrationLock(home / f".{RUNTIME_NAME}-migration.lock"):
        source, destination, files = collect(home)
        apply_copy(home, source, destination, files)
    return public_result("apply", source, destination, files)


def main() -> int:
    try:
        result = run(parse_args())
    except MigrationError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, separators=(",", ":")), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
