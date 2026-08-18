#!/usr/bin/env python3
"""Validate configured routes and build a secret-free runtime allowlist."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlsplit


MODEL_TRANSPORTS = {"responses-direct", "responses-adapter-dedicated"}
TRANSPORTS = MODEL_TRANSPORTS | {"mcp-tool"}
ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
NAME_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+$")


class RuntimeContractError(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _require_text(value: Any, code: str, maximum: int = 240) -> str:
    if not isinstance(value, str):
        raise RuntimeContractError(code)
    text = value.strip()
    if not text or len(text) > maximum or any(ord(char) < 32 for char in text):
        raise RuntimeContractError(code)
    return text


def _read_regular(path: Path, code: str, maximum: int = 1_000_000) -> bytes:
    try:
        if path.is_symlink() or not path.resolve(strict=True).is_file():
            raise RuntimeContractError(code)
        data = path.read_bytes()
    except RuntimeContractError:
        raise
    except OSError as exc:
        raise RuntimeContractError(code) from exc
    if len(data) > maximum:
        raise RuntimeContractError(code)
    return data


def _load_json(path: Path, code: str) -> dict[str, Any]:
    try:
        value = json.loads(_read_regular(path, code).decode("utf-8"))
    except RuntimeContractError:
        raise
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeContractError(code) from exc
    if not isinstance(value, dict):
        raise RuntimeContractError(code)
    return value


def _decode_toml_text(path: Path, code: str) -> tuple[bytes, str]:
    raw = _read_regular(path, code)
    try:
        return raw, raw.decode("utf-8")
    except UnicodeError as exc:
        raise RuntimeContractError(code) from exc


def _simple_assignments(text: str, section_name: str) -> dict[str, str]:
    section = ""
    result: dict[str, str] = {}
    assignment = re.compile(r"^([A-Za-z0-9_.-]+)\s*=\s*(['\"])(.*?)\2\s*$")
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip()
            continue
        if section == section_name:
            match = assignment.fullmatch(line)
            if match:
                key = match.group(1)
                if key in result:
                    raise RuntimeContractError("route_config_invalid")
                result[key] = match.group(3)
    return result


def _matching_table_text(text: str, table_prefix: str) -> str:
    chunks: list[str] = []
    active = False
    for raw in text.splitlines(keepends=True):
        stripped = raw.split("#", 1)[0].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            table = stripped.strip("[]").strip()
            active = table == table_prefix or table.startswith(table_prefix + ".")
        if active:
            chunks.append(raw)
    if not chunks:
        raise RuntimeContractError("route_config_invalid")
    return "".join(chunks)


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _validate_base_url(value: Any) -> None:
    url = _require_text(value, "route_config_invalid", 2048)
    parsed = urlsplit(url)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme not in {"http", "https"}
        or not host
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeContractError("route_config_invalid")
    if parsed.scheme == "http" and host not in {"127.0.0.1", "localhost", "::1"}:
        raise RuntimeContractError("route_config_invalid")


def load_provider_registry(path: Path) -> list[dict[str, Any]]:
    data = _load_json(path, "providers_registry_unreadable")
    if set(data) != {"version", "providers"} or data.get("version") != 2:
        raise RuntimeContractError("providers_registry_schema_invalid")
    providers = data.get("providers")
    if not isinstance(providers, list):
        raise RuntimeContractError("providers_registry_schema_invalid")

    seen: set[str] = set()
    validated: list[dict[str, Any]] = []
    common = {"providerId", "label", "transport", "enabled", "notes"}
    for item in providers:
        if not isinstance(item, dict):
            raise RuntimeContractError("providers_registry_schema_invalid")
        provider_id = _require_text(item.get("providerId"), "provider_id_invalid", 80)
        if not ID_PATTERN.fullmatch(provider_id) or provider_id in seen:
            raise RuntimeContractError("provider_id_invalid")
        seen.add(provider_id)
        _require_text(item.get("label"), "provider_label_invalid", 120)
        _require_text(item.get("notes"), "provider_notes_invalid", 600)
        transport = _require_text(item.get("transport"), "provider_transport_invalid", 64)
        if transport not in TRANSPORTS or not isinstance(item.get("enabled"), bool):
            raise RuntimeContractError("providers_registry_schema_invalid")
        if transport in MODEL_TRANSPORTS:
            if set(item) != common | {"agentType"}:
                raise RuntimeContractError("providers_registry_schema_invalid")
            agent_type = _require_text(item.get("agentType"), "provider_agent_invalid", 120)
            if not NAME_PATTERN.fullmatch(agent_type):
                raise RuntimeContractError("provider_agent_invalid")
        else:
            if set(item) != common | {"mcpServer", "toolName", "readOnly"}:
                raise RuntimeContractError("providers_registry_schema_invalid")
            server = _require_text(item.get("mcpServer"), "provider_tool_invalid", 160)
            tool = _require_text(item.get("toolName"), "provider_tool_invalid", 160)
            if (
                not NAME_PATTERN.fullmatch(server)
                or not NAME_PATTERN.fullmatch(tool)
                or item.get("readOnly") is not True
            ):
                raise RuntimeContractError("provider_tool_invalid")
        validated.append(item)
    return validated


def load_smoke_evidence(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    data = _load_json(path, "smoke_evidence_unreadable")
    if set(data) != {"version", "evidence"} or data.get("version") != 1:
        raise RuntimeContractError("smoke_evidence_schema_invalid")
    items = data.get("evidence")
    if not isinstance(items, list):
        raise RuntimeContractError("smoke_evidence_schema_invalid")
    result: dict[str, dict[str, Any]] = {}
    expected = {
        "providerId",
        "status",
        "deliveryKind",
        "configFingerprint",
        "testedAt",
    }
    for item in items:
        if not isinstance(item, dict) or set(item) != expected:
            raise RuntimeContractError("smoke_evidence_schema_invalid")
        provider_id = _require_text(item.get("providerId"), "smoke_evidence_schema_invalid", 80)
        if provider_id in result:
            raise RuntimeContractError("smoke_evidence_duplicate")
        if item.get("status") != "passed" or item.get("deliveryKind") not in {
            "v1-child",
            "mcp-tool",
        }:
            raise RuntimeContractError("smoke_evidence_schema_invalid")
        fingerprint = _require_text(
            item.get("configFingerprint"), "smoke_evidence_schema_invalid", 80
        )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is None:
            raise RuntimeContractError("smoke_evidence_schema_invalid")
        _require_text(item.get("testedAt"), "smoke_evidence_schema_invalid", 64)
        result[provider_id] = item
    return result


def agent_config_fingerprint(home: Path, agent_type: str) -> str:
    if not NAME_PATTERN.fullmatch(agent_type):
        raise RuntimeContractError("route_config_invalid")
    agents_dir = home / "agents"
    try:
        if agents_dir.is_symlink() or not agents_dir.resolve(strict=True).is_dir():
            raise RuntimeContractError("route_config_invalid")
    except RuntimeContractError:
        raise
    except OSError as exc:
        raise RuntimeContractError("route_config_invalid") from exc
    path = agents_dir / f"{agent_type}.toml"
    try:
        if path.resolve(strict=True).parent != agents_dir.resolve(strict=True):
            raise RuntimeContractError("route_config_invalid")
    except RuntimeContractError:
        raise
    except OSError as exc:
        raise RuntimeContractError("route_config_invalid") from exc
    raw, text = _decode_toml_text(path, "route_config_invalid")
    top = _simple_assignments(text, "")
    if top.get("name") != agent_type:
        raise RuntimeContractError("route_config_invalid")
    model = top.get("model")
    provider_name = top.get("model_provider")
    if not model or not provider_name or NAME_PATTERN.fullmatch(provider_name) is None:
        raise RuntimeContractError("route_config_invalid")
    provider = _simple_assignments(text, f"model_providers.{provider_name}")
    if provider.get("wire_api") != "responses":
        raise RuntimeContractError("route_config_invalid")
    _validate_base_url(provider.get("base_url"))
    return _sha256(raw)


def mcp_config_fingerprint(home: Path, server_name: str) -> str:
    if not NAME_PATTERN.fullmatch(server_name):
        raise RuntimeContractError("route_config_invalid")
    config_path = home / "config.toml"
    try:
        if config_path.resolve(strict=True).parent != home.resolve(strict=True):
            raise RuntimeContractError("route_config_invalid")
    except RuntimeContractError:
        raise
    except OSError as exc:
        raise RuntimeContractError("route_config_invalid") from exc
    _, text = _decode_toml_text(config_path, "route_config_invalid")
    selected = _matching_table_text(text, f"mcp_servers.{server_name}")
    return _sha256(selected.encode("utf-8"))


def build_runtime_allowlist(
    home: Path, providers_path: Path, evidence_path: Path
) -> dict[str, list[dict[str, Any]]]:
    providers = load_provider_registry(providers_path)
    evidence = load_smoke_evidence(evidence_path)
    allowed: list[dict[str, Any]] = []
    rejected: list[dict[str, str]] = []

    for item in sorted(providers, key=lambda value: value["providerId"]):
        provider_id = item["providerId"]
        if item["enabled"] is not True:
            rejected.append({"providerId": provider_id, "reason": "route_disabled"})
            continue
        proof = evidence.get(provider_id)
        if proof is None:
            rejected.append({"providerId": provider_id, "reason": "local_smoke_required"})
            continue
        delivery = "v1-child" if item["transport"] in MODEL_TRANSPORTS else "mcp-tool"
        if proof["deliveryKind"] != delivery:
            rejected.append({"providerId": provider_id, "reason": "smoke_kind_mismatch"})
            continue
        try:
            if delivery == "v1-child":
                fingerprint = agent_config_fingerprint(home, item["agentType"])
            else:
                fingerprint = mcp_config_fingerprint(home, item["mcpServer"])
        except RuntimeContractError:
            rejected.append({"providerId": provider_id, "reason": "route_config_invalid"})
            continue
        if proof["configFingerprint"] != fingerprint:
            rejected.append(
                {"providerId": provider_id, "reason": "config_fingerprint_mismatch"}
            )
            continue
        if delivery == "v1-child":
            allowed.append(
                {
                    "providerId": provider_id,
                    "transport": item["transport"],
                    "delivery": delivery,
                    "agentType": item["agentType"],
                }
            )
        else:
            allowed.append(
                {
                    "providerId": provider_id,
                    "transport": "mcp-tool",
                    "delivery": delivery,
                    "mcpServer": item["mcpServer"],
                    "toolName": item["toolName"],
                    "readOnly": True,
                }
            )
    return {"allowed": allowed, "rejected": rejected}


def developer_instructions(allowed: list[dict[str, Any]]) -> str:
    manifest = json.dumps(allowed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return (
        "Codex External Subagent Runtime policy. The following JSON is the complete "
        f"external-route allowlist for this task: {manifest}. Use only these routes. "
        "A v1-child route names an existing agentType; an mcp-tool route names one "
        "bounded tool and is not a child model. Do not infer or add providers, change "
        "global or provider configuration, switch a shared proxy, expose credentials, "
        "or silently fall back. If no suitable allowlisted route exists, stop and say so."
    )
