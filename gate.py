#!/usr/bin/env python3
"""Free/Pro gate for the Selfhost Lifeguard GitHub Action.

Free commands run unconditionally. Pro commands require a valid license key,
validated online against a merchant-of-record (Lemon Squeezy by default).

    preflight         free   (default; no writes, no Docker)
    backup            free
    verify-restore    pro
    rehearse-upgrade  pro
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PRO = {"verify-restore", "rehearse-upgrade"}
BUY_URL = "https://selfhost-lifeguard.lemonsqueezy.com"
VALIDATE_URL = os.environ.get(
    "LIFEGUARD_LICENSE_URL", "https://api.lemonsqueezy.com/v1/licenses/validate"
)


def license_valid(key: str) -> bool:
    """Return True only if the merchant confirms the key is active. Fails closed.

    ponytail: online validation, needs network at run time. Upgrade path when
    offline runners matter: ship an Ed25519-signed key and verify it against a
    bundled public key instead of calling the merchant.
    """
    if not key:
        return False
    body = urllib.parse.urlencode({"license_key": key}).encode()
    request = urllib.request.Request(VALIDATE_URL, data=body, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.load(response)
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return False
    return bool(payload.get("valid"))


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

    if command in PRO and not license_valid(os.environ.get("LIFEGUARD_LICENSE_KEY", "")):
        print(f"FAIL  {command!r} is a Pro command. A valid license key is required: {BUY_URL}")
        return 3

    result = build_command(command, args.directory, args.backup, args.version)
    if isinstance(result, str):
        print(f"FAIL  {result}")
        return 2
    return subprocess.call(result)


if __name__ == "__main__":
    raise SystemExit(main())
