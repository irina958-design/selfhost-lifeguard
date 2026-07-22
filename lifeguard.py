#!/usr/bin/env python3
"""Read-only preflight checks for a Docker Compose Immich installation."""

from __future__ import annotations

import argparse
import gzip
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str


class BackupError(RuntimeError):
    pass


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


def custom_backup_is_mounted(compose_text: str) -> bool:
    return any(
        not line.lstrip().startswith("#")
        and "${BACKUP_LOCATION}" in line
        and re.search(r":\s*/data/backups(?:[:\s'\"]|$)", line)
        for line in compose_text.splitlines()
    )


def resolve_backup_path(root: Path, compose: Path | None, env: dict[str, str]) -> tuple[Path | None, Finding | None]:
    upload = env.get("UPLOAD_LOCATION")
    upload_path = host_path(root, upload) if upload else None
    backup_path = upload_path / "backups" if upload_path else None
    custom_backup = env.get("BACKUP_LOCATION")
    if not custom_backup:
        return backup_path, None

    compose_text = compose.read_text(encoding="utf-8") if compose else ""
    if not custom_backup_is_mounted(compose_text):
        return None, Finding("INFO", "backup.unverified", "BACKUP_LOCATION is set, but its /data/backups Compose mount could not be verified.")

    backup_path = host_path(root, custom_backup)
    if backup_path is None:
        return None, Finding("INFO", "backup.unverified", "BACKUP_LOCATION looks like a named Docker volume; backup check skipped.")
    return backup_path, None


def build_backup_command(env: dict[str, str]) -> list[str]:
    return [
        "docker",
        "exec",
        "-i",
        "immich_postgres",
        "pg_dump",
        "--clean",
        "--if-exists",
        f"--dbname={env.get('DB_DATABASE_NAME', 'immich')}",
        f"--username={env.get('DB_USERNAME', 'postgres')}",
    ]


def create_database_backup(root: Path) -> Path:
    compose = next((root / name for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") if (root / name).is_file()), None)
    env_file = root / ".env"
    if compose is None or not env_file.is_file():
        raise BackupError("A Docker Compose file and .env are required before creating a backup.")

    env = read_env(env_file)
    backup_path, location_finding = resolve_backup_path(root, compose, env)
    if backup_path is None:
        raise BackupError(location_finding.message if location_finding else "Backup location could not be verified.")
    if not backup_path.is_dir():
        raise BackupError(f"Backup directory does not exist: {backup_path.resolve()}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    final_path = backup_path / f"immich-db-{stamp}-{secrets.token_hex(4)}.sql.gz"
    with tempfile.NamedTemporaryFile(dir=backup_path, prefix=".lifeguard-", suffix=".tmp", delete=False) as temporary:
        temporary_path = Path(temporary.name)

    try:
        with tempfile.TemporaryFile() as stderr:
            try:
                process = subprocess.Popen(build_backup_command(env), stdout=subprocess.PIPE, stderr=stderr)
            except FileNotFoundError as error:
                raise BackupError("Docker was not found.") from error
            except (OSError, ValueError) as error:
                raise BackupError("The Docker backup process could not be started safely.") from error

            assert process.stdout is not None
            written = 0
            try:
                with gzip.open(temporary_path, "wb") as compressed:
                    while chunk := process.stdout.read(1024 * 1024):
                        compressed.write(chunk)
                        written += len(chunk)
            except Exception:
                process.kill()
                process.wait()
                raise
            finally:
                process.stdout.close()
            exit_code = process.wait()
            if exit_code != 0 or written == 0:
                stderr.seek(0)
                detail = stderr.read(1000).decode("utf-8", errors="replace").strip()
                message = f"pg_dump failed with exit code {exit_code}"
                raise BackupError(f"{message}: {detail}" if detail else message)

        try:
            os.link(temporary_path, final_path)
        except FileExistsError as error:
            raise BackupError("Refusing to overwrite an existing backup.") from error
        except OSError as error:
            raise BackupError("The backup filesystem cannot publish the file safely without overwrite risk.") from error
        temporary_path.unlink()
        return final_path
    finally:
        temporary_path.unlink(missing_ok=True)


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

    backup_path, location_finding = resolve_backup_path(root, compose, env)
    if location_finding:
        findings.append(location_finding)

    backups = list(backup_path.glob("*.sql*")) if backup_path and backup_path.is_dir() else []
    if backups:
        newest = max(backups, key=lambda path: path.stat().st_mtime)
        findings.append(Finding("PASS", "backup.found", f"Database backup found: {newest.name}"))
    elif backup_path and backup_path.is_dir():
        findings.append(Finding("WARN", "backup.missing", f"No database backup found in {backup_path.resolve()}."))
    elif backup_path:
        findings.append(Finding("WARN", "backup.missing", f"Backup directory does not exist: {backup_path.resolve()}"))

    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path, help="Directory containing Immich docker-compose.yml and .env")
    parser.add_argument("--backup", action="store_true", help="Create a new compressed PostgreSQL backup after preflight checks")
    args = parser.parse_args()

    root = args.directory.resolve()
    findings = inspect(root)
    for finding in findings:
        print(f"{finding.level:4}  {finding.message}")

    if any(item.level == "FAIL" for item in findings):
        return 2
    if args.backup:
        try:
            backup = create_database_backup(root)
        except BackupError as error:
            print(f"FAIL  {error}")
            return 2
        print(f"PASS  Database backup created: {backup}")
    if any(item.level == "WARN" for item in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
