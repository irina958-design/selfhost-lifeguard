#!/usr/bin/env python3
"""Command runner for the Selfhost Lifeguard GitHub Action.

Maps an Action command to a lifeguard.py invocation:

    preflight         default; no writes, no Docker
    backup
    verify-restore
    rehearse-upgrade
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def build_command(command: str, directory: str, backup: str, version: str) -> list[str] | str:
    """Map an Action command to a lifeguard argv, or return an error message."""
    lifeguard = Path(__file__).with_name("lifeguard.py")
    cmd = [sys.executable, str(lifeguard), directory]
    if command == "preflight":
        return cmd
    if command == "backup":
        return cmd + ["--backup"]
    if command == "verify-restore":
        if not backup:
            return "verify-restore requires 'backup:' (path to a .sql.gz backup)."
        return cmd + ["--verify-restore", backup]
    if command == "rehearse-upgrade":
        if not version:
            return "rehearse-upgrade requires 'version:' (target X.Y.Z)."
        return cmd + ["--rehearse-upgrade", version]
    return f"Unknown command {command!r}. Use preflight, backup, verify-restore or rehearse-upgrade."


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True)
    parser.add_argument("--command", default="preflight")
    parser.add_argument("--backup", default="")
    parser.add_argument("--version", default="")
    args = parser.parse_args()

    command = args.command.strip() or "preflight"
    result = build_command(command, args.directory, args.backup, args.version)
    if isinstance(result, str):
        print(f"FAIL  {result}")
        return 2
    return subprocess.call(result)


if __name__ == "__main__":
    raise SystemExit(main())
