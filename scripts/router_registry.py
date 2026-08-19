#!/usr/bin/env python3
"""Deterministic, fail-closed route registry CLI for the project agent router.

Commands:
  validate
  resolve --capability SLUG [--route-id SLUG] [--now RFC3339]
  fingerprint FILE [FILE ...]
  validate-packet (--packet FILE | --packet-hex LOWERCASE_UTF8_HEX)
  prepare-job --task-name SLUG --route-id SLUG --capability SLUG
              (--packet FILE | --packet-hex LOWERCASE_UTF8_HEX)
  read-job --task-name SLUG
  claim-job
  cleanup-job --task-name SLUG

stdout is always exactly one JSON object. Exit code is 0 on success and 2 on
validation/resolution errors. Python 3.9+ standard library only.
"""

import contextlib
import datetime
import fcntl
import hashlib
import json
import os
import re
import stat
import struct
import sys
import tempfile
import uuid


VERSION = 1

DEFAULT_ROUTES_PATH = os.path.join(
    os.path.expanduser("~"), ".codex", "project-agent-runtime", "routes.json"
)
DEFAULT_EVIDENCE_PATH = os.path.join(
    os.path.expanduser("~"), ".codex", "project-agent-runtime", "smoke-evidence.json"
)
DEFAULT_JOBS_DIR = os.path.join(
    os.path.realpath(tempfile.gettempdir()),
    "codex-project-agent-runtime-jobs-%d" % os.getuid(),
)

KINDS = ("native-agent", "skill-tool")
NATIVE_TRANSPORTS = ("message", "runtime-job")
SKILL_TRANSPORT = "direct"
WRITE_MODES = ("read-only", "workspace")
RESULT_VALUES = ("passed", "failed")
WRITE_PERMISSIONS = ("none", "read-only", "workspace")
WRITE_PERMISSION_RANK = {"none": 0, "read-only": 1, "workspace": 2}

SMOKE_TTL_MIN = 60
SMOKE_TTL_MAX = 31536000
PACKET_TIMEOUT_MIN = 1
PACKET_TIMEOUT_MAX = 3600
JOB_TTL_MIN = 30
JOB_TTL_MAX = 900
DEFAULT_JOB_TTL = 300
MAX_PACKET_JSON_BYTES = 65536
MAX_JOB_BYTES = MAX_PACKET_JSON_BYTES + 4096

SLUG_RE = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")
HEX64_RE = re.compile(r"^[0-9a-f]{64}$")
TASK_SUFFIX_RE = re.compile(r"_[0-9a-f]{12,32}$")
SKILL_TARGET_RE = re.compile(r"^\$[a-z0-9]+(?:[_-][a-z0-9]+)*$")
MCP_TARGET_RE = re.compile(
    r"^mcp__[a-z0-9]+(?:_[a-z0-9]+)*__[a-z0-9]+(?:_[a-z0-9]+)*$"
)
MARKER_RE = re.compile(r"(?=.*[A-Z0-9])[A-Z0-9 _-]{3,128}")

ROUTE_FIELDS = frozenset(
    {
        "id",
        "kind",
        "target",
        "capabilities",
        "enabled",
        "configFingerprint",
        "smokeTtlSeconds",
        "writeMode",
        "transport",
    }
)
JOB_FIELDS = frozenset(
    {
        "version",
        "taskName",
        "routeId",
        "target",
        "configFingerprint",
        "createdAt",
        "expiresAt",
        "creatorThreadHash",
        "claimedThreadHash",
        "packet",
    }
)
EVIDENCE_FIELDS = frozenset(
    {"routeId", "configFingerprint", "passedAt", "result"}
)
PACKET_FIELDS = frozenset(
    {
        "objective",
        "canonicalCwd",
        "inputs",
        "allowedFiles",
        "writePermission",
        "expectedOutput",
        "acceptanceMarker",
        "timeoutSeconds",
    }
)

FORBIDDEN_FIELD_RE = re.compile(
    r"^(?:url|uri|endpoint|note|notes|env|environment|credential|credentials|"
    r"secret|secrets|token|tokens|password|passwd|api[_-]?key|access[_-]?key|"
    r"authorization|auth[_-]?token)$",
    re.IGNORECASE,
)

SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:api[_-]?key|access[_-]?key|secret|passwd|password|bearer|token|"
    r"private[_-]?key|client[_-]?secret|authorization|auth[_-]?token|credential)"
    r"\s*[:=]\s*\S"
)
SECRET_VALUE_RE = re.compile(
    r"(?i)\b(?:sk-[a-z0-9_-]{8,}|gh[pousr]_[a-z0-9]{20,}|xox[baprs]-[a-z0-9-]{10,}|"
    r"AKIA[0-9A-Z]{16}|eyJ[a-z0-9_-]{10,}\.[a-z0-9_-]{10,}\.[a-z0-9_-]{10,})\b"
)
URL_SCHEME_RE = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
URL_WITH_USERINFO_RE = re.compile(r"://[^/\s:@]+:[^/\s@]*@")

RFC3339_RE = re.compile(
    r"^(?P<year>\d{4})-(?P<month>\d{2})-(?P<day>\d{2})"
    r"[Tt](?P<hour>\d{2}):(?P<minute>\d{2}):(?P<second>\d{2})"
    r"(?P<frac>\.\d+)?"
    r"(?P<tz>[Zz]|[+-]\d{2}:\d{2})$"
)


class CliError(Exception):
    def __init__(self, code, **fields):
        super().__init__(code)
        self.code = code
        self.fields = fields


def _string_values(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for value in obj.values():
            yield from _string_values(value)
    elif isinstance(obj, list):
        for item in obj:
            yield from _string_values(item)


def _looks_like_url(value):
    return bool(URL_SCHEME_RE.match(value) or "://" in value or value.lower().startswith("www."))


def _looks_like_secret(value):
    if URL_WITH_USERINFO_RE.search(value):
        return True
    if SECRET_ASSIGNMENT_RE.search(value):
        return True
    if SECRET_VALUE_RE.search(value):
        return True
    # Normalized absolute paths often contain long random directory names and
    # mixed-case system components. Explicit secret signatures above still
    # apply, but path entropy alone is not evidence of a credential.
    if (
        value != os.path.sep
        and os.path.isabs(value)
        and os.path.abspath(value) == value
    ):
        return False
    if len(value) < 40 or any(char.isspace() for char in value):
        return False
    has_lower = any(char.islower() for char in value)
    has_upper = any(char.isupper() for char in value)
    has_digit = any(char.isdigit() for char in value)
    return has_lower and has_upper and has_digit


def parse_rfc3339(text):
    if not isinstance(text, str):
        return None
    match = RFC3339_RE.match(text)
    if match is None:
        return None
    try:
        microsecond = 0
        frac = match.group("frac")
        if frac:
            microsecond = int((frac[1:] + "000000")[:6])
        parsed = datetime.datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
            int(match.group("hour")),
            int(match.group("minute")),
            int(match.group("second")),
            microsecond,
        )
    except ValueError:
        return None
    tz = match.group("tz")
    if tz in ("Z", "z"):
        return parsed.replace(tzinfo=datetime.timezone.utc)
    sign = 1 if tz[0] == "+" else -1
    try:
        offset_hours = int(tz[1:3])
        offset_minutes = int(tz[4:6])
    except ValueError:
        return None
    if offset_hours > 23 or offset_minutes > 59:
        return None
    offset = datetime.timedelta(hours=offset_hours, minutes=offset_minutes)
    if offset >= datetime.timedelta(hours=24):
        return None
    return parsed.replace(tzinfo=datetime.timezone(sign * offset))


def validate_route(route):
    if not isinstance(route, dict):
        raise CliError("invalid_registry")
    if set(route.keys()) != ROUTE_FIELDS:
        for key in route.keys():
            if FORBIDDEN_FIELD_RE.match(key):
                raise CliError("invalid_registry")
        raise CliError("invalid_registry")
    rid = route["id"]
    if not isinstance(rid, str) or not SLUG_RE.match(rid):
        raise CliError("invalid_registry")
    kind = route["kind"]
    if kind not in KINDS:
        raise CliError("invalid_registry")
    target = route["target"]
    if not isinstance(target, str):
        raise CliError("invalid_registry")
    if kind == "skill-tool":
        if not (SKILL_TARGET_RE.match(target) or MCP_TARGET_RE.match(target)):
            raise CliError("invalid_registry")
    elif not SLUG_RE.match(target):
        raise CliError("invalid_registry")
    capabilities = route["capabilities"]
    if not isinstance(capabilities, list) or not capabilities:
        raise CliError("invalid_registry")
    for capability in capabilities:
        if not isinstance(capability, str) or not SLUG_RE.match(capability):
            raise CliError("invalid_registry")
    if len(capabilities) != len(set(capabilities)):
        raise CliError("invalid_registry")
    if type(route["enabled"]) is not bool:
        raise CliError("invalid_registry")
    fingerprint = route["configFingerprint"]
    if not isinstance(fingerprint, str) or not HEX64_RE.match(fingerprint):
        raise CliError("invalid_registry")
    ttl = route["smokeTtlSeconds"]
    if type(ttl) is not int or not (SMOKE_TTL_MIN <= ttl <= SMOKE_TTL_MAX):
        raise CliError("invalid_registry")
    if route["writeMode"] not in WRITE_MODES:
        raise CliError("invalid_registry")
    transport = route["transport"]
    if kind == "native-agent":
        if transport not in NATIVE_TRANSPORTS:
            raise CliError("invalid_registry")
    elif transport != SKILL_TRANSPORT:
        raise CliError("invalid_registry")
    for value in _string_values(route):
        if _looks_like_url(value) or _looks_like_secret(value):
            raise CliError("invalid_registry")


def validate_registry(data):
    if not isinstance(data, dict):
        raise CliError("invalid_registry")
    if set(data.keys()) != {"version", "routes"}:
        raise CliError("invalid_registry")
    version = data.get("version")
    if type(version) is not int or version != VERSION:
        raise CliError("invalid_registry")
    routes = data.get("routes")
    if not isinstance(routes, list):
        raise CliError("invalid_registry")
    seen = set()
    for route in routes:
        validate_route(route)
        if route["id"] in seen:
            raise CliError("invalid_registry")
        seen.add(route["id"])


def load_registry(path):
    if not os.path.exists(path):
        raise CliError("registry_missing")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        raise CliError("invalid_registry")
    validate_registry(data)
    return data["routes"]


def validate_evidence_entry(entry):
    if not isinstance(entry, dict):
        raise CliError("invalid_evidence")
    if set(entry.keys()) != EVIDENCE_FIELDS:
        raise CliError("invalid_evidence")
    rid = entry["routeId"]
    if not isinstance(rid, str) or not SLUG_RE.match(rid):
        raise CliError("invalid_evidence")
    fingerprint = entry["configFingerprint"]
    if not isinstance(fingerprint, str) or not HEX64_RE.match(fingerprint):
        raise CliError("invalid_evidence")
    passed_at = parse_rfc3339(entry["passedAt"])
    if passed_at is None:
        raise CliError("invalid_evidence")
    if entry["result"] not in RESULT_VALUES:
        raise CliError("invalid_evidence")
    return {
        "routeId": rid,
        "configFingerprint": fingerprint,
        "passedAt": passed_at,
        "result": entry["result"],
    }


def validate_evidence(data):
    if not isinstance(data, dict):
        raise CliError("invalid_evidence")
    if set(data.keys()) != {"version", "evidence"}:
        raise CliError("invalid_evidence")
    version = data.get("version")
    if type(version) is not int or version != VERSION:
        raise CliError("invalid_evidence")
    entries = data.get("evidence")
    if not isinstance(entries, list):
        raise CliError("invalid_evidence")
    return [validate_evidence_entry(entry) for entry in entries]


def load_evidence(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        raise CliError("invalid_evidence")
    return validate_evidence(data)


def check_eligibility(route, evidence, now):
    if not route["enabled"]:
        return False, "route_disabled"
    entries = [entry for entry in evidence if entry["routeId"] == route["id"]]
    matching = [
        entry
        for entry in entries
        if entry["configFingerprint"] == route["configFingerprint"]
    ]
    if entries and not matching:
        return False, "fingerprint_mismatch"
    if not matching:
        return False, "smoke_missing"
    latest = max(matching, key=lambda entry: entry["passedAt"])
    if latest["result"] != "passed":
        return False, "smoke_failed"
    age_seconds = (now - latest["passedAt"]).total_seconds()
    if age_seconds < 0:
        return False, "smoke_from_future"
    if age_seconds > route["smokeTtlSeconds"]:
        return False, "smoke_expired"
    return True, None


def resolve_route(routes, evidence, capability, route_id=None, now=None):
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    if route_id is None:
        candidates = [route for route in routes if capability in route["capabilities"]]
        eligible = []
        for route in candidates:
            ok, _reason = check_eligibility(route, evidence, now)
            if ok:
                eligible.append(route)
        if not eligible:
            raise CliError(
                "route_unavailable", routeIds=[route["id"] for route in candidates]
            )
        if len(eligible) > 1:
            raise CliError(
                "route_ambiguous", routeIds=[route["id"] for route in eligible]
            )
        return eligible[0]
    matches = [route for route in routes if route["id"] == route_id]
    if not matches:
        raise CliError("route_not_found", routeId=route_id)
    route = matches[0]
    if capability not in route["capabilities"]:
        raise CliError("capability_mismatch", routeId=route_id)
    ok, reason = check_eligibility(route, evidence, now)
    if not ok:
        raise CliError(reason, routeId=route_id)
    return route


def success_payload(route):
    if route["kind"] == "native-agent":
        invoke = "spawn-agent"
        surface = "subtask-card"
    else:
        invoke = "explicit-skill" if route["target"].startswith("$") else "mcp-tool"
        surface = "main-task-tool-call"
    return {
        "ok": True,
        "route": {
            "id": route["id"],
            "kind": route["kind"],
            "target": route["target"],
            "capabilities": list(route["capabilities"]),
            "writeMode": route["writeMode"],
            "transport": route["transport"],
        },
        "execution": {"invoke": invoke, "surface": surface},
    }


def compute_fingerprint(paths):
    ordered = sorted({os.path.abspath(path) for path in paths})
    digest = hashlib.sha256()
    for path in ordered:
        if os.path.islink(path) or not os.path.isfile(path):
            raise CliError("invalid_input")
        try:
            with open(path, "rb") as fh:
                data = fh.read()
        except OSError:
            raise CliError("invalid_input")
        digest.update(struct.pack(">Q", len(data)))
        digest.update(data)
    return {"ok": True, "configFingerprint": digest.hexdigest()}


def validate_packet(data, route_write_mode=None):
    if not isinstance(data, dict):
        raise CliError("invalid_packet")
    if set(data.keys()) != PACKET_FIELDS:
        raise CliError("invalid_packet")
    try:
        packet_size = len(
            json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
        )
    except (TypeError, ValueError):
        raise CliError("invalid_packet")
    if packet_size > MAX_PACKET_JSON_BYTES:
        raise CliError("invalid_packet")
    objective = data["objective"]
    if not isinstance(objective, str) or not objective.strip():
        raise CliError("invalid_packet")
    canonical_cwd = data["canonicalCwd"]
    if (
        not isinstance(canonical_cwd, str)
        or not os.path.isabs(canonical_cwd)
        or os.path.abspath(canonical_cwd) != canonical_cwd
        or canonical_cwd == os.path.sep
    ):
        raise CliError("invalid_packet")
    inputs = data["inputs"]
    if not isinstance(inputs, list) or not all(isinstance(item, str) for item in inputs):
        raise CliError("invalid_packet")
    allowed_files = data["allowedFiles"]
    if not isinstance(allowed_files, list) or not all(
        isinstance(item, str) for item in allowed_files
    ):
        raise CliError("invalid_packet")
    if any(
        not os.path.isabs(item)
        or os.path.abspath(item) != item
        or item == os.path.sep
        for item in allowed_files
    ):
        raise CliError("invalid_packet")
    for item in inputs:
        if not os.path.isabs(item):
            continue
        if os.path.abspath(item) != item or item == os.path.sep:
            raise CliError("invalid_packet")
        covered = False
        for allowed in allowed_files:
            try:
                if os.path.commonpath((item, allowed)) == allowed:
                    covered = True
                    break
            except ValueError:
                continue
        if not covered:
            raise CliError("input_path_not_allowed")
    if data["writePermission"] not in WRITE_PERMISSIONS:
        raise CliError("invalid_packet")
    if route_write_mode is not None:
        if route_write_mode not in WRITE_MODES:
            raise CliError("usage_error")
        if (
            WRITE_PERMISSION_RANK[data["writePermission"]]
            > WRITE_PERMISSION_RANK[route_write_mode]
        ):
            raise CliError("write_permission_exceeds_route")
    expected_output = data["expectedOutput"]
    if not isinstance(expected_output, str) or not expected_output.strip():
        raise CliError("invalid_packet")
    marker = data["acceptanceMarker"]
    if not isinstance(marker, str) or not MARKER_RE.fullmatch(marker):
        raise CliError("invalid_packet")
    timeout = data["timeoutSeconds"]
    if type(timeout) is not int or not (
        PACKET_TIMEOUT_MIN <= timeout <= PACKET_TIMEOUT_MAX
    ):
        raise CliError("invalid_packet")
    for value in _string_values(data):
        if "\x00" in value or _looks_like_secret(value):
            raise CliError("invalid_packet")


def load_packet(path, route_write_mode=None):
    try:
        if path == "-":
            data = json.load(sys.stdin)
        else:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
    except (OSError, ValueError):
        raise CliError("invalid_packet")
    validate_packet(data, route_write_mode=route_write_mode)
    return data


def load_packet_hex(value, route_write_mode=None):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PACKET_JSON_BYTES * 2
        or len(value) % 2
        or re.fullmatch(r"[0-9a-f]+", value) is None
    ):
        raise CliError("invalid_packet")
    try:
        raw = bytes.fromhex(value)
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise CliError("invalid_packet")
    validate_packet(data, route_write_mode=route_write_mode)
    return data


def load_packet_source(path, hex_value, route_write_mode=None):
    if (path is None) == (hex_value is None):
        raise CliError("usage_error")
    if hex_value is not None:
        return load_packet_hex(hex_value, route_write_mode=route_write_mode)
    return load_packet(path, route_write_mode=route_write_mode)


def _format_rfc3339(value):
    return value.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _thread_identity_hash():
    value = os.environ.get("CODEX_THREAD_ID")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        raise CliError("job_identity_unavailable")
    if str(parsed) != value:
        raise CliError("job_identity_unavailable")
    return hashlib.sha256(value.encode("ascii")).hexdigest()


def _validate_task_name(task_name):
    if (
        not isinstance(task_name, str)
        or len(task_name) > 128
        or not SLUG_RE.fullmatch(task_name)
    ):
        raise CliError("invalid_task_name")


def _jobs_directory(path, create=False):
    path = os.path.abspath(path)
    if (
        path == os.path.sep
        or os.path.islink(path)
        or os.path.realpath(path) != path
    ):
        raise CliError("jobs_dir_unsafe")
    parent = os.path.dirname(path)
    if not os.path.isdir(parent) or os.path.islink(parent):
        raise CliError("jobs_dir_unsafe")
    parent_info = os.stat(parent, follow_symlinks=False)
    private_parent = (
        parent_info.st_uid == os.getuid()
        and not parent_info.st_mode & 0o077
    )
    sticky_root_temp = (
        parent_info.st_uid == 0
        and bool(parent_info.st_mode & stat.S_ISVTX)
    )
    if not (private_parent or sticky_root_temp):
        raise CliError("jobs_dir_unsafe")
    if os.path.exists(path):
        if not os.path.isdir(path):
            raise CliError("jobs_dir_unsafe")
        try:
            info = os.stat(path, follow_symlinks=False)
        except OSError:
            raise CliError("jobs_dir_unsafe")
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise CliError("jobs_dir_unsafe")
        return path
    if not create:
        return path
    try:
        os.mkdir(path, 0o700)
    except FileExistsError:
        if os.path.islink(path) or not os.path.isdir(path):
            raise CliError("jobs_dir_unsafe")
    except OSError:
        raise CliError("job_write_failed")
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        raise CliError("jobs_dir_unsafe")
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
    ):
        raise CliError("jobs_dir_unsafe")
    return path


def _job_path(jobs_dir, task_name):
    _validate_task_name(task_name)
    return os.path.join(jobs_dir, task_name + ".json")


@contextlib.contextmanager
def _job_queue_lock(jobs_dir):
    lock_path = os.path.join(jobs_dir, ".queue.lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(lock_path, flags, 0o600)
    except OSError:
        raise CliError("job_queue_unsafe")
    try:
        info = os.fstat(descriptor)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise CliError("job_queue_unsafe")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (BlockingIOError, OSError):
            raise CliError("job_queue_busy")
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(descriptor)


def _job_names(jobs_dir):
    try:
        names = os.listdir(jobs_dir)
    except OSError:
        raise CliError("jobs_dir_unsafe")
    task_names = []
    for name in names:
        if not name.endswith(".json"):
            continue
        task_name = name[:-5]
        try:
            _validate_task_name(task_name)
        except CliError:
            raise CliError("job_queue_unsafe")
        task_names.append(task_name)
    return sorted(task_names)


def _task_name_matches_target(task_name, target):
    return task_name.startswith(target + "_") and bool(
        TASK_SUFFIX_RE.search(task_name)
    )


def _write_job_temp(jobs_dir, payload):
    encoded = (
        json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")
    if len(encoded) > MAX_JOB_BYTES:
        raise CliError("job_write_failed")
    try:
        descriptor, temp_path = tempfile.mkstemp(
            prefix=".job-",
            suffix=".tmp",
            dir=jobs_dir,
        )
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = None
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        if "temp_path" in locals():
            try:
                os.unlink(temp_path)
            except OSError:
                pass
        raise CliError("job_write_failed")
    finally:
        if "descriptor" in locals() and descriptor is not None:
            os.close(descriptor)
    return temp_path


def _fsync_directory(path):
    try:
        descriptor = os.open(path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        raise CliError("job_write_failed")


def _write_job(path, payload):
    jobs_dir = os.path.dirname(path)
    temp_path = _write_job_temp(jobs_dir, payload)
    try:
        try:
            os.link(temp_path, path, follow_symlinks=False)
        except FileExistsError:
            raise CliError("job_exists")
        except OSError:
            raise CliError("job_write_failed")
        _fsync_directory(jobs_dir)
    finally:
        try:
            os.unlink(temp_path)
        except OSError:
            pass


def _replace_job(path, payload):
    jobs_dir = os.path.dirname(path)
    temp_path = _write_job_temp(jobs_dir, payload)
    try:
        os.replace(temp_path, path)
        _fsync_directory(jobs_dir)
    except OSError:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise CliError("job_write_failed")


def _load_job(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except FileNotFoundError:
        raise CliError("job_missing")
    except OSError:
        raise CliError("job_unsafe")
    try:
        with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
            info = os.fstat(handle.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > MAX_JOB_BYTES
            ):
                raise CliError("job_unsafe")
            payload = json.load(handle)
    except CliError:
        raise
    except (OSError, UnicodeError, ValueError):
        raise CliError("invalid_job")
    if not isinstance(payload, dict) or set(payload.keys()) != JOB_FIELDS:
        raise CliError("invalid_job")
    if type(payload["version"]) is not int or payload["version"] != VERSION:
        raise CliError("invalid_job")
    _validate_task_name(payload["taskName"])
    if not isinstance(payload["routeId"], str) or not SLUG_RE.fullmatch(
        payload["routeId"]
    ):
        raise CliError("invalid_job")
    if not isinstance(payload["target"], str) or not SLUG_RE.fullmatch(
        payload["target"]
    ):
        raise CliError("invalid_job")
    if not isinstance(payload["configFingerprint"], str) or not HEX64_RE.fullmatch(
        payload["configFingerprint"]
    ):
        raise CliError("invalid_job")
    if (
        not isinstance(payload["creatorThreadHash"], str)
        or not HEX64_RE.fullmatch(payload["creatorThreadHash"])
    ):
        raise CliError("invalid_job")
    if (
        payload["claimedThreadHash"] is not None
        and (
            not isinstance(payload["claimedThreadHash"], str)
            or not HEX64_RE.fullmatch(payload["claimedThreadHash"])
        )
    ):
        raise CliError("invalid_job")
    created_at = parse_rfc3339(payload["createdAt"])
    expires_at = parse_rfc3339(payload["expiresAt"])
    if created_at is None or expires_at is None or expires_at <= created_at:
        raise CliError("invalid_job")
    validate_packet(payload["packet"])
    return payload, expires_at


def _remove_job_artifact(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    return True


def _purge_expired_jobs(jobs_dir, now):
    removed_expired = 0
    removed_invalid = 0
    try:
        names = os.listdir(jobs_dir)
    except OSError:
        raise CliError("jobs_dir_unsafe")
    for name in names:
        if name.startswith(".job-") and name.endswith(".tmp"):
            if not _remove_job_artifact(os.path.join(jobs_dir, name)):
                raise CliError("job_queue_unsafe")
            removed_invalid += 1
            continue
        if not name.endswith(".json"):
            continue
        task_name = name[:-5]
        path = os.path.join(jobs_dir, name)
        try:
            _validate_task_name(task_name)
            payload, expires_at = _load_job(path)
            if payload["taskName"] != task_name:
                raise CliError("invalid_job")
        except CliError:
            if not _remove_job_artifact(path):
                raise CliError("job_queue_unsafe")
            removed_invalid += 1
            continue
        if now <= expires_at:
            continue
        if not _remove_job_artifact(path):
            raise CliError("job_queue_unsafe")
        removed_expired += 1
    return removed_expired, removed_invalid


def prepare_job(
    routes,
    evidence,
    capability,
    route_id,
    task_name,
    packet,
    jobs_dir,
    now,
    ttl_seconds,
):
    route = resolve_route(
        routes, evidence, capability, route_id=route_id, now=now
    )
    if route["kind"] != "native-agent" or route["transport"] != "runtime-job":
        raise CliError("job_transport_unsupported")
    if not _task_name_matches_target(task_name, route["target"]):
        raise CliError("invalid_task_name")
    validate_packet(packet, route_write_mode=route["writeMode"])
    expires_at = now + datetime.timedelta(seconds=ttl_seconds)
    payload = {
        "version": VERSION,
        "taskName": task_name,
        "routeId": route["id"],
        "target": route["target"],
        "configFingerprint": route["configFingerprint"],
        "createdAt": _format_rfc3339(now),
        "expiresAt": _format_rfc3339(expires_at),
        "creatorThreadHash": _thread_identity_hash(),
        "claimedThreadHash": None,
        "packet": packet,
    }
    jobs_dir = _jobs_directory(jobs_dir, create=True)
    with _job_queue_lock(jobs_dir):
        purged_expired, purged_invalid = _purge_expired_jobs(jobs_dir, now)
        existing_jobs = _job_names(jobs_dir)
        if task_name in existing_jobs:
            raise CliError("job_exists")
        if existing_jobs:
            raise CliError("job_queue_busy")
        _write_job(_job_path(jobs_dir, task_name), payload)
    return {
        "ok": True,
        "prepared": True,
        "taskName": task_name,
        "expiresAt": payload["expiresAt"],
        "purgedExpired": purged_expired,
        "purgedInvalid": purged_invalid,
        "route": {
            "id": route["id"],
            "target": route["target"],
            "transport": route["transport"],
        },
    }


def _claim_job_by_name(routes, evidence, task_name, jobs_dir, now):
    path = _job_path(jobs_dir, task_name)
    payload, expires_at = _load_job(path)
    if payload["taskName"] != task_name:
        raise CliError("invalid_job")
    if now > expires_at:
        raise CliError("job_expired")
    matches = [route for route in routes if route["id"] == payload["routeId"]]
    if len(matches) != 1:
        raise CliError("job_context_changed")
    route = matches[0]
    if (
        route["kind"] != "native-agent"
        or route["transport"] != "runtime-job"
        or route["target"] != payload["target"]
        or route["configFingerprint"] != payload["configFingerprint"]
        or not _task_name_matches_target(task_name, route["target"])
    ):
        raise CliError("job_context_changed")
    eligible, _reason = check_eligibility(route, evidence, now)
    if not eligible:
        raise CliError("job_context_changed")
    validate_packet(payload["packet"], route_write_mode=route["writeMode"])
    thread_hash = _thread_identity_hash()
    if payload["claimedThreadHash"] is None:
        payload["claimedThreadHash"] = thread_hash
        _replace_job(path, payload)
    elif payload["claimedThreadHash"] != thread_hash:
        raise CliError("job_already_claimed")
    return {
        "ok": True,
        "taskName": task_name,
        "route": {"id": route["id"], "target": route["target"]},
        "packet": payload["packet"],
    }


def read_job(routes, evidence, task_name, jobs_dir, now):
    jobs_dir = _jobs_directory(jobs_dir, create=False)
    if not os.path.isdir(jobs_dir):
        raise CliError("job_missing")
    with _job_queue_lock(jobs_dir):
        return _claim_job_by_name(
            routes,
            evidence,
            task_name,
            jobs_dir,
            now,
        )


def claim_job(routes, evidence, jobs_dir, now):
    _thread_identity_hash()
    jobs_dir = _jobs_directory(jobs_dir, create=False)
    if not os.path.isdir(jobs_dir):
        raise CliError("job_missing")
    with _job_queue_lock(jobs_dir):
        _purge_expired_jobs(jobs_dir, now)
        task_names = _job_names(jobs_dir)
        if not task_names:
            raise CliError("job_missing")
        if len(task_names) != 1:
            raise CliError("job_queue_ambiguous")
        return _claim_job_by_name(
            routes,
            evidence,
            task_names[0],
            jobs_dir,
            now,
        )


def cleanup_job(task_name, jobs_dir):
    thread_hash = _thread_identity_hash()
    jobs_dir = _jobs_directory(jobs_dir, create=False)
    if not os.path.isdir(jobs_dir):
        return {"ok": True, "taskName": task_name, "removed": False}
    path = _job_path(jobs_dir, task_name)
    with _job_queue_lock(jobs_dir):
        if not os.path.exists(path):
            return {"ok": True, "taskName": task_name, "removed": False}
        if os.path.islink(path) or not os.path.isfile(path):
            raise CliError("job_unsafe")
        payload, _expires_at = _load_job(path)
        if payload["creatorThreadHash"] != thread_hash:
            raise CliError("job_owner_mismatch")
        try:
            os.unlink(path)
        except OSError:
            raise CliError("job_cleanup_failed")
    return {"ok": True, "taskName": task_name, "removed": True}


def _dispatch(argv):
    if not argv:
        raise CliError("usage_error")
    command = argv[0]
    rest = argv[1:]
    routes_path = DEFAULT_ROUTES_PATH
    evidence_path = DEFAULT_EVIDENCE_PATH
    capability = None
    route_id = None
    now_text = None
    packet_path = None
    packet_hex = None
    route_write_mode = None
    task_name = None
    jobs_dir = DEFAULT_JOBS_DIR
    ttl_seconds = DEFAULT_JOB_TTL
    files = []

    index = 0
    while index < len(rest):
        arg = rest[index]
        if arg == "--routes":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            routes_path = rest[index]
        elif arg == "--evidence":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            evidence_path = rest[index]
        elif command in ("resolve", "prepare-job") and arg == "--capability":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            capability = rest[index]
        elif command in ("resolve", "prepare-job") and arg == "--route-id":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            route_id = rest[index]
        elif command in ("resolve", "prepare-job", "read-job", "claim-job") and arg == "--now":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            now_text = rest[index]
        elif command in ("validate-packet", "prepare-job") and arg == "--packet":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            packet_path = rest[index]
        elif command in ("validate-packet", "prepare-job") and arg == "--packet-hex":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            packet_hex = rest[index]
        elif command == "validate-packet" and arg == "--route-write-mode":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            route_write_mode = rest[index]
        elif command in ("prepare-job", "read-job", "cleanup-job") and arg == "--task-name":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            task_name = rest[index]
        elif command in ("prepare-job", "read-job", "claim-job", "cleanup-job") and arg == "--jobs-dir":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            jobs_dir = rest[index]
        elif command == "prepare-job" and arg == "--ttl-seconds":
            index += 1
            if index >= len(rest):
                raise CliError("usage_error")
            try:
                ttl_seconds = int(rest[index])
            except ValueError:
                raise CliError("usage_error")
        elif arg.startswith("-"):
            raise CliError("usage_error")
        elif command == "fingerprint":
            files.append(arg)
        else:
            raise CliError("usage_error")
        index += 1

    if command == "validate":
        routes = load_registry(routes_path)
        if os.path.exists(evidence_path):
            evidence = load_evidence(evidence_path)
            evidence_count = len(evidence)
        else:
            evidence_count = 0
        return {
            "ok": True,
            "valid": True,
            "routes": len(routes),
            "evidence": evidence_count,
        }
    if command == "resolve":
        if capability is None:
            raise CliError("usage_error")
        if now_text is None:
            now = datetime.datetime.now(datetime.timezone.utc)
        else:
            now = parse_rfc3339(now_text)
            if now is None:
                raise CliError("usage_error")
        routes = load_registry(routes_path)
        evidence = load_evidence(evidence_path)
        route = resolve_route(
            routes, evidence, capability, route_id=route_id, now=now
        )
        return success_payload(route)
    if command == "fingerprint":
        if not files:
            raise CliError("usage_error")
        return compute_fingerprint(files)
    if command == "validate-packet":
        load_packet_source(
            packet_path,
            packet_hex,
            route_write_mode=route_write_mode,
        )
        return {"ok": True, "valid": True}
    if command == "prepare-job":
        if (
            capability is None
            or route_id is None
            or task_name is None
            or not (JOB_TTL_MIN <= ttl_seconds <= JOB_TTL_MAX)
        ):
            raise CliError("usage_error")
        if now_text is None:
            now = datetime.datetime.now(datetime.timezone.utc)
        else:
            now = parse_rfc3339(now_text)
            if now is None:
                raise CliError("usage_error")
        routes = load_registry(routes_path)
        evidence = load_evidence(evidence_path)
        packet = load_packet_source(packet_path, packet_hex)
        _validate_task_name(task_name)
        return prepare_job(
            routes,
            evidence,
            capability,
            route_id,
            task_name,
            packet,
            jobs_dir,
            now,
            ttl_seconds,
        )
    if command == "read-job":
        if task_name is None:
            raise CliError("usage_error")
        if now_text is None:
            now = datetime.datetime.now(datetime.timezone.utc)
        else:
            now = parse_rfc3339(now_text)
            if now is None:
                raise CliError("usage_error")
        routes = load_registry(routes_path)
        evidence = load_evidence(evidence_path)
        _validate_task_name(task_name)
        return read_job(routes, evidence, task_name, jobs_dir, now)
    if command == "claim-job":
        if now_text is None:
            now = datetime.datetime.now(datetime.timezone.utc)
        else:
            now = parse_rfc3339(now_text)
            if now is None:
                raise CliError("usage_error")
        routes = load_registry(routes_path)
        evidence = load_evidence(evidence_path)
        return claim_job(routes, evidence, jobs_dir, now)
    if command == "cleanup-job":
        if task_name is None:
            raise CliError("usage_error")
        _validate_task_name(task_name)
        return cleanup_job(task_name, jobs_dir)
    raise CliError("usage_error")


def _emit(payload):
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


def run(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    try:
        payload = _dispatch(argv)
    except CliError as exc:
        payload = {"ok": False, "error": exc.code}
        payload.update(exc.fields)
        return _emit_and_exit(payload, 2)
    except Exception:
        return _emit_and_exit({"ok": False, "error": "internal_error"}, 2)
    return _emit_and_exit(payload, 0)


def _emit_and_exit(payload, code):
    _emit(payload)
    return code


if __name__ == "__main__":
    sys.exit(run())
