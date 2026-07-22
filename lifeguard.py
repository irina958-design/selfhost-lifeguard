#!/usr/bin/env python3
"""Read-only preflight checks for a Docker Compose Immich installation."""

from __future__ import annotations

import argparse
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def host_path(root: Path, value: str) -> Path | None:
    expanded = Path(value).expanduser()
    if expanded.is_absolute() or value.startswith((".", "~")) or ":" in value:
        return expanded if expanded.is_absolute() else root / expanded
    return None  # A bare value is usually a named Docker volume.


def inspect(root: Path) -> list[Finding]:
    findings: list[Finding] = []
    compose = next((root / name for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") if (root / name).is_file()), None)
    env_file = root / ".env"

    if compose is None:
        findings.append(Finding("FAIL", "compose.missing", "No Docker Compose file found."))
    else:
        findings.append(Finding("PASS", "compose.found", f"Compose file: {compose.name}"))

    if not env_file.is_file():
        findings.append(Finding("FAIL", "env.missing", "No .env file found."))
        return findings

    env = read_env(env_file)
    for key in ("UPLOAD_LOCATION", "DB_DATA_LOCATION", "IMMICH_VERSION"):
        if not env.get(key):
            findings.append(Finding("FAIL", f"env.{key.lower()}", f"{key} is missing or empty."))

    version = env.get("IMMICH_VERSION", "")
    if version and not re.fullmatch(r"v?\d+\.\d+\.\d+", version):
        findings.append(Finding("WARN", "version.unpinned", f"IMMICH_VERSION={version!r} is not pinned to an exact release."))
    elif version:
        findings.append(Finding("PASS", "version.pinned", f"Immich version is pinned to {version}."))

    password = env.get("DB_PASSWORD", "")
    if password == "postgres":
        findings.append(Finding("WARN", "db.default-password", "DB_PASSWORD still uses the documented default."))

    for key in ("UPLOAD_LOCATION", "DB_DATA_LOCATION"):
        value = env.get(key)
        if not value:
            continue
        path = host_path(root, value)
        if path is None:
            findings.append(Finding("INFO", f"storage.{key.lower()}", f"{key}={value!r} looks like a named Docker volume; host checks skipped."))
        elif not path.exists():
            findings.append(Finding("FAIL", f"storage.{key.lower()}", f"{key} does not exist: {path.resolve()}"))
        else:
            free = shutil.disk_usage(path).free / (1024**3)
            findings.append(Finding("PASS", f"storage.{key.lower()}", f"{key} exists; {free:.1f} GiB free."))

    upload = env.get("UPLOAD_LOCATION")
    upload_path = host_path(root, upload) if upload else None
    backups = list((upload_path / "backups").glob("*.sql*")) if upload_path and (upload_path / "backups").is_dir() else []
    if backups:
        newest = max(backups, key=lambda path: path.stat().st_mtime)
        findings.append(Finding("PASS", "backup.found", f"Database backup found: {newest.name}"))
    elif upload_path and upload_path.exists():
        findings.append(Finding("WARN", "backup.missing", "No database backup found in UPLOAD_LOCATION/backups."))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing Immich docker-compose.yml and .env")
    args = parser.parse_args()

    findings = inspect(args.directory.resolve())
    for finding in findings:
        print(f"{finding.level:4}  {finding.message}")

    if any(item.level == "FAIL" for item in findings):
        return 2
    if any(item.level == "WARN" for item in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

