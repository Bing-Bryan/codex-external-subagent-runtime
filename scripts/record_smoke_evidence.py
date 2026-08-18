#!/usr/bin/env python3
"""Record a user-confirmed smoke result against the current config fingerprint."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import tempfile
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from runtime_contract import (
    RuntimeContractError,
    MODEL_TRANSPORTS,
    agent_config_fingerprint,
    load_provider_registry,
    load_smoke_evidence,
    mcp_config_fingerprint,
)


class EvidenceError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def codex_home() -> Path:
    raw = os.environ.get("CODEX_HOME")
    return Path(raw).expanduser().resolve() if raw else (Path.home() / ".codex").resolve()


def parse_args() -> argparse.Namespace:
    home = codex_home()
    runtime = home / "codex-external-subagent-runtime"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider-id", required=True)
    parser.add_argument("--providers", type=Path, default=runtime / "providers.json")
    parser.add_argument("--evidence", type=Path, default=runtime / "smoke-evidence.json")
    parser.add_argument("--confirm-observed-delivery", action="store_true")
    return parser.parse_args()


def run() -> dict[str, Any]:
    args = parse_args()
    if not args.confirm_observed_delivery:
        raise EvidenceError("smoke_delivery_not_confirmed")
    home = codex_home()
    try:
        providers = load_provider_registry(args.providers)
    except RuntimeContractError as exc:
        raise EvidenceError(exc.code) from exc
    matches = [item for item in providers if item["providerId"] == args.provider_id]
    if len(matches) != 1:
        raise EvidenceError("provider_not_found")
    provider = matches[0]
    try:
        if provider["transport"] in MODEL_TRANSPORTS:
            delivery = "v1-child"
            fingerprint = agent_config_fingerprint(home, provider["agentType"])
        else:
            delivery = "mcp-tool"
            fingerprint = mcp_config_fingerprint(home, provider["mcpServer"])
        existing = load_smoke_evidence(args.evidence)
    except RuntimeContractError as exc:
        raise EvidenceError(exc.code) from exc

    existing[args.provider_id] = {
        "providerId": args.provider_id,
        "status": "passed",
        "deliveryKind": delivery,
        "configFingerprint": fingerprint,
        "testedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }
    payload = {
        "version": 1,
        "evidence": [existing[key] for key in sorted(existing)],
    }
    target = args.evidence.expanduser()
    try:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.is_symlink():
            raise EvidenceError("smoke_evidence_target_invalid")
        fd, raw_temp = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
        temp = Path(raw_temp)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp, 0o600)
            os.replace(temp, target)
        finally:
            try:
                temp.unlink()
            except OSError:
                pass
    except EvidenceError:
        raise
    except OSError as exc:
        raise EvidenceError("smoke_evidence_write_failed") from exc
    return {
        "ok": True,
        "providerId": args.provider_id,
        "deliveryKind": delivery,
        "externalCallPerformed": False,
        "recorded": True,
    }


def main() -> int:
    try:
        result = run()
    except EvidenceError as exc:
        print(json.dumps({"ok": False, "error": exc.code}, separators=(",", ":")), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print('{"ok":false,"error":"interrupted"}', file=sys.stderr)
        return 130
    except Exception:
        print('{"ok":false,"error":"internal_error"}', file=sys.stderr)
        return 1
    print(json.dumps(result, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
