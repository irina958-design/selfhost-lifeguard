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
import hashlib
import json
import os
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

PRO = {"verify-restore", "rehearse-upgrade"}
BUY_URL = "https://selfhost-lifeguard.lemonsqueezy.com"
API = os.environ.get("LIFEGUARD_LICENSE_URL", "https://api.lemonsqueezy.com/v1/licenses")
STATE_FILE = Path(
    os.environ.get("LIFEGUARD_STATE", Path.home() / ".lifeguard" / "instances.json")
)


def install_id() -> str:
    """Opaque, stable name for this installation. Carries no readable detail."""
    seed = os.environ.get("GITHUB_REPOSITORY") or socket.gethostname()
    return "lifeguard-" + hashlib.sha256(seed.encode()).hexdigest()[:12]


def _post(endpoint: str, fields: dict[str, str]) -> dict:
    """POST to the license API. Returns {} on any transport or decoding failure."""
    body = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(
        f"{API}/{endpoint}", data=body, headers={"Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        try:
            return json.load(error)
        except (json.JSONDecodeError, OSError):
            return {}
    except (urllib.error.URLError, json.JSONDecodeError, OSError):
        return {}


def _instances() -> dict[str, str]:
    try:
        return json.loads(STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def _remember(fingerprint: str, instance_id: str) -> bool:
    instances = _instances()
    instances[fingerprint] = instance_id
    try:
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(instances, indent=2, sort_keys=True))
    except OSError:
        return False
    return True


def license_valid(key: str) -> bool:
    """Return True only if the merchant confirms the key is active. Fails closed.

    The first Pro run activates this installation under an opaque name; later
    runs validate that same activation, so one key covers a known number of
    installations instead of an unbounded number of runs.
    """
    if not key:
        return False
    fingerprint = hashlib.sha256(key.encode()).hexdigest()[:12]
    known = _instances().get(fingerprint)
    if known and _post("validate", {"license_key": key, "instance_id": known}).get("valid"):
        return True

    payload = _post("activate", {"license_key": key, "instance_name": install_id()})
    if not payload.get("activated"):
        error = payload.get("error")
        if error:
            print(f"      license: {error}")
        return False
    if not _remember(fingerprint, str(payload.get("instance", {}).get("id", ""))):
        print(
            f"      license: activated as {install_id()}, but {STATE_FILE} is not"
            " writable, so the next run activates again. Set LIFEGUARD_STATE to a"
            " writable path."
        )
    return True


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
        print(f"      installation: {install_id()}")
        return 3

    result = build_command(command, args.directory, args.backup, args.version)
    if isinstance(result, str):
        print(f"FAIL  {result}")
        return 2
    return subprocess.call(result)


if __name__ == "__main__":
    raise SystemExit(main())
