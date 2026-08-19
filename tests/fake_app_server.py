#!/usr/bin/env python3
"""Small JSONL app-server double used only by runtime integration tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import time


THREAD_ID = "019c1234-5678-7abc-8def-0123456789ab"


def emit(value: dict) -> None:
    print(json.dumps(value, separators=(",", ":")), flush=True)


def log(value: dict) -> None:
    raw = os.environ.get("FAKE_APP_LOG")
    if raw:
        with Path(raw).open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(value, separators=(",", ":")) + "\n")


def main() -> int:
    if sys.argv[1:] == ["--version"]:
        print("codex-cli 9.9.9-test")
        return 0
    if sys.argv[1:] != ["app-server", "--stdio"]:
        return 2

    mode = os.environ.get("FAKE_APP_MODE", "ok")
    cwd = ""
    experimental_api = False
    for raw in sys.stdin:
        message = json.loads(raw)
        log(message)
        request_id = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if request_id is None:
            continue
        if method == "initialize":
            capabilities = params.get("capabilities")
            experimental_api = (
                isinstance(capabilities, dict)
                and capabilities.get("experimentalApi") is True
            )
            emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
        elif method == "model/list":
            models = [
                {
                    "id": "luna",
                    "model": "gpt-5.6-luna",
                    "supportedReasoningEfforts": [
                        {"reasoningEffort": "low", "description": "Low"}
                    ],
                }
            ]
            if mode != "missing_sol":
                models.append(
                    {
                        "id": "sol",
                        "model": "gpt-5.6-sol",
                        "supportedReasoningEfforts": [
                            {"reasoningEffort": "ultra", "description": "Ultra"}
                        ],
                    }
                )
            emit(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"data": models, "nextCursor": None},
                }
            )
        elif method == "thread/start":
            cwd = params.get("cwd", "")
            emit(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "thread": {"id": THREAD_ID, "cwd": cwd, "turns": []},
                        "cwd": cwd,
                        "model": params.get("model"),
                        "modelProvider": "openai",
                        "sandbox": {"type": "workspaceWrite"},
                        "approvalPolicy": "on-request",
                    },
                }
            )
        elif method == "thread/settings/update":
            if mode == "experimental_api_error" or not experimental_api:
                emit(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32600,
                            "message": "thread/settings/update requires experimentalApi capability",
                        },
                    }
                )
                continue
            if mode == "method_not_found":
                emit(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32601, "message": "method unavailable"},
                    }
                )
                continue
            if mode == "invalid_params":
                emit(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32602, "message": "invalid parameters"},
                    }
                )
                continue
            if mode == "settings_error":
                emit(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {
                            "code": -32000,
                            "message": "rejected",
                            "data": {"secret": "must-not-leak"},
                        },
                    }
                )
                continue
            if mode == "settings_timeout":
                time.sleep(2)
                continue
            emit({"jsonrpc": "2.0", "id": request_id, "result": {}})
            emit(
                {
                    "jsonrpc": "2.0",
                    "method": "thread/settings/updated",
                    "params": {
                        "threadId": THREAD_ID,
                        "threadSettings": {
                            "model": params.get("model"),
                            "effort": params.get("effort"),
                            "cwd": cwd,
                        },
                    },
                }
            )
        elif method == "thread/read":
            emit(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "thread": {"id": THREAD_ID, "cwd": cwd, "turns": []}
                    },
                }
            )
        else:
            emit(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": "unknown method"},
                }
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
