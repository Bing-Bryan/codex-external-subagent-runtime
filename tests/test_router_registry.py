"""Tests for the codex-project-agent-router route registry CLI.

Runs the CLI as a subprocess and asserts on stdout JSON, exit codes, and
stable error codes. Python 3.9-compatible (standard library only).
"""

import ast
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest


_TEST_ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_TEST_ROOT, ".."))
CLI_PATH = os.path.join(_REPO_ROOT, "scripts", "router_registry.py")

FP_A = "a" * 64
FP_B = "b" * 64
FP_OLD = "c" * 64
PARENT_THREAD_ID = "00000000-0000-4000-8000-000000000001"
CHILD_THREAD_ID = "00000000-0000-4000-8000-000000000002"
OTHER_THREAD_ID = "00000000-0000-4000-8000-000000000003"


def _routes(*route_list):
    return {"version": 1, "routes": list(route_list)}


def _route(
    rid="worker-a",
    kind="native-agent",
    target="deepseek_worker",
    capabilities=("code",),
    enabled=True,
    fingerprint=FP_A,
    ttl=3600,
    write_mode="workspace",
    transport=None,
):
    if transport is None:
        transport = "runtime-job" if kind == "native-agent" else "direct"
    return {
        "id": rid,
        "kind": kind,
        "target": target,
        "capabilities": list(capabilities),
        "enabled": enabled,
        "configFingerprint": fingerprint,
        "smokeTtlSeconds": ttl,
        "writeMode": write_mode,
        "transport": transport,
    }


def _evidence(route_id, fingerprint=FP_A, passed_at="2026-08-19T00:00:00Z", result="passed"):
    return {
        "routeId": route_id,
        "configFingerprint": fingerprint,
        "passedAt": passed_at,
        "result": result,
    }


def _evidence_file(*entries):
    return {"version": 1, "evidence": list(entries)}


def _valid_packet():
    return {
        "objective": "Implement the registry module",
        "canonicalCwd": "/Users/example/projects/registry",
        "inputs": ["/Users/example/projects/registry/routes.json"],
        "allowedFiles": ["/Users/example/projects/registry"],
        "writePermission": "workspace",
        "expectedOutput": "tests pass",
        "acceptanceMarker": "ACCEPTANCE_OK_2026",
        "timeoutSeconds": 300,
    }


class RouterRegistryCliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = os.path.realpath(self._tmp.name)
        self.routes_path = os.path.join(self.tmp, "routes.json")
        self.evidence_path = os.path.join(self.tmp, "smoke-evidence.json")
        self.packet_path = os.path.join(self.tmp, "packet.json")
        self.jobs_dir = os.path.join(self.tmp, "jobs")
        self.runtime_tmp = os.path.join(self.tmp, "runtime-tmp")
        os.makedirs(self.runtime_tmp, mode=0o700)
        self.write_routes(_routes(_route(), _route(
            rid="skill-pub",
            kind="skill-tool",
            target="$skill-discovery-optimizer",
            capabilities=("skill-publish",),
            fingerprint=FP_B,
            write_mode="read-only",
        )))
        self.write_evidence(_evidence_file(
            _evidence("worker-a"),
            _evidence("skill-pub", fingerprint=FP_B),
        ))

    def write_routes(self, payload):
        with open(self.routes_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def write_evidence(self, payload):
        with open(self.evidence_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def write_packet(self, payload):
        with open(self.packet_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh)

    def write_file(self, name, content):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def run_cli(
        self,
        *args,
        input_text=None,
        home=None,
        thread_id=PARENT_THREAD_ID
    ):
        env = dict(os.environ)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["TMPDIR"] = self.runtime_tmp
        if thread_id is None:
            env.pop("CODEX_THREAD_ID", None)
        else:
            env["CODEX_THREAD_ID"] = thread_id
        if home is not None:
            env["HOME"] = home
        return subprocess.run(
            [sys.executable, CLI_PATH] + list(args),
            capture_output=True,
            text=True,
            input=input_text,
            env=env,
            cwd=self.tmp,
        )

    def run_cli_json(
        self,
        *args,
        expect_code=0,
        input_text=None,
        thread_id=PARENT_THREAD_ID
    ):
        proc = self.run_cli(
            *args,
            input_text=input_text,
            thread_id=thread_id,
        )
        self.assertEqual(
            proc.returncode, expect_code,
            "exit code mismatch\nstdout=%r\nstderr=%r" % (proc.stdout, proc.stderr),
        )
        self.assertNotIn("Traceback", proc.stderr)
        try:
            return json.loads(proc.stdout)
        except ValueError:
            self.fail("stdout is not JSON: %r" % proc.stdout)

    def resolve(self, *args, **kwargs):
        return self.run_cli_json(
            "resolve",
            "--capability", "code",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--now", "2026-08-19T00:00:10Z",
            *args,
            **kwargs,
        )

    def validate(self, **kwargs):
        return self.run_cli_json(
            "validate",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            **kwargs,
        )

    # ------------------------------------------------------------- surfaces

    def test_native_agent_surface(self):
        result = self.resolve("--route-id", "worker-a")
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["route"]["id"], "worker-a")
        self.assertEqual(result["route"]["kind"], "native-agent")
        self.assertEqual(result["route"]["target"], "deepseek_worker")
        self.assertEqual(result["route"]["capabilities"], ["code"])
        self.assertEqual(result["route"]["writeMode"], "workspace")
        self.assertEqual(result["route"]["transport"], "runtime-job")
        self.assertEqual(
            result["execution"],
            {"invoke": "spawn-agent", "surface": "subtask-card"},
        )

    def test_skill_tool_surface(self):
        result = self.resolve("--route-id", "skill-pub", "--capability", "skill-publish")
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["route"]["id"], "skill-pub")
        self.assertEqual(result["route"]["kind"], "skill-tool")
        self.assertEqual(result["route"]["target"], "$skill-discovery-optimizer")
        self.assertEqual(result["route"]["transport"], "direct")
        self.assertEqual(
            result["execution"],
            {"invoke": "explicit-skill", "surface": "main-task-tool-call"},
        )

    def test_direct_mcp_tool_surface(self):
        self.write_routes(_routes(_route(
            rid="mcp-route",
            kind="skill-tool",
            target="mcp__example_server__lookup_item",
            capabilities=("lookup",),
        )))
        self.write_evidence(_evidence_file(_evidence("mcp-route")))
        result = self.run_cli_json(
            "resolve",
            "--capability", "lookup",
            "--route-id", "mcp-route",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--now", "2026-08-19T00:00:10Z",
        )
        self.assertEqual(result["route"]["target"], "mcp__example_server__lookup_item")
        self.assertEqual(
            result["execution"],
            {"invoke": "mcp-tool", "surface": "main-task-tool-call"},
        )

    def test_resolve_output_excludes_fingerprints_and_evidence(self):
        proc = self.run_cli(
            "resolve",
            "--capability", "code",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--now", "2026-08-19T00:00:10Z",
        )
        self.assertEqual(proc.returncode, 0)
        self.assertNotIn("configFingerprint", proc.stdout)
        self.assertNotIn("smokeTtlSeconds", proc.stdout)
        self.assertNotIn("passedAt", proc.stdout)
        self.assertNotIn("evidence", proc.stdout)
        result = json.loads(proc.stdout)
        self.assertEqual(
            set(result.keys()), {"ok", "route", "execution"},
            "unexpected top-level keys",
        )
        self.assertEqual(
            set(result["route"].keys()),
            {"id", "kind", "target", "capabilities", "writeMode", "transport"},
            "unexpected route keys",
        )

    # ------------------------------------------------- eligibility reasons

    def test_disabled_route_reason(self):
        self.write_routes(_routes(_route(enabled=False)))
        err = self.resolve("--route-id", "worker-a", expect_code=2)
        self.assertEqual(err, {"ok": False, "error": "route_disabled", "routeId": "worker-a"})
        err2 = self.resolve(expect_code=2)
        self.assertEqual(err2["ok"], False)
        self.assertEqual(err2["error"], "route_unavailable")
        self.assertEqual(err2["routeIds"], ["worker-a"])

    def test_missing_smoke_reason(self):
        self.write_evidence(_evidence_file())
        err = self.resolve("--route-id", "worker-a", expect_code=2)
        self.assertEqual(err, {"ok": False, "error": "smoke_missing", "routeId": "worker-a"})

    def test_failed_smoke_reason(self):
        self.write_evidence(_evidence_file(
            _evidence("worker-a", passed_at="2026-08-19T00:00:00Z", result="failed"),
        ))
        err = self.resolve("--route-id", "worker-a", expect_code=2)
        self.assertEqual(err, {"ok": False, "error": "smoke_failed", "routeId": "worker-a"})

    def test_expired_smoke_reason(self):
        self.write_evidence(_evidence_file(
            _evidence("worker-a", passed_at="2026-08-19T00:00:00Z"),
        ))
        err = self.resolve(
            "--route-id", "worker-a",
            "--now", "2026-08-19T02:00:00Z",
            expect_code=2,
        )
        self.assertEqual(err, {"ok": False, "error": "smoke_expired", "routeId": "worker-a"})

    def test_future_smoke_reason(self):
        err = self.resolve(
            "--route-id", "worker-a",
            "--now", "2026-08-18T23:59:59Z",
            expect_code=2,
        )
        self.assertEqual(
            err,
            {"ok": False, "error": "smoke_from_future", "routeId": "worker-a"},
        )

    def test_fingerprint_mismatch_reason(self):
        self.write_routes(_routes(_route(fingerprint=FP_OLD)))
        err = self.resolve("--route-id", "worker-a", expect_code=2)
        self.assertEqual(
            err, {"ok": False, "error": "fingerprint_mismatch", "routeId": "worker-a"},
        )

    def test_route_ambiguous_no_fallback(self):
        self.write_routes(_routes(
            _route(rid="worker-a"),
            _route(rid="worker-b", target="luna_researcher", fingerprint=FP_B),
        ))
        self.write_evidence(_evidence_file(
            _evidence("worker-a"),
            _evidence("worker-b", fingerprint=FP_B),
        ))
        err = self.resolve(expect_code=2)
        self.assertEqual(err["ok"], False)
        self.assertEqual(err["error"], "route_ambiguous")
        self.assertEqual(err["routeIds"], ["worker-a", "worker-b"])

    def test_route_unavailable_no_fallback(self):
        self.write_routes(_routes(_route(capabilities=("research",))))
        err = self.resolve(expect_code=2)
        self.assertEqual(err, {"ok": False, "error": "route_unavailable", "routeIds": []})

    def test_capability_mismatch_with_route_id(self):
        err = self.resolve("--route-id", "worker-a", "--capability", "visual", expect_code=2)
        self.assertEqual(
            err, {"ok": False, "error": "capability_mismatch", "routeId": "worker-a"},
        )

    def test_route_not_found(self):
        err = self.resolve("--route-id", "nope", expect_code=2)
        self.assertEqual(err, {"ok": False, "error": "route_not_found", "routeId": "nope"})

    # ------------------------------------------------- registry validation

    def test_validate_accepts_valid_registry_and_evidence(self):
        result = self.validate()
        self.assertEqual(result["ok"], True)
        self.assertEqual(result["valid"], True)

    def test_registry_missing(self):
        os.remove(self.routes_path)
        result = self.validate(expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "registry_missing"})

    def test_duplicate_ids_rejected(self):
        self.write_routes(_routes(_route(), _route(rid="worker-a", target="luna_researcher")))
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )

    def test_unknown_fields_rejected(self):
        route = _route()
        route["note"] = "hand off"
        self.write_routes(_routes(route))
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )

    def test_forbidden_field_names_rejected(self):
        for key in ("url", "env", "credentials", "notes", "token"):
            route = _route()
            route[key] = "something"
            self.write_routes(_routes(route))
            with self.subTest(field=key):
                self.assertEqual(
                    self.validate(expect_code=2),
                    {"ok": False, "error": "invalid_registry"},
                )

    def test_url_looking_values_rejected(self):
        self.write_routes(_routes(_route(target="https://evil.example/spawn")))
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )

    def test_secret_looking_values_rejected(self):
        self.write_routes(_routes(_route(target="sk-0123456789abcdef0123456789")))
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )

    def test_invalid_kind_and_write_mode_rejected(self):
        for kind in ("agent", "native_agent"):
            with self.subTest(kind=kind):
                self.write_routes(_routes(_route(kind=kind)))
                self.assertEqual(
                    self.validate(expect_code=2),
                    {"ok": False, "error": "invalid_registry"},
                )

    def test_transport_must_match_route_kind(self):
        invalid_routes = (
            _route(transport="direct"),
            _route(transport="bogus"),
            _route(kind="skill-tool", target="$example", transport="runtime-job"),
            _route(kind="skill-tool", target="$example", transport="message"),
        )
        for route in invalid_routes:
            with self.subTest(route=route):
                self.write_routes(_routes(route))
                self.assertEqual(
                    self.validate(expect_code=2),
                    {"ok": False, "error": "invalid_registry"},
                )
        for mode in ("readonly", "write"):
            with self.subTest(mode=mode):
                self.write_routes(_routes(_route(write_mode=mode)))
                self.assertEqual(
                    self.validate(expect_code=2),
                    {"ok": False, "error": "invalid_registry"},
                )

    def test_empty_or_duplicate_capabilities_rejected(self):
        for capabilities in ((), ("code", "code")):
            with self.subTest(capabilities=capabilities):
                self.write_routes(_routes(_route(capabilities=capabilities)))
                self.assertEqual(
                    self.validate(expect_code=2),
                    {"ok": False, "error": "invalid_registry"},
                )
    def test_kind_target_mismatch_rejected(self):
        self.write_routes(_routes(_route(kind="skill-tool", target="deepseek_worker")))
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )
        self.write_routes(_routes(_route(target="$deepseek-worker")))
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )
        self.write_routes(_routes(_route(
            kind="skill-tool", target="mcp:server.tool"
        )))
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )

    def test_invalid_fingerprint_and_ttl_rejected(self):
        for fingerprint in ("z" * 64, "a" * 63, "A" * 64, ""):
            with self.subTest(fingerprint=fingerprint):
                self.write_routes(_routes(_route(fingerprint=fingerprint)))
                self.assertEqual(
                    self.validate(expect_code=2),
                    {"ok": False, "error": "invalid_registry"},
                )
        for ttl in (59, 31536001, 3600.5, "3600", True):
            with self.subTest(ttl=ttl):
                self.write_routes(_routes(_route(ttl=ttl)))
                self.assertEqual(
                    self.validate(expect_code=2),
                    {"ok": False, "error": "invalid_registry"},
                )

    def test_upper_version_and_bad_root_rejected(self):
        payload = _routes(_route())
        payload["version"] = 2
        self.write_routes(payload)
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )
        self.write_routes({"version": 1, "routes": "nope"})
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )

    def test_evidence_schema_rejected(self):
        bad_entries = [
            _evidence("worker-a", passed_at="2026-08-19T00:00:00"),  # naive
            _evidence("worker-a", passed_at="not-a-time"),
            _evidence("worker-a", passed_at="2026-08-19T00:00:00+00:99"),
            _evidence("worker-a", fingerprint="c" * 63),
            _evidence("worker-a", result="skipped"),
            _evidence("WORKER-A"),
        ]
        for entry in bad_entries:
            with self.subTest(entry=entry):
                self.write_evidence(_evidence_file(entry))
                self.assertEqual(
                    self.validate(expect_code=2),
                    {"ok": False, "error": "invalid_evidence"},
                )
        entry = _evidence("worker-a")
        entry["extra"] = True
        self.write_evidence(_evidence_file(entry))
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_evidence"},
        )

    def test_malformed_json_files_rejected(self):
        with open(self.routes_path, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_registry"},
        )
        self.write_routes(_routes(_route()))
        with open(self.evidence_path, "w", encoding="utf-8") as fh:
            fh.write("[]")
        self.assertEqual(
            self.validate(expect_code=2), {"ok": False, "error": "invalid_evidence"},
        )

    # ------------------------------------------------------------ fingerprint

    def test_fingerprint_deterministic_and_order_independent(self):
        file_a = self.write_file("a.txt", "hello\n")
        file_b = self.write_file("b.txt", "world\n")
        first = self.run_cli_json("fingerprint", file_b, file_a)
        second = self.run_cli_json("fingerprint", file_a, file_b)
        self.assertEqual(first["ok"], True)
        self.assertEqual(first["configFingerprint"], second["configFingerprint"])
        self.assertEqual(len(first["configFingerprint"]), 64)
        self.assertNotIn(file_a, json.dumps(first))
        self.assertNotIn("hello", json.dumps(first))
        changed = self.write_file("a.txt", "hello world\n")
        third = self.run_cli_json("fingerprint", file_a, file_b)
        self.assertNotEqual(first["configFingerprint"], third["configFingerprint"])
        self.assertEqual(changed, file_a)

    def test_fingerprint_symlink_rejected(self):
        target = self.write_file("target.txt", "data\n")
        link = os.path.join(self.tmp, "link.txt")
        os.symlink(target, link)
        result = self.run_cli_json("fingerprint", link, expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "invalid_input"})

    def test_fingerprint_missing_file_rejected(self):
        result = self.run_cli_json(
            "fingerprint", os.path.join(self.tmp, "missing.txt"), expect_code=2,
        )
        self.assertEqual(result, {"ok": False, "error": "invalid_input"})

    def test_fingerprint_directory_rejected(self):
        result = self.run_cli_json("fingerprint", self.tmp, expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "invalid_input"})

    # ------------------------------------------------------------ task packet

    def test_valid_packet_accepted(self):
        self.write_packet(_valid_packet())
        result = self.run_cli_json("validate-packet", "--packet", self.packet_path)
        self.assertEqual(result, {"ok": True, "valid": True})

    def test_valid_packet_can_be_read_from_stdin(self):
        proc = self.run_cli(
            "validate-packet",
            "--packet", "-",
            "--route-write-mode", "workspace",
            input_text=json.dumps(_valid_packet()),
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {"ok": True, "valid": True})

    def test_valid_packet_can_be_read_from_lowercase_utf8_hex(self):
        packet = dict(_valid_packet(), objective="只读检查主要入口")
        packet_hex = json.dumps(
            packet, ensure_ascii=False
        ).encode("utf-8").hex()
        result = self.run_cli_json(
            "validate-packet",
            "--packet-hex", packet_hex,
            "--route-write-mode", "workspace",
        )
        self.assertEqual(result, {"ok": True, "valid": True})

    def test_packet_hex_rejects_malformed_or_conflicting_input(self):
        valid_hex = json.dumps(_valid_packet()).encode("utf-8").hex()
        for packet_hex in ("", "abc", valid_hex.upper(), "zz"):
            with self.subTest(packet_hex=packet_hex):
                result = self.run_cli_json(
                    "validate-packet",
                    "--packet-hex", packet_hex,
                    expect_code=2,
                )
                self.assertEqual(result, {"ok": False, "error": "invalid_packet"})
        self.write_packet(_valid_packet())
        result = self.run_cli_json(
            "validate-packet",
            "--packet", self.packet_path,
            "--packet-hex", valid_hex,
            expect_code=2,
        )
        self.assertEqual(result, {"ok": False, "error": "usage_error"})
        result = self.run_cli_json(
            "validate-packet",
            expect_code=2,
        )
        self.assertEqual(result, {"ok": False, "error": "usage_error"})

    def test_default_state_directory_is_project_agent_runtime(self):
        codex_dir = os.path.join(self.tmp, ".codex", "project-agent-runtime")
        os.makedirs(codex_dir)
        routes_path = os.path.join(codex_dir, "routes.json")
        evidence_path = os.path.join(codex_dir, "smoke-evidence.json")
        with open(routes_path, "w", encoding="utf-8") as handle:
            json.dump(_routes(_route()), handle)
        with open(evidence_path, "w", encoding="utf-8") as handle:
            json.dump(_evidence_file(_evidence("worker-a")), handle)
        proc = self.run_cli("validate", home=self.tmp)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(
            json.loads(proc.stdout),
            {"ok": True, "valid": True, "routes": 1, "evidence": 1},
        )

    def test_packet_missing_and_unknown_fields_rejected(self):
        packet = _valid_packet()
        del packet["objective"]
        self.write_packet(packet)
        self.assertEqual(
            self.run_cli_json(
                "validate-packet", "--packet", self.packet_path, expect_code=2,
            ),
            {"ok": False, "error": "invalid_packet"},
        )
        packet = _valid_packet()
        packet["extra"] = 1
        self.write_packet(packet)
        self.assertEqual(
            self.run_cli_json(
                "validate-packet", "--packet", self.packet_path, expect_code=2,
            ),
            {"ok": False, "error": "invalid_packet"},
        )

    def test_packet_invalid_fields_rejected(self):
        packets = [
            dict(_valid_packet(), objective=""),
            dict(_valid_packet(), objective="   "),
            dict(_valid_packet(), canonicalCwd="relative/path"),
            dict(_valid_packet(), canonicalCwd="/tmp/../tmp/project"),
            dict(_valid_packet(), canonicalCwd="/"),
            dict(_valid_packet(), inputs="not-a-list"),
            dict(_valid_packet(), inputs=["ok", 42]),
            dict(_valid_packet(), allowedFiles={"a": "b"}),
            dict(_valid_packet(), allowedFiles=["relative/path"]),
            dict(_valid_packet(), allowedFiles=["/"]),
            dict(_valid_packet(), writePermission="write"),
            dict(_valid_packet(), timeoutSeconds=0),
            dict(_valid_packet(), timeoutSeconds=3601),
            dict(_valid_packet(), timeoutSeconds=1.5),
            dict(_valid_packet(), acceptanceMarker="lowercase-ok"),
            dict(_valid_packet(), acceptanceMarker="AB"),
            dict(_valid_packet(), acceptanceMarker="A" * 129),
            dict(_valid_packet(), expectedOutput=""),
            dict(_valid_packet(), expectedOutput="   "),
        ]
        for packet in packets:
            with self.subTest(packet=packet):
                self.write_packet(packet)
                self.assertEqual(
                    self.run_cli_json(
                        "validate-packet", "--packet", self.packet_path, expect_code=2,
                    ),
                    {"ok": False, "error": "invalid_packet"},
                )

    def test_packet_nul_rejected(self):
        self.write_packet(dict(_valid_packet(), objective="bad\x00marker"))
        self.assertEqual(
            self.run_cli_json(
                "validate-packet", "--packet", self.packet_path, expect_code=2,
            ),
            {"ok": False, "error": "invalid_packet"},
        )

    def test_oversized_packet_rejected(self):
        self.write_packet(dict(_valid_packet(), objective="A" * 70000))
        self.assertEqual(
            self.run_cli_json(
                "validate-packet", "--packet", self.packet_path, expect_code=2,
            ),
            {"ok": False, "error": "invalid_packet"},
        )

    def test_packet_secret_rejected_but_public_url_ok(self):
        self.write_packet(dict(_valid_packet(), objective="token=sk-0123456789abcdef"))
        self.assertEqual(
            self.run_cli_json(
                "validate-packet", "--packet", self.packet_path, expect_code=2,
            ),
            {"ok": False, "error": "invalid_packet"},
        )
        self.write_packet(
            dict(_valid_packet(), objective="Pull data from https://example.com/api/v1"),
        )
        result = self.run_cli_json("validate-packet", "--packet", self.packet_path)
        self.assertEqual(result, {"ok": True, "valid": True})

    def test_packet_entropy_like_absolute_paths_are_allowed(self):
        temp_root = "/private/tmp/agent-router-kimi-cd8e5016ce8aa396"
        packet = dict(
            _valid_packet(),
            canonicalCwd="/Users/example/Desktop/project",
            inputs=[temp_root + "/index.html"],
            allowedFiles=[temp_root],
            writePermission="workspace",
        )
        self.write_packet(packet)
        result = self.run_cli_json(
            "validate-packet",
            "--packet", self.packet_path,
            "--route-write-mode", "workspace",
        )
        self.assertEqual(result, {"ok": True, "valid": True})

    def test_packet_secret_token_inside_absolute_path_is_rejected(self):
        secret_root = "/private/tmp/sk-0123456789abcdef0123456789abcdef"
        packet = dict(
            _valid_packet(),
            inputs=[secret_root + "/index.html"],
            allowedFiles=[secret_root],
        )
        self.write_packet(packet)
        result = self.run_cli_json(
            "validate-packet", "--packet", self.packet_path, expect_code=2,
        )
        self.assertEqual(result, {"ok": False, "error": "invalid_packet"})

    def test_packet_write_permission_cannot_exceed_route(self):
        self.write_packet(_valid_packet())
        result = self.run_cli_json(
            "validate-packet",
            "--packet", self.packet_path,
            "--route-write-mode", "read-only",
            expect_code=2,
        )
        self.assertEqual(
            result, {"ok": False, "error": "write_permission_exceeds_route"},
        )

        self.write_packet(dict(_valid_packet(), writePermission="read-only"))
        result = self.run_cli_json(
            "validate-packet",
            "--packet", self.packet_path,
            "--route-write-mode", "workspace",
        )
        self.assertEqual(result, {"ok": True, "valid": True})

    def test_absolute_input_paths_must_be_inside_allowed_files(self):
        outside = dict(
            _valid_packet(),
            inputs=["/etc/passwd"],
            allowedFiles=["/Users/example/projects/registry"],
        )
        self.write_packet(outside)
        self.assertEqual(
            self.run_cli_json(
                "validate-packet", "--packet", self.packet_path, expect_code=2,
            ),
            {"ok": False, "error": "input_path_not_allowed"},
        )

        explicit_external = dict(
            _valid_packet(),
            inputs=["/tmp/reference/image.png"],
            allowedFiles=[
                "/Users/example/projects/registry",
                "/tmp/reference/image.png",
            ],
        )
        self.write_packet(explicit_external)
        self.assertEqual(
            self.run_cli_json("validate-packet", "--packet", self.packet_path),
            {"ok": True, "valid": True},
        )

    # ------------------------------------------------------- V2 job transport

    def prepare_job(self, task_name="deepseek_worker_job_0123456789ab", **kwargs):
        packet = kwargs.pop("packet", _valid_packet())
        expect_code = kwargs.pop("expect_code", 0)
        extra = kwargs.pop("extra", ())
        self.assertFalse(kwargs, "unexpected prepare_job kwargs")
        return self.run_cli_json(
            "prepare-job",
            "--task-name", task_name,
            "--route-id", "worker-a",
            "--capability", "code",
            "--packet", "-",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:10Z",
            *extra,
            expect_code=expect_code,
            input_text=json.dumps(packet),
        )

    def test_runtime_job_prepare_read_and_cleanup(self):
        task_name = "deepseek_worker_job_0123456789ab"
        prepared = self.prepare_job(task_name)
        self.assertEqual(prepared["ok"], True)
        self.assertEqual(prepared["prepared"], True)
        self.assertEqual(prepared["taskName"], task_name)
        self.assertEqual(prepared["route"], {
            "id": "worker-a",
            "target": "deepseek_worker",
            "transport": "runtime-job",
        })
        job_path = os.path.join(self.jobs_dir, task_name + ".json")
        self.assertEqual(stat.S_IMODE(os.stat(job_path).st_mode), 0o600)

        read = self.run_cli_json(
            "read-job",
            "--task-name", task_name,
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:11Z",
        )
        self.assertEqual(read["ok"], True)
        self.assertEqual(read["taskName"], task_name)
        self.assertEqual(read["route"], {
            "id": "worker-a",
            "target": "deepseek_worker",
        })
        self.assertEqual(read["packet"], _valid_packet())
        self.assertNotIn("configFingerprint", json.dumps(read))

        cleaned = self.run_cli_json(
            "cleanup-job",
            "--task-name", task_name,
            "--jobs-dir", self.jobs_dir,
        )
        self.assertEqual(
            cleaned,
            {"ok": True, "taskName": task_name, "removed": True},
        )
        self.assertFalse(os.path.exists(job_path))

    def test_runtime_job_can_be_claimed_without_a_task_name(self):
        task_name = "deepseek_worker_claim_0123456789ab"
        self.prepare_job(task_name)

        claimed = self.run_cli_json(
            "claim-job",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:11Z",
            thread_id=CHILD_THREAD_ID,
        )
        self.assertEqual(claimed["ok"], True)
        self.assertEqual(claimed["taskName"], task_name)
        self.assertEqual(claimed["route"], {
            "id": "worker-a",
            "target": "deepseek_worker",
        })
        self.assertEqual(claimed["packet"], _valid_packet())
        self.assertNotIn("configFingerprint", json.dumps(claimed))
        self.assertNotIn("ThreadHash", json.dumps(claimed))

    def test_runtime_job_claim_and_cleanup_enforce_thread_ownership(self):
        task_name = "deepseek_worker_owned_0123456789ab"
        self.prepare_job(task_name)

        claimed = self.run_cli_json(
            "claim-job",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:11Z",
            thread_id=CHILD_THREAD_ID,
        )
        self.assertEqual(claimed["taskName"], task_name)

        replay = self.run_cli_json(
            "claim-job",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:12Z",
            thread_id=OTHER_THREAD_ID,
            expect_code=2,
        )
        self.assertEqual(
            replay,
            {"ok": False, "error": "job_already_claimed"},
        )

        wrong_cleanup = self.run_cli_json(
            "cleanup-job",
            "--task-name", task_name,
            "--jobs-dir", self.jobs_dir,
            thread_id=OTHER_THREAD_ID,
            expect_code=2,
        )
        self.assertEqual(
            wrong_cleanup,
            {"ok": False, "error": "job_owner_mismatch"},
        )

        cleaned = self.run_cli_json(
            "cleanup-job",
            "--task-name", task_name,
            "--jobs-dir", self.jobs_dir,
            thread_id=PARENT_THREAD_ID,
        )
        self.assertEqual(cleaned["removed"], True)

    def test_runtime_job_commands_require_codex_thread_identity(self):
        missing_identity = self.run_cli_json(
            "prepare-job",
            "--task-name", "deepseek_worker_noid_0123456789ab",
            "--route-id", "worker-a",
            "--capability", "code",
            "--packet", "-",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:10Z",
            input_text=json.dumps(_valid_packet()),
            thread_id=None,
            expect_code=2,
        )
        self.assertEqual(
            missing_identity,
            {"ok": False, "error": "job_identity_unavailable"},
        )

    def test_runtime_job_claim_fails_closed_for_zero_or_multiple_jobs(self):
        missing = self.run_cli_json(
            "claim-job",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:11Z",
            expect_code=2,
        )
        self.assertEqual(missing, {"ok": False, "error": "job_missing"})

        first_name = "deepseek_worker_first_0123456789ab"
        second_name = "deepseek_worker_second_abcdef012345"
        self.prepare_job(first_name)
        first_path = os.path.join(self.jobs_dir, first_name + ".json")
        with open(first_path, "r", encoding="utf-8") as handle:
            second_payload = json.load(handle)
        second_payload["taskName"] = second_name
        second_path = os.path.join(self.jobs_dir, second_name + ".json")
        with open(second_path, "w", encoding="utf-8") as handle:
            json.dump(second_payload, handle)
        os.chmod(second_path, 0o600)

        ambiguous = self.run_cli_json(
            "claim-job",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:11Z",
            expect_code=2,
        )
        self.assertEqual(
            ambiguous,
            {"ok": False, "error": "job_queue_ambiguous"},
        )

    def test_runtime_job_queue_allows_only_one_live_job(self):
        first_name = "deepseek_worker_first_0123456789ab"
        second_name = "deepseek_worker_second_abcdef012345"
        self.prepare_job(first_name)
        blocked = self.prepare_job(second_name, expect_code=2)
        self.assertEqual(blocked, {"ok": False, "error": "job_queue_busy"})

    def test_runtime_job_queue_recovers_corrupt_and_invalid_named_artifacts(self):
        os.makedirs(self.jobs_dir, mode=0o700)
        corrupt_path = os.path.join(
            self.jobs_dir,
            "deepseek_worker_corrupt_0123456789ab.json",
        )
        with open(corrupt_path, "w", encoding="utf-8") as handle:
            handle.write("{not-json")
        os.chmod(corrupt_path, 0o600)
        invalid_name_path = os.path.join(self.jobs_dir, "INVALID.json")
        with open(invalid_name_path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        os.chmod(invalid_name_path, 0o600)

        prepared = self.prepare_job("deepseek_worker_clean_abcdef012345")
        self.assertEqual(prepared["ok"], True)
        self.assertEqual(prepared["purgedInvalid"], 2)
        self.assertFalse(os.path.exists(corrupt_path))
        self.assertFalse(os.path.exists(invalid_name_path))
        leftovers = [
            name for name in os.listdir(self.jobs_dir)
            if name.endswith(".tmp")
        ]
        self.assertEqual(leftovers, [])

    def test_default_runtime_jobs_use_private_system_temp_not_codex_home(self):
        task_name = "deepseek_worker_default_0123456789ab"
        packet_hex = json.dumps(_valid_packet()).encode("utf-8").hex()
        proc = self.run_cli(
            "prepare-job",
            "--task-name", task_name,
            "--route-id", "worker-a",
            "--capability", "code",
            "--packet-hex", packet_hex,
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--now", "2026-08-19T00:00:10Z",
            home=self.tmp,
        )
        self.assertEqual(proc.returncode, 0, proc.stdout)
        default_jobs = os.path.join(
            os.path.realpath(self.runtime_tmp),
            "codex-project-agent-runtime-jobs-%d" % os.getuid(),
        )
        job_path = os.path.join(default_jobs, task_name + ".json")
        self.assertTrue(os.path.isfile(job_path))
        self.assertEqual(stat.S_IMODE(os.stat(default_jobs).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(job_path).st_mode), 0o600)
        self.assertFalse(
            os.path.exists(
                os.path.join(
                    self.tmp,
                    ".codex",
                    "project-agent-runtime",
                    "jobs",
                    task_name + ".json",
                )
            )
        )

    def test_runtime_job_can_prepare_from_packet_hex(self):
        task_name = "deepseek_worker_hex_0123456789ab"
        packet_hex = json.dumps(
            _valid_packet(), ensure_ascii=False
        ).encode("utf-8").hex()
        prepared = self.run_cli_json(
            "prepare-job",
            "--task-name", task_name,
            "--route-id", "worker-a",
            "--capability", "code",
            "--packet-hex", packet_hex,
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:10Z",
        )
        self.assertEqual(prepared["ok"], True)
        read = self.run_cli_json(
            "read-job",
            "--task-name", task_name,
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:11Z",
        )
        self.assertEqual(read["packet"], _valid_packet())
        self.assertNotIn("configFingerprint", json.dumps(read))
        job_path = os.path.join(self.jobs_dir, task_name + ".json")

        cleaned = self.run_cli_json(
            "cleanup-job",
            "--task-name", task_name,
            "--jobs-dir", self.jobs_dir,
        )
        self.assertEqual(
            cleaned,
            {"ok": True, "taskName": task_name, "removed": True},
        )
        self.assertFalse(os.path.exists(job_path))

    def test_runtime_job_requires_one_packet_source_before_writing(self):
        result = self.run_cli_json(
            "prepare-job",
            "--task-name", "deepseek_worker_none_0123456789ab",
            "--route-id", "worker-a",
            "--capability", "code",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:10Z",
            expect_code=2,
        )
        self.assertEqual(result, {"ok": False, "error": "usage_error"})
        self.assertFalse(os.path.exists(self.jobs_dir))

    def test_runtime_job_rejects_expiry_replay_and_context_change(self):
        task_name = "deepseek_worker_job_expiry_0123456789ab"
        self.prepare_job(task_name, extra=("--ttl-seconds", "30"))
        expired = self.run_cli_json(
            "read-job",
            "--task-name", task_name,
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:41Z",
            expect_code=2,
        )
        self.assertEqual(expired, {"ok": False, "error": "job_expired"})
        replay = self.prepare_job(task_name, expect_code=2)
        self.assertEqual(replay, {"ok": False, "error": "job_exists"})
        self.run_cli_json(
            "cleanup-job",
            "--task-name", task_name,
            "--jobs-dir", self.jobs_dir,
        )

        drift_name = "deepseek_worker_job_drift_0123456789ab"
        self.prepare_job(drift_name)
        self.write_routes(_routes(_route(fingerprint=FP_OLD)))
        changed = self.run_cli_json(
            "read-job",
            "--task-name", drift_name,
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:11Z",
            expect_code=2,
        )
        self.assertEqual(changed, {"ok": False, "error": "job_context_changed"})

    def test_runtime_job_requires_matching_route_and_task_name(self):
        bad_name = self.prepare_job("other_job_0123456789ab", expect_code=2)
        self.assertEqual(bad_name, {"ok": False, "error": "invalid_task_name"})
        weak_name = self.prepare_job("deepseek_worker_job_short", expect_code=2)
        self.assertEqual(weak_name, {"ok": False, "error": "invalid_task_name"})

        self.write_routes(_routes(_route(transport="message")))
        unsupported = self.prepare_job(
            "deepseek_worker_job_message_0123456789ab", expect_code=2
        )
        self.assertEqual(
            unsupported,
            {"ok": False, "error": "job_transport_unsupported"},
        )

    def test_runtime_job_rejects_unsafe_job_directory(self):
        target = os.path.join(self.tmp, "real-jobs")
        os.makedirs(target)
        os.symlink(target, self.jobs_dir)
        result = self.prepare_job(expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "jobs_dir_unsafe"})

        real_parent = os.path.join(self.tmp, "real-parent")
        linked_parent = os.path.join(self.tmp, "linked-parent")
        os.makedirs(real_parent, mode=0o700)
        os.symlink(real_parent, linked_parent)
        self.jobs_dir = os.path.join(linked_parent, "jobs")
        result = self.prepare_job(expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "jobs_dir_unsafe"})

    def test_runtime_job_directory_must_be_private(self):
        os.makedirs(self.jobs_dir)
        os.chmod(self.jobs_dir, 0o755)
        result = self.prepare_job(expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "jobs_dir_unsafe"})

    def test_runtime_job_file_must_remain_private_and_regular(self):
        task_name = "deepseek_worker_job_mode_0123456789ab"
        self.prepare_job(task_name)
        job_path = os.path.join(self.jobs_dir, task_name + ".json")
        os.chmod(job_path, 0o644)
        unsafe = self.run_cli_json(
            "read-job",
            "--task-name", task_name,
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:11Z",
            expect_code=2,
        )
        self.assertEqual(unsafe, {"ok": False, "error": "job_unsafe"})

    def test_expired_jobs_are_reclaimed_on_next_prepare(self):
        task_name = "deepseek_worker_job_gc_0123456789ab"
        self.prepare_job(task_name, extra=("--ttl-seconds", "30"))
        refreshed = self.run_cli_json(
            "prepare-job",
            "--task-name", task_name,
            "--route-id", "worker-a",
            "--capability", "code",
            "--packet", "-",
            "--routes", self.routes_path,
            "--evidence", self.evidence_path,
            "--jobs-dir", self.jobs_dir,
            "--now", "2026-08-19T00:00:41Z",
            input_text=json.dumps(_valid_packet()),
        )
        self.assertEqual(refreshed["ok"], True)
        self.assertEqual(refreshed["purgedExpired"], 1)

    # ------------------------------------------------------------ CLI hygiene

    def test_usage_error_for_bad_arguments(self):
        result = self.run_cli_json("resolve", expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "usage_error"})
        result = self.run_cli_json("bogus-command", expect_code=2)
        self.assertEqual(result, {"ok": False, "error": "usage_error"})

    def test_python39_syntax(self):
        with open(CLI_PATH, "r", encoding="utf-8") as fh:
            source = fh.read()
        ast.parse(source, filename=CLI_PATH, feature_version=(3, 9))


if __name__ == "__main__":
    unittest.main()
