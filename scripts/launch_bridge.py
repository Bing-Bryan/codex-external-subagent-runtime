#!/usr/bin/env python3
"""Create a V1 thread as Luna, switch it to Sol/Ultra, and send no turns."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import queue
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from uuid import UUID

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from bridge_runtime import BridgeRuntimeError, build_runtime_allowlist, developer_instructions


LUNA_MODEL = "gpt-5.6-luna"
SOL_MODEL = "gpt-5.6-sol"
SOL_ROOT_EFFORT = "ultra"
DEFAULT_STARTUP_TIMEOUT = 20
DEFAULT_MODEL_TIMEOUT = 20
DEFAULT_THREAD_TIMEOUT = 60
DEFAULT_SETTINGS_TIMEOUT = 30
DEFAULT_READ_TIMEOUT = 30
MAX_PHASE_TIMEOUT = 180
RUNTIME_NAME = "codex-external-subagent-bridge"


class LauncherError(RuntimeError):
    def __init__(self, code: str, thread_id: str | None = None):
        super().__init__(code)
        self.code = code
        self.thread_id = thread_id


class JsonArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise LauncherError("invalid_arguments")


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".codex").resolve()


def is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def load_projects_registry(path: Path) -> list[dict[str, str]]:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise LauncherError("projects_registry_invalid")
        data = json.loads(path.read_text(encoding="utf-8"))
    except LauncherError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise LauncherError("projects_registry_unreadable") from exc
    if not isinstance(data, dict) or set(data) != {"version", "projects"} or data.get("version") != 1:
        raise LauncherError("projects_registry_invalid")
    raw_projects = data.get("projects")
    if not isinstance(raw_projects, list) or not raw_projects:
        raise LauncherError("projects_registry_invalid")

    projects: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    seen_cwds: set[str] = set()
    seen_labels: set[str] = set()
    for item in raw_projects:
        if not isinstance(item, dict) or set(item) != {"projectId", "label", "cwd"}:
            raise LauncherError("projects_registry_invalid")
        project_id = item.get("projectId")
        label = item.get("label")
        cwd_text = item.get("cwd")
        if not all(isinstance(value, str) and value.strip() for value in (project_id, label, cwd_text)):
            raise LauncherError("projects_registry_invalid")
        if len(label) > 80 or any(ord(char) < 32 for char in label):
            raise LauncherError("projects_registry_invalid")
        try:
            parsed_id = UUID(project_id)
        except ValueError as exc:
            raise LauncherError("projects_registry_invalid") from exc
        if str(parsed_id) != project_id:
            raise LauncherError("projects_registry_invalid")
        configured_cwd = Path(cwd_text).expanduser()
        if not configured_cwd.is_absolute() or configured_cwd == Path(configured_cwd.anchor):
            raise LauncherError("projects_registry_invalid")
        try:
            resolved_cwd = configured_cwd.resolve(strict=True)
        except OSError as exc:
            raise LauncherError("projects_registry_invalid") from exc
        if not resolved_cwd.is_dir():
            raise LauncherError("projects_registry_invalid")
        canonical = str(resolved_cwd)
        if project_id in seen_ids or canonical in seen_cwds or label in seen_labels:
            raise LauncherError("projects_registry_invalid")
        seen_ids.add(project_id)
        seen_cwds.add(canonical)
        seen_labels.add(label)
        projects.append({"projectId": project_id, "label": label, "cwd": canonical})
    return projects


def resolve_approved_project(
    projects: list[dict[str, str]], project_id: str, cwd: str
) -> tuple[str, Path]:
    matches = [item for item in projects if item["projectId"] == project_id]
    if len(matches) != 1:
        raise LauncherError("project_scope_rejected")
    requested = Path(cwd).expanduser()
    if not requested.is_absolute():
        raise LauncherError("invalid_cwd")
    try:
        canonical = requested.resolve(strict=True)
    except OSError as exc:
        raise LauncherError("invalid_cwd") from exc
    if not canonical.is_dir():
        raise LauncherError("invalid_cwd")
    project = matches[0]
    if str(canonical) != project["cwd"]:
        raise LauncherError("project_binding_mismatch")
    return project["label"], canonical


def require_global_v1(home: Path) -> None:
    try:
        import tomllib
    except ModuleNotFoundError:
        tomllib = None
    try:
        config_path = home / "config.toml"
        if config_path.is_symlink() or not config_path.resolve(strict=True).is_file():
            raise LauncherError("global_config_unreadable")
        text = config_path.read_text(encoding="utf-8")
        if tomllib is not None:
            parsed = tomllib.loads(text)
            features = parsed.get("features")
        else:
            section = ""
            features = {}
            pattern = re.compile(r"^(multi_agent|multi_agent_v2)\s*=\s*(true|false)\s*$")
            for raw in text.splitlines():
                line = raw.split("#", 1)[0].strip()
                if line.startswith("[") and line.endswith("]"):
                    section = line[1:-1].strip()
                    continue
                if section == "features":
                    match = pattern.fullmatch(line)
                    if match:
                        features[match.group(1)] = match.group(2) == "true"
    except LauncherError:
        raise
    except (OSError, UnicodeError) as exc:
        raise LauncherError("global_config_unreadable") from exc
    except ValueError as exc:
        raise LauncherError("global_config_invalid") from exc
    if not isinstance(features, dict):
        raise LauncherError("global_v1_required")
    if features.get("multi_agent") is not True or features.get("multi_agent_v2") is not False:
        raise LauncherError("global_v1_required")


def require_codex_state_outside_project(home: Path, project_cwd: Path, runtime_dir: Path) -> None:
    paths = [home, home / "config.toml", runtime_dir, home / "agents", home / "sessions", home / "archived_sessions"]
    for path in paths:
        try:
            resolved = path.resolve(strict=True)
        except OSError:
            continue
        if is_within(resolved, project_cwd):
            raise LauncherError("codex_state_inside_project")


def executable_candidate(raw: str) -> Path | None:
    expanded = Path(raw).expanduser()
    if expanded.is_file() and os.access(expanded, os.X_OK):
        return expanded.resolve()
    found = shutil.which(raw)
    if found:
        candidate = Path(found)
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    return None


def locate_codex_app_cli() -> Path:
    override = os.environ.get("CODEX_APP_CLI")
    if override:
        candidate = executable_candidate(override.strip())
        if candidate is None:
            raise LauncherError("codex_app_cli_invalid")
        return candidate
    candidates = (
        Path("/Applications/Codex.app/Contents/Resources/codex"),
        Path("/Applications/ChatGPT.app/Contents/Resources/codex"),
        Path.home() / "Applications/Codex.app/Contents/Resources/codex",
        Path.home() / "Applications/ChatGPT.app/Contents/Resources/codex",
    )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise LauncherError("desktop_codex_missing")


def codex_cli_version(binary: Path) -> str:
    try:
        completed = subprocess.run(
            [str(binary), "--version"], check=True, capture_output=True, text=True, timeout=5
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise LauncherError("app_server_version_unavailable") from exc
    version = completed.stdout.strip()
    if re.fullmatch(r"codex-cli\s+\S+", version) is None:
        raise LauncherError("app_server_version_unavailable")
    return version.split(maxsplit=1)[1]


class ExclusiveLock:
    def __init__(self, path: Path):
        self.path = path
        self.fd: int | None = None
        self.identity: tuple[int, int] | None = None

    def __enter__(self) -> "ExclusiveLock":
        try:
            self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise LauncherError("lock_unavailable") from exc
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_CLOEXEC"):
            flags |= os.O_CLOEXEC
        try:
            self.fd = os.open(self.path, flags, 0o600)
        except FileExistsError as exc:
            raise LauncherError("already_running") from exc
        except OSError as exc:
            raise LauncherError("lock_unavailable") from exc
        try:
            state = os.fstat(self.fd)
            self.identity = (state.st_dev, state.st_ino)
            os.write(self.fd, b'{"state":"running"}\n')
        except OSError as exc:
            self.__exit__(None, None, None)
            raise LauncherError("lock_unavailable") from exc
        return self

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
        try:
            current = self.path.lstat()
        except OSError:
            return
        if (
            self.identity is not None
            and not stat.S_ISLNK(current.st_mode)
            and (current.st_dev, current.st_ino) == self.identity
        ):
            try:
                self.path.unlink()
            except OSError:
                pass


class AppServer:
    def __init__(self, binary: Path, runtime_dir: Path):
        self.binary = binary
        self.runtime_dir = runtime_dir
        self.proc: subprocess.Popen[str] | None = None
        self.messages: queue.Queue[str | None] = queue.Queue()
        self.next_id = 1
        self.notifications: list[dict[str, Any]] = []

    def start(self, timeout: int) -> None:
        try:
            self.proc = subprocess.Popen(
                [str(self.binary), "app-server", "--stdio"],
                cwd=str(self.runtime_dir),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise LauncherError("app_server_unavailable") from exc
        threading.Thread(target=self._read_stdout, daemon=True).start()
        self.request(
            "initialize",
            {"clientInfo": {"name": RUNTIME_NAME, "title": "Codex External Subagent Bridge", "version": "2.0.0"}},
            timeout,
            "initialize_failed",
            "startup_timeout",
        )
        self.notify("initialized", {})

    def _read_stdout(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            self.messages.put(None)
            return
        try:
            for line in proc.stdout:
                self.messages.put(line)
        finally:
            self.messages.put(None)

    def close(self) -> None:
        proc = self.proc
        if proc is None:
            return
        if proc.stdin is not None and not proc.stdin.closed:
            try:
                proc.stdin.close()
            except OSError:
                pass
        try:
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.terminate()
                try:
                    proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=3)
        except (OSError, subprocess.SubprocessError):
            pass

    def _write(self, payload: dict[str, Any]) -> None:
        proc = self.proc
        if proc is None or proc.stdin is None or proc.poll() is not None:
            raise LauncherError("app_server_unavailable")
        try:
            proc.stdin.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
            proc.stdin.flush()
        except OSError as exc:
            raise LauncherError("app_server_unavailable") from exc

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def request(
        self,
        method: str,
        params: dict[str, Any],
        timeout: int,
        error_code: str,
        timeout_code: str,
    ) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            message = self._read_message(deadline, timeout_code)
            if message.get("id") == request_id:
                if "error" in message:
                    raise LauncherError(error_code)
                result = message.get("result")
                if not isinstance(result, dict):
                    raise LauncherError(error_code)
                return result
            if isinstance(message.get("method"), str) and "id" not in message:
                self.notifications.append(message)
                continue
            if "method" in message and "id" in message:
                raise LauncherError("unexpected_server_request")

    def _read_message(self, deadline: float, timeout_code: str) -> dict[str, Any]:
        while time.monotonic() < deadline:
            proc = self.proc
            if proc is None:
                raise LauncherError("app_server_unavailable")
            remaining = max(0.0, deadline - time.monotonic())
            try:
                line = self.messages.get(timeout=min(0.5, remaining))
            except queue.Empty:
                if proc.poll() is not None:
                    raise LauncherError("app_server_exited")
                continue
            if line is None:
                raise LauncherError("app_server_exited")
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                return value
        raise LauncherError(timeout_code)

    def matching_notification(
        self,
        method: str,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: int,
        timeout_code: str,
    ) -> dict[str, Any]:
        for message in self.notifications:
            if message.get("method") == method and predicate(message):
                return message
        deadline = time.monotonic() + timeout
        while True:
            message = self._read_message(deadline, timeout_code)
            if message.get("method") == method and "id" not in message and predicate(message):
                self.notifications.append(message)
                return message
            if isinstance(message.get("method"), str) and "id" not in message:
                self.notifications.append(message)
                continue
            raise LauncherError("app_server_response_invalid")


def require_models(server: AppServer, timeout: int) -> None:
    models: list[dict[str, Any]] = []
    cursor: str | None = None
    for _ in range(20):
        params: dict[str, Any] = {"limit": 100}
        if cursor is not None:
            params["cursor"] = cursor
        result = server.request("model/list", params, timeout, "model_list_failed", "model_list_timeout")
        page = result.get("data")
        if not isinstance(page, list) or not all(isinstance(item, dict) for item in page):
            raise LauncherError("model_list_invalid")
        models.extend(page)
        cursor = result.get("nextCursor")
        if cursor is None:
            break
        if not isinstance(cursor, str) or not cursor:
            raise LauncherError("model_list_invalid")
    else:
        raise LauncherError("model_list_invalid")

    by_model = {item.get("model"): item for item in models if isinstance(item.get("model"), str)}
    luna = by_model.get(LUNA_MODEL)
    sol = by_model.get(SOL_MODEL)
    if luna is None or sol is None:
        raise LauncherError("required_model_unavailable")
    efforts = sol.get("supportedReasoningEfforts")
    if not isinstance(efforts, list):
        raise LauncherError("required_model_unavailable")
    supported = {
        item.get("reasoningEffort")
        for item in efforts
        if isinstance(item, dict) and isinstance(item.get("reasoningEffort"), str)
    }
    if SOL_ROOT_EFFORT not in supported:
        raise LauncherError("required_model_unavailable")


def load_route_policy(home: Path, providers_path: Path, evidence_path: Path) -> dict[str, list[dict[str, Any]]]:
    if not providers_path.exists():
        return {"allowed": [], "rejected": []}
    try:
        return build_runtime_allowlist(home, providers_path, evidence_path)
    except BridgeRuntimeError as exc:
        raise LauncherError(exc.code) from exc


def absolute_without_symlink_resolution(path: Path) -> Path:
    expanded = path.expanduser()
    return expanded if expanded.is_absolute() else Path.cwd() / expanded


def parse_args() -> argparse.Namespace:
    home = codex_home()
    runtime = home / RUNTIME_NAME
    parser = JsonArgumentParser(description=__doc__)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--projects-registry", type=Path, default=runtime / "projects.json")
    parser.add_argument("--providers-registry", type=Path, default=runtime / "providers.json")
    parser.add_argument("--smoke-evidence", type=Path, default=runtime / "smoke-evidence.json")
    parser.add_argument(
        "--startup-timeout-seconds",
        type=int,
        default=DEFAULT_STARTUP_TIMEOUT,
        choices=range(1, MAX_PHASE_TIMEOUT + 1),
    )
    parser.add_argument(
        "--model-timeout-seconds",
        type=int,
        default=DEFAULT_MODEL_TIMEOUT,
        choices=range(1, MAX_PHASE_TIMEOUT + 1),
    )
    parser.add_argument(
        "--thread-timeout-seconds",
        type=int,
        default=DEFAULT_THREAD_TIMEOUT,
        choices=range(1, MAX_PHASE_TIMEOUT + 1),
    )
    parser.add_argument(
        "--settings-timeout-seconds",
        type=int,
        default=DEFAULT_SETTINGS_TIMEOUT,
        choices=range(1, MAX_PHASE_TIMEOUT + 1),
    )
    parser.add_argument(
        "--read-timeout-seconds",
        type=int,
        default=DEFAULT_READ_TIMEOUT,
        choices=range(1, MAX_PHASE_TIMEOUT + 1),
    )
    return parser.parse_args()


def run() -> dict[str, Any]:
    args = parse_args()
    home = codex_home()
    runtime_dir = home / RUNTIME_NAME
    projects_path = absolute_without_symlink_resolution(args.projects_registry)
    providers_path = absolute_without_symlink_resolution(args.providers_registry)
    evidence_path = absolute_without_symlink_resolution(args.smoke_evidence)
    projects = load_projects_registry(projects_path)
    project_label, project_cwd = resolve_approved_project(projects, args.project_id, args.cwd)
    require_codex_state_outside_project(home, project_cwd, runtime_dir)
    require_global_v1(home)
    route_policy = load_route_policy(home, providers_path, evidence_path)
    instructions = developer_instructions(route_policy["allowed"])
    binary = locate_codex_app_cli()
    if is_within(binary, project_cwd):
        raise LauncherError("codex_app_cli_inside_project")
    cli_version = codex_cli_version(binary)

    lock_path = runtime_dir / "launch.lock"
    thread_id: str | None = None
    with ExclusiveLock(lock_path):
        server = AppServer(binary, runtime_dir)
        try:
            server.start(args.startup_timeout_seconds)
            require_models(server, args.model_timeout_seconds)
            started = server.request(
                "thread/start",
                {
                    "cwd": str(project_cwd),
                    "model": LUNA_MODEL,
                    "developerInstructions": instructions,
                    "allowProviderModelFallback": False,
                    "config": {
                        "features": {"multi_agent": True, "multi_agent_v2": False},
                        "project_doc_max_bytes": 0,
                        "project_doc_fallback_filenames": [],
                    },
                    "ephemeral": False,
                },
                args.thread_timeout_seconds,
                "thread_start_failed",
                "thread_start_timeout",
            )
            thread = started.get("thread")
            thread = thread if isinstance(thread, dict) else {}
            thread_id = thread.get("id")
            if not isinstance(thread_id, str):
                raise LauncherError("thread_id_missing")
            try:
                UUID(thread_id)
            except ValueError as exc:
                raise LauncherError("thread_id_invalid", thread_id) from exc
            started_cwd = started.get("cwd", thread.get("cwd"))
            started_model = started.get("model")
            if started_cwd != str(project_cwd) or started_model != LUNA_MODEL:
                raise LauncherError("thread_start_override_failed", thread_id)

            server.request(
                "thread/settings/update",
                {"threadId": thread_id, "model": SOL_MODEL, "effort": SOL_ROOT_EFFORT},
                args.settings_timeout_seconds,
                "settings_update_failed",
                "settings_timeout",
            )
            read = server.request(
                "thread/read",
                {"threadId": thread_id, "includeTurns": True},
                args.read_timeout_seconds,
                "thread_read_failed",
                "thread_read_timeout",
            )
            read_thread = read.get("thread")
            if not isinstance(read_thread, dict):
                raise LauncherError("thread_read_invalid", thread_id)
            turns = read_thread.get("turns")
            if (
                read_thread.get("id") != thread_id
                or read_thread.get("cwd") != str(project_cwd)
                or not isinstance(turns, list)
                or turns
            ):
                raise LauncherError("thread_read_invalid", thread_id)

            notification = server.matching_notification(
                "thread/settings/updated",
                lambda item: isinstance(item.get("params"), dict) and item["params"].get("threadId") == thread_id,
                args.read_timeout_seconds,
                "settings_verification_timeout",
            )
            params = notification.get("params")
            settings = params.get("threadSettings") if isinstance(params, dict) else None
            if (
                not isinstance(settings, dict)
                or settings.get("model") != SOL_MODEL
                or settings.get("effort") != SOL_ROOT_EFFORT
                or settings.get("cwd") != str(project_cwd)
            ):
                raise LauncherError("settings_verification_failed", thread_id)
        except LauncherError as exc:
            if exc.thread_id is None and thread_id is not None:
                exc.thread_id = thread_id
            raise
        except Exception as exc:
            raise LauncherError("launcher_internal_error", thread_id) from exc
        finally:
            server.close()

    return {
        "ok": True,
        "threadId": thread_id,
        "requestedProjectId": args.project_id,
        "projectLabel": project_label,
        "cwd": str(project_cwd),
        "projectBinding": "desktop_required",
        "startModel": LUNA_MODEL,
        "model": SOL_MODEL,
        "effort": SOL_ROOT_EFFORT,
        "multiAgentVersion": "v1",
        "globalMultiAgentV2": False,
        "bootstrapTurns": 0,
        "settingsVerified": True,
        "allowedRoutes": route_policy["allowed"],
        "rejectedRoutes": route_policy["rejected"],
        "codexCliVersion": cli_version,
    }


def main() -> int:
    try:
        payload = run()
    except LauncherError as exc:
        error: dict[str, Any] = {"ok": False, "error": exc.code}
        if exc.thread_id is not None:
            error["threadId"] = exc.thread_id
        print(json.dumps(error, separators=(",", ":")), file=sys.stderr)
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
