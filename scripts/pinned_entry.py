#!/usr/bin/env python3
"""Deterministic pinned-entry gate for the exact lowercase ``new`` command."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


READY = "ENTRY_READY"
LAUNCH_PHRASE = "new"
REJECTED = "ONLY_ACCEPTS_NEW"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--ready", action="store_true")
    group.add_argument("--message")
    parser.add_argument("--project-id")
    parser.add_argument("--cwd")
    parser.add_argument("--projects-registry")
    parser.add_argument("--providers-registry")
    parser.add_argument("--smoke-evidence")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.ready:
        print(READY)
        return 0
    if args.message.strip() != LAUNCH_PHRASE:
        print(REJECTED)
        return 0
    if not args.project_id or not args.cwd:
        print('{"ok":false,"error":"pinned_binding_missing"}', file=sys.stderr)
        return 1

    launcher = Path(__file__).with_name("launch_bridge.py").resolve(strict=True)
    command = [
        sys.executable,
        str(launcher),
        "--project-id",
        args.project_id,
        "--cwd",
        args.cwd,
    ]
    optional = (
        ("--projects-registry", args.projects_registry),
        ("--providers-registry", args.providers_registry),
        ("--smoke-evidence", args.smoke_evidence),
    )
    for flag, value in optional:
        if value:
            command.extend((flag, value))
    os.execv(sys.executable, command)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
