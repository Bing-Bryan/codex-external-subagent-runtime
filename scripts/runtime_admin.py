#!/usr/bin/env python3
"""Install or remove the always-on project Agent Runtime AGENTS.md block.

All commands emit exactly one JSON object. Mutating commands require the
explicit --allow-agents-write flag, take an exclusive lock, create a backup,
and replace AGENTS.md atomically. Python 3.9+ standard library only.
"""

import argparse
import datetime
import json
import os
import shutil
import stat
import sys
import tempfile
import time
import uuid


BEGIN = "<!-- BEGIN CODEX PROJECT AGENT RUNTIME -->"
END = "<!-- END CODEX PROJECT AGENT RUNTIME -->"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RUNTIME_ROOT = os.path.dirname(SCRIPT_DIR)
DEFAULT_TEMPLATE = os.path.join(
    DEFAULT_RUNTIME_ROOT, "templates", "agents-runtime-block.md"
)
DEFAULT_AGENTS_FILE = os.path.join(os.path.expanduser("~"), ".codex", "AGENTS.md")
DEFAULT_BACKUP_DIR = os.path.join(
    os.path.expanduser("~"), ".codex", "backups", "codex-project-agent-runtime"
)
STALE_LOCK_MIN_AGE_SECONDS = 30


class RuntimeErrorCode(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


class JsonArgumentParser(argparse.ArgumentParser):
    """Convert CLI usage failures into the runtime's stable JSON contract."""

    def error(self, _message):
        raise RuntimeErrorCode("usage_error")


def emit(payload):
    try:
        sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return True
    except BrokenPipeError:
        try:
            sys.stdout.close()
        except BrokenPipeError:
            pass
        return False


def require_regular_file(path, error_code):
    if os.path.islink(path) or not os.path.isfile(path):
        raise RuntimeErrorCode(error_code)


def validate_contract(template_path, runtime_root):
    runtime_root = os.path.abspath(runtime_root)
    if os.path.islink(runtime_root) or not os.path.isdir(runtime_root):
        raise RuntimeErrorCode("runtime_contract_missing")
    require_regular_file(template_path, "runtime_contract_missing")
    helper = os.path.join(runtime_root, "scripts", "router_registry.py")
    require_regular_file(helper, "runtime_contract_missing")
    try:
        with open(template_path, "r", encoding="utf-8") as handle:
            template = handle.read()
    except OSError:
        raise RuntimeErrorCode("runtime_contract_missing")
    if "{{RUNTIME_ROOT}}" not in template:
        raise RuntimeErrorCode("runtime_contract_missing")
    rendered = template.replace("{{RUNTIME_ROOT}}", runtime_root).strip()
    if not rendered:
        raise RuntimeErrorCode("runtime_contract_missing")
    return BEGIN + "\n" + rendered + "\n" + END


def validate_agents_path(path):
    if os.path.islink(path):
        raise RuntimeErrorCode("agents_file_unsafe")
    if os.path.exists(path) and not os.path.isfile(path):
        raise RuntimeErrorCode("agents_file_unsafe")
    parent = os.path.dirname(os.path.abspath(path))
    if not os.path.isdir(parent) or os.path.islink(parent):
        raise RuntimeErrorCode("agents_file_unsafe")


def read_agents(path):
    validate_agents_path(path)
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except (OSError, UnicodeError):
        raise RuntimeErrorCode("agents_file_unsafe")


def locate_block(content):
    begin_count = content.count(BEGIN)
    end_count = content.count(END)
    if begin_count == 0 and end_count == 0:
        return None
    if begin_count != 1 or end_count != 1:
        raise RuntimeErrorCode("managed_block_conflict")
    start = content.find(BEGIN)
    end_start = content.find(END)
    if start < 0 or end_start < start:
        raise RuntimeErrorCode("managed_block_conflict")
    return start, end_start + len(END)


def desired_install(content, block):
    location = locate_block(content)
    if location is None:
        base = content.rstrip("\n")
        if base:
            updated = base + "\n\n" + block + "\n"
        else:
            updated = block + "\n"
        return "install", updated
    start, end = location
    current = content[start:end]
    if current == block:
        return "none", content
    return "update", content[:start] + block + content[end:]


def desired_uninstall(content):
    location = locate_block(content)
    if location is None:
        return "none", content
    start, end = location
    remove_start = start
    if content[:start].endswith("\n\n"):
        remove_start -= 2
    elif content[:start].endswith("\n"):
        remove_start -= 1
    remove_end = end
    if remove_end < len(content) and content[remove_end] == "\n":
        remove_end += 1
    updated = content[:remove_start] + content[remove_end:]
    if updated and not updated.endswith("\n"):
        updated += "\n"
    return "uninstall", updated


def create_backup(path, backup_root):
    if not os.path.exists(path):
        return
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    directory = os.path.join(backup_root, stamp + "-" + uuid.uuid4().hex[:8])
    destination = os.path.join(directory, "AGENTS.md")
    try:
        os.makedirs(directory, mode=0o700)
        source_flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        source_descriptor = os.open(path, source_flags)
        with os.fdopen(source_descriptor, "rb") as source:
            if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
                raise OSError("source is not a regular file")
            destination_descriptor = os.open(
                destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
            with os.fdopen(destination_descriptor, "wb") as target:
                shutil.copyfileobj(source, target)
                target.flush()
                os.fsync(target.fileno())
    except OSError:
        raise RuntimeErrorCode("backup_failed")


def atomic_write(path, content):
    parent = os.path.dirname(os.path.abspath(path))
    mode = 0o644
    if os.path.exists(path):
        mode = stat.S_IMODE(os.stat(path, follow_symlinks=False).st_mode)
    descriptor = None
    temp_path = None
    try:
        descriptor, temp_path = tempfile.mkstemp(prefix=".AGENTS.", dir=parent)
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
    except OSError:
        raise RuntimeErrorCode("agents_write_failed")
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temp_path is not None:
            try:
                os.unlink(temp_path)
            except OSError:
                pass


class ExclusiveLock:
    def __init__(self, path):
        self.path = path
        self.acquired = False

    def _acquire_once(self):
        try:
            descriptor = os.open(
                self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600
            )
        except FileExistsError:
            return False
        except OSError:
            raise RuntimeErrorCode("runtime_lock_failed")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(str(os.getpid()) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError:
            try:
                os.unlink(self.path)
            except OSError:
                pass
            raise RuntimeErrorCode("runtime_lock_failed")
        self.acquired = True
        return True

    def _remove_stale_dead_lock(self):
        try:
            info = os.stat(self.path, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode):
                return False
            if time.time() - info.st_mtime < STALE_LOCK_MIN_AGE_SECONDS:
                return False
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(self.path, flags)
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                raw_pid = handle.read(32).strip()
            pid = int(raw_pid)
            if pid <= 0:
                return False
            try:
                os.kill(pid, 0)
            except ProcessLookupError:
                pass
            except (PermissionError, OSError):
                return False
            else:
                return False
            os.unlink(self.path)
            return True
        except (OSError, UnicodeError, ValueError):
            return False

    def __enter__(self):
        if not self._acquire_once():
            if not self._remove_stale_dead_lock() or not self._acquire_once():
                raise RuntimeErrorCode("runtime_lock_busy")
        return self

    def __exit__(self, _exc_type, _exc, _traceback):
        if self.acquired:
            try:
                os.unlink(self.path)
            except OSError:
                pass


def execute(args):
    block = validate_contract(args.template, args.runtime_root)
    content = read_agents(args.agents_file)

    if args.command == "status":
        location = locate_block(content)
        if location is None:
            return {"ok": True, "installed": False, "current": False}
        start, end = location
        return {
            "ok": True,
            "installed": True,
            "current": content[start:end] == block,
        }

    if args.command == "uninstall":
        action, updated = desired_uninstall(content)
    else:
        action, updated = desired_install(content, block)

    changed = updated != content
    if args.command == "plan":
        return {"ok": True, "action": action, "changed": changed}
    if not args.allow_agents_write:
        raise RuntimeErrorCode("write_approval_required")
    if not changed:
        return {"ok": True, "action": "none", "changed": False}

    lock_path = args.agents_file + ".codex-project-agent-runtime.lock"
    with ExclusiveLock(lock_path):
        locked_content = read_agents(args.agents_file)
        if locked_content != content:
            raise RuntimeErrorCode("agents_file_changed")
        create_backup(args.agents_file, args.backup_dir)
        atomic_write(args.agents_file, updated)
    return {"ok": True, "action": action, "changed": True}


def parser():
    result = JsonArgumentParser(description=__doc__, add_help=False)
    result.add_argument("command", choices=("plan", "install", "status", "uninstall"))
    result.add_argument("--agents-file", default=DEFAULT_AGENTS_FILE)
    result.add_argument("--template", default=DEFAULT_TEMPLATE)
    result.add_argument("--runtime-root", default=DEFAULT_RUNTIME_ROOT)
    result.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR)
    result.add_argument("--allow-agents-write", action="store_true")
    return result


def run(argv=None):
    try:
        args = parser().parse_args(argv)
        payload = execute(args)
        emit(payload)
        return 0
    except RuntimeErrorCode as error:
        emit({"ok": False, "error": error.code})
        return 2
    except SystemExit:
        emit({"ok": False, "error": "usage_error"})
        return 2
    except Exception:
        emit({"ok": False, "error": "internal_error"})
        return 2


if __name__ == "__main__":
    sys.exit(run())
