#!/usr/bin/env python3
"""Safety checks and recovery verification for a Docker Compose Immich installation."""

from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import secrets
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

__version__ = "0.3.4"


@dataclass(frozen=True)
class Finding:
    level: str
    code: str
    message: str


class BackupError(RuntimeError):
    pass


class RestoreError(RuntimeError):
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


def exact_version(value: str) -> tuple[int, int, int] | None:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", value)
    return tuple(map(int, match.groups())) if match else None


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


def build_backup_command(root: Path, compose: Path, env: dict[str, str]) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-directory",
        str(root),
        "-f",
        str(compose),
        "exec",
        "-T",
        "database",
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
    try:
        staging = tempfile.TemporaryDirectory(dir=backup_path, prefix=".lifeguard-")
    except OSError as error:
        raise BackupError("The backup could not be staged safely in the verified backup directory.") from error

    with staging as temporary:
        temporary_path = Path(temporary) / "backup.tmp"
        with tempfile.TemporaryFile() as stderr:
            try:
                process = subprocess.Popen(build_backup_command(root, compose, env), stdout=subprocess.PIPE, stderr=stderr)
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
            except OSError as error:
                process.kill()
                process.wait()
                raise BackupError("The backup could not be staged safely in the verified backup directory.") from error
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
        return final_path


def run_checked(command: list[str], cwd: Path, timeout: int) -> str:
    try:
        result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as error:
        raise RestoreError("Docker was not found.") from error
    except subprocess.TimeoutExpired as error:
        raise RestoreError(f"Timed out while running {command[0]} {command[1]}.") from error
    if result.returncode != 0:
        detail = result.stderr.strip()[-1000:]
        raise RestoreError(detail or f"Command failed with exit code {result.returncode}.")
    return result.stdout


def service_image(root: Path, compose: Path, service: str) -> str:
    output = run_checked(
        ["docker", "compose", "--project-directory", str(root), "-f", str(compose), "config", "--format", "json"],
        root,
        60,
    )
    try:
        image = json.loads(output)["services"][service]["image"]
    except (KeyError, TypeError, json.JSONDecodeError) as error:
        raise RestoreError(f"The Compose configuration has no {service} service image.") from error
    if not isinstance(image, str) or not image:
        raise RestoreError(f"The Compose {service} image is empty.")
    return image


def database_image(root: Path, compose: Path) -> str:
    return service_image(root, compose, "database")


def open_backup(backup: Path):
    if backup.suffix.lower() == ".gz":
        return gzip.open(backup, "rt", encoding="utf-8")
    return backup.open("rt", encoding="utf-8")


def restore_backup(compose_command: list[str], backup: Path, database: str, user: str, cwd: Path) -> None:
    with tempfile.TemporaryFile() as stderr:
        process = subprocess.Popen(
            [
                *compose_command,
                "exec",
                "-T",
                "database",
                "psql",
                f"--dbname={database}",
                f"--username={user}",
                "--single-transaction",
                "--set",
                "ON_ERROR_STOP=on",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=stderr,
            cwd=cwd,
        )
        assert process.stdin is not None
        try:
            with open_backup(backup) as sql:
                for line in sql:
                    line = line.replace(
                        "SELECT pg_catalog.set_config('search_path', '', false);",
                        "SELECT pg_catalog.set_config('search_path', 'public, pg_catalog', true);",
                    )
                    process.stdin.write(line.encode("utf-8"))
        except (BrokenPipeError, OSError, UnicodeError) as error:
            process.stdin.close()
            process.wait()
            raise RestoreError("The SQL backup could not be streamed into PostgreSQL.") from error
        process.stdin.close()
        exit_code = process.wait()
        if exit_code != 0:
            stderr.seek(0)
            detail = stderr.read(1000).decode("utf-8", errors="replace").strip()
            raise RestoreError(detail or f"Restore failed with exit code {exit_code}.")


def public_table_count(compose_command: list[str], database: str, user: str, cwd: Path) -> int:
    output = run_checked(
        [
            *compose_command,
            "exec",
            "-T",
            "database",
            "psql",
            f"--dbname={database}",
            f"--username={user}",
            "-Atc",
            "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = 'public'",
        ],
        cwd,
        60,
    )
    try:
        return int(output.strip())
    except ValueError as error:
        raise RestoreError("The restored database returned an invalid table count.") from error


def verify_database_restore(root: Path, backup: Path) -> tuple[str, str]:
    compose = next((root / name for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") if (root / name).is_file()), None)
    env_file = root / ".env"
    if compose is None or not env_file.is_file():
        raise RestoreError("A Docker Compose file and .env are required before verifying a restore.")
    backup = backup.resolve()
    if not backup.is_file():
        raise RestoreError(f"Backup file does not exist: {backup}")

    env = read_env(env_file)
    user = env.get("DB_USERNAME", "postgres")
    database = env.get("DB_DATABASE_NAME", "immich")
    image = database_image(root, compose)
    project = f"lifeguard-restore-{secrets.token_hex(4)}"
    restore_error: Exception | None = None
    cleanup_error: RestoreError | None = None

    with tempfile.TemporaryDirectory(prefix="lifeguard-restore-") as temporary:
        temporary_path = Path(temporary)
        restore_compose = temporary_path / "compose.json"
        restore_compose.write_text(
            json.dumps(
                {
                    "services": {
                        "database": {
                            "image": image,
                            "environment": {
                                "POSTGRES_USER": user,
                                "POSTGRES_PASSWORD": secrets.token_urlsafe(24),
                                "POSTGRES_DB": database,
                            },
                            "healthcheck": {
                                "test": ["CMD", "psql", "--username", user, "--dbname", database, "-c", "SELECT 1"],
                                "interval": "1s",
                                "timeout": "5s",
                                "retries": 60,
                            },
                            "volumes": ["restore-data:/var/lib/postgresql/data"],
                        }
                    },
                    "volumes": {"restore-data": {}},
                    "networks": {"default": {"internal": True}},
                }
            ),
            encoding="utf-8",
        )
        compose_command = ["docker", "compose", "-p", project, "-f", str(restore_compose)]

        try:
            run_checked([*compose_command, "up", "-d", "--wait", "database"], temporary_path, 300)
            restore_backup(compose_command, backup, database, user, temporary_path)
            restored_tables = public_table_count(compose_command, database, user, temporary_path)
            if restored_tables < 1:
                raise RestoreError("The restored database contains no user tables.")
        except Exception as error:
            restore_error = error
        finally:
            try:
                cleanup = subprocess.run(
                    [*compose_command, "down", "-v", "--remove-orphans"],
                    cwd=temporary_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if cleanup.returncode != 0:
                    cleanup_error = RestoreError(f"Disposable restore cleanup failed for project {project}: {cleanup.stderr.strip()[:500]}")
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_error = RestoreError(f"Disposable restore cleanup failed for project {project}: {error}")

    if cleanup_error:
        raise cleanup_error from restore_error
    if restore_error:
        raise restore_error
    return project, image


def upgrade_backup(root: Path, compose: Path | None, env: dict[str, str]) -> Path | None:
    backup_path, _ = resolve_backup_path(root, compose, env)
    backups = list(backup_path.glob("*.sql*")) if backup_path and backup_path.is_dir() else []
    return max(backups, key=lambda path: path.stat().st_mtime) if backups else None


def plan_upgrade(root: Path, target: str) -> list[Finding]:
    env_file = root / ".env"
    if not env_file.is_file():
        return []  # inspect() already reports the missing file.

    env = read_env(env_file)
    current = env.get("IMMICH_VERSION", "")
    current_version = exact_version(current)
    target_version = exact_version(target)
    if current_version is None:
        return [Finding("FAIL", "upgrade.current", "IMMICH_VERSION must be pinned to an exact release before planning an upgrade.")]
    if target_version is None:
        return [Finding("FAIL", "upgrade.target", f"Target version {target!r} is not an exact X.Y.Z version.")]
    if target_version <= current_version:
        return [Finding("FAIL", "upgrade.direction", f"Target {target} must be newer than current version {current}.")]
    if target_version[0] != current_version[0]:
        return [Finding("FAIL", "upgrade.major", "Major-version upgrades require manual review of Immich's breaking changes.")]

    compose = next((root / name for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") if (root / name).is_file()), None)
    newest = upgrade_backup(root, compose, env)
    if newest is None:
        return [Finding("FAIL", "upgrade.backup", "A database backup in the verified backup directory is required before planning an upgrade.")]

    return [
        Finding("PASS", "upgrade.target", f"Same-major upgrade direction validated: {current} -> {target}."),
        Finding("PASS", "upgrade.backup", f"Upgrade backup candidate: {newest.name}."),
        Finding("INFO", "upgrade.read-only", "Plan only: no images were pulled and no files or containers were changed."),
        Finding("INFO", "upgrade.rollback", "Immich does not support downgrades; verify a database backup before following the official upgrade instructions."),
    ]


def rehearse_upgrade(root: Path, target: str) -> tuple[str, str, str]:
    failures = [item for item in plan_upgrade(root, target) if item.level == "FAIL"]
    if failures:
        raise RestoreError(failures[0].message)

    compose = next((root / name for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml") if (root / name).is_file()), None)
    env_file = root / ".env"
    if compose is None or not env_file.is_file():
        raise RestoreError("A Docker Compose file and .env are required before rehearsing an upgrade.")
    env = read_env(env_file)
    backup = upgrade_backup(root, compose, env)
    if backup is None:
        raise RestoreError("A database backup is required before rehearsing an upgrade.")

    target_version = exact_version(target)
    assert target_version is not None
    target_tag = "v" + ".".join(map(str, target_version))
    server_image = f"ghcr.io/immich-app/immich-server:{target_tag}"
    database = env.get("DB_DATABASE_NAME", "immich")
    user = env.get("DB_USERNAME", "postgres")
    password = secrets.token_urlsafe(24)
    database_image_name = service_image(root, compose, "database")
    redis_image_name = service_image(root, compose, "redis")
    project = f"lifeguard-upgrade-{secrets.token_hex(4)}"
    rehearsal_error: Exception | None = None
    cleanup_error: RestoreError | None = None

    with tempfile.TemporaryDirectory(prefix="lifeguard-upgrade-") as temporary:
        temporary_path = Path(temporary)
        rehearsal_compose = temporary_path / "compose.json"
        rehearsal_compose.write_text(
            json.dumps(
                {
                    "services": {
                        "database": {
                            "image": database_image_name,
                            "environment": {
                                "POSTGRES_USER": user,
                                "POSTGRES_PASSWORD": password,
                                "POSTGRES_DB": database,
                                "POSTGRES_INITDB_ARGS": "--data-checksums",
                            },
                            "healthcheck": {
                                "test": ["CMD", "psql", "--username", user, "--dbname", database, "-c", "SELECT 1"],
                                "interval": "1s",
                                "timeout": "5s",
                                "retries": 120,
                            },
                            "volumes": ["rehearsal-db:/var/lib/postgresql/data"],
                        },
                        "redis": {
                            "image": redis_image_name,
                            "healthcheck": {
                                "test": ["CMD", "redis-cli", "ping"],
                                "interval": "1s",
                                "timeout": "5s",
                                "retries": 60,
                            },
                        },
                        "immich-server": {
                            "image": server_image,
                            "environment": {
                                "DB_HOSTNAME": "database",
                                "DB_USERNAME": user,
                                "DB_PASSWORD": password,
                                "DB_DATABASE_NAME": database,
                                "REDIS_HOSTNAME": "redis",
                                "IMMICH_WORKERS_INCLUDE": "api",
                            },
                            "depends_on": {
                                "database": {"condition": "service_healthy"},
                                "redis": {"condition": "service_healthy"},
                            },
                            "healthcheck": {
                                "test": ["CMD", "immich-healthcheck"],
                                "interval": "2s",
                                "timeout": "5s",
                                "retries": 150,
                                "start_period": "10s",
                            },
                            "volumes": ["rehearsal-upload:/data"],
                        },
                    },
                    "volumes": {"rehearsal-db": {}, "rehearsal-upload": {}},
                    "networks": {"default": {"internal": True}},
                }
            ),
            encoding="utf-8",
        )
        compose_command = ["docker", "compose", "-p", project, "-f", str(rehearsal_compose)]

        try:
            run_checked([*compose_command, "up", "-d", "--wait", "database", "redis"], temporary_path, 300)
            restore_backup(compose_command, backup, database, user, temporary_path)
            if public_table_count(compose_command, database, user, temporary_path) < 1:
                raise RestoreError("The restored database contains no user tables.")
            run_checked(
                [
                    *compose_command,
                    "run",
                    "--rm",
                    "--no-deps",
                    "--entrypoint",
                    "/bin/sh",
                    "immich-server",
                    "-c",
                    "for folder in upload library thumbs encoded-video profile backups; do mkdir -p /data/$folder; : > /data/$folder/.immich; done",
                ],
                temporary_path,
                300,
            )
            run_checked([*compose_command, "up", "-d", "--wait", "immich-server"], temporary_path, 600)
            running_version = run_checked(
                [*compose_command, "exec", "-T", "immich-server", "immich-admin", "version"], temporary_path, 60
            ).strip()
            if running_version != target_tag:
                raise RestoreError(f"The disposable server reported {running_version!r}, expected {target_tag}.")
            schema_report = run_checked(
                [*compose_command, "exec", "-T", "immich-server", "immich-admin", "schema-check"], temporary_path, 180
            )
            if "Migrations are up to date" not in schema_report or "No schema drift detected" not in schema_report:
                raise RestoreError("The target Immich server reported migration or schema drift problems.")
        except Exception as error:
            try:
                logs = subprocess.run(
                    [*compose_command, "logs", "--no-color", "--tail", "80", "immich-server"],
                    cwd=temporary_path,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                detail = logs.stdout.strip()[-3000:]
            except (OSError, subprocess.SubprocessError):
                detail = ""
            rehearsal_error = RestoreError(f"{error}\nTarget server logs:\n{detail}" if detail else str(error))
        finally:
            try:
                cleanup = subprocess.run(
                    [*compose_command, "down", "-v", "--remove-orphans"],
                    cwd=temporary_path,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                if cleanup.returncode != 0:
                    cleanup_error = RestoreError(f"Disposable upgrade cleanup failed for project {project}: {cleanup.stderr.strip()[:500]}")
            except (OSError, subprocess.SubprocessError) as error:
                cleanup_error = RestoreError(f"Disposable upgrade cleanup failed for project {project}: {error}")

    if cleanup_error:
        raise cleanup_error from rehearsal_error
    if rehearsal_error:
        safe_message = str(rehearsal_error).replace(password, "[redacted]")
        raise RestoreError(safe_message) from rehearsal_error
    return project, server_image, backup.name


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
    if version and exact_version(version) is None:
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
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("directory", type=Path, help="Directory containing Immich docker-compose.yml and .env")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--backup", action="store_true", help="Create a new compressed PostgreSQL backup after preflight checks")
    actions.add_argument("--verify-restore", type=Path, metavar="BACKUP", help="Restore a backup into an isolated disposable database")
    actions.add_argument("--plan-upgrade", metavar="VERSION", help="Validate a same-major upgrade plan without changing the installation")
    actions.add_argument("--rehearse-upgrade", metavar="VERSION", help="Start a target release against a restored backup in disposable containers")
    args = parser.parse_args()

    root = args.directory.resolve()
    findings = inspect(root)
    if args.backup:
        findings = [item for item in findings if item.code != "backup.missing"]
    if args.plan_upgrade or args.rehearse_upgrade:
        findings = [item for item in findings if item.code != "backup.missing"]
        upgrade_findings = plan_upgrade(root, args.plan_upgrade or args.rehearse_upgrade)
        if args.rehearse_upgrade:
            upgrade_findings = [item for item in upgrade_findings if item.code != "upgrade.read-only"]
            upgrade_findings.append(
                Finding("INFO", "upgrade.isolated", "Rehearsal pulls the target image and uses only randomly named disposable containers and volumes.")
            )
        findings.extend(upgrade_findings)
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
    if args.verify_restore:
        try:
            project, image = verify_database_restore(root, args.verify_restore)
        except (RestoreError, OSError, subprocess.SubprocessError) as error:
            print(f"FAIL  {error}")
            return 2
        print(f"PASS  Restore verified with {image} in disposable project {project}; resources removed.")
    if args.rehearse_upgrade:
        try:
            project, image, backup_name = rehearse_upgrade(root, args.rehearse_upgrade)
        except (RestoreError, OSError, subprocess.SubprocessError) as error:
            print(f"FAIL  {error}")
            return 2
        print(f"PASS  Upgrade rehearsal passed with {image} and {backup_name} in disposable project {project}; resources removed.")
    if any(item.level == "WARN" for item in findings):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
