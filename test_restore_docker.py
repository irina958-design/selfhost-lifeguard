import gzip
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lifeguard import RestoreError, create_database_backup, verify_database_restore


SCRIPT = Path(__file__).with_name("lifeguard.py").resolve()


def create_install(root: Path, custom_backup: bool = False) -> tuple[list[str], Path]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "library").mkdir()
    backup_directory = root / ("database backups" if custom_backup else "library/backups")
    backup_directory.mkdir(parents=True)
    backup_mount = "      - ${BACKUP_LOCATION}:/data/backups\n" if custom_backup else ""
    (root / "docker-compose.yml").write_text(
        f"""services:
  database:
    image: postgres:14-alpine
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: test-only
      POSTGRES_DB: immich
    healthcheck:
      test: [CMD, psql, --username, postgres, --dbname, immich, -c, SELECT 1]
      interval: 1s
      timeout: 5s
      retries: 60
    volumes:
      - source-data:/var/lib/postgresql/data
{backup_mount}volumes:
  source-data:
""",
        encoding="utf-8",
    )
    custom_env = "BACKUP_LOCATION=./database backups\n" if custom_backup else ""
    (root / ".env").write_text(
        f"UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=source-data\n{custom_env}DB_USERNAME=postgres\nDB_DATABASE_NAME=immich\nIMMICH_VERSION=v3.0.0\nDB_PASSWORD=test-only\n",
        encoding="utf-8",
    )
    return ["docker", "compose", "--project-directory", str(root), "-f", str(root / "docker-compose.yml")], backup_directory


def run_cli(root: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, str(SCRIPT), str(root), *arguments], capture_output=True, text=True)


@unittest.skipUnless(os.environ.get("LIFEGUARD_DOCKER_TEST") == "1", "set LIFEGUARD_DOCKER_TEST=1 to run")
class DockerRestoreTest(unittest.TestCase):
    def assert_no_restore_resources(self):
        containers = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=lifeguard-restore-", "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        volumes = subprocess.run(
            ["docker", "volume", "ls", "--filter", "name=lifeguard-restore-", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual("", containers.stdout.strip())
        self.assertEqual("", volumes.stdout.strip())

    def test_api_rejects_empty_dump_then_restores_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compose_command, _ = create_install(root)
            backup = root / "probe.sql.gz"
            try:
                subprocess.run([*compose_command, "up", "-d", "--wait", "database"], check=True)
                subprocess.run(
                    [*compose_command, "exec", "-T", "database", "psql", "--dbname=immich", "--username=postgres", "-c", "CREATE TABLE backup_probe (value integer); INSERT INTO backup_probe VALUES (1);"],
                    check=True,
                )

                with gzip.open(backup, "wt", encoding="utf-8") as sql:
                    sql.write("SELECT 1;\n")
                with self.assertRaisesRegex(RestoreError, "contains no user tables"):
                    verify_database_restore(root, backup)

                backup = create_database_backup(root)
                with gzip.open(backup, "rt", encoding="utf-8") as sql:
                    self.assertIn("backup_probe", sql.read())
                _, image = verify_database_restore(root, backup)
            finally:
                subprocess.run([*compose_command, "down", "-v", "--remove-orphans"], check=False)

            self.assertEqual("postgres:14-alpine", image)
            self.assert_no_restore_resources()

    def test_cli_custom_backup_path_with_spaces(self):
        with tempfile.TemporaryDirectory(prefix="lifeguard pilot ") as temporary:
            root = Path(temporary)
            compose_command, backup_directory = create_install(root, custom_backup=True)
            try:
                subprocess.run([*compose_command, "up", "-d", "--wait", "database"], check=True)
                subprocess.run(
                    [*compose_command, "exec", "-T", "database", "psql", "--dbname=immich", "--username=postgres", "-c", "CREATE TABLE custom_backup_probe (value integer);"],
                    check=True,
                )

                backup_result = run_cli(root, "--backup")
                self.assertEqual(0, backup_result.returncode, backup_result.stdout + backup_result.stderr)
                backup = next(backup_directory.glob("*.sql.gz"))
                restore_result = run_cli(root, "--verify-restore", str(backup))
                self.assertEqual(0, restore_result.returncode, restore_result.stdout + restore_result.stderr)
            finally:
                subprocess.run([*compose_command, "down", "-v", "--remove-orphans"], check=False)

            self.assert_no_restore_resources()

    def test_cli_scopes_two_parallel_installations(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            selected = root / f"{root.name} selected installation"
            other = root / f"{root.name} other installation"
            selected_command, selected_backups = create_install(selected)
            other_command, _ = create_install(other)
            try:
                subprocess.run([*selected_command, "up", "-d", "--wait", "database"], check=True)
                subprocess.run([*other_command, "up", "-d", "--wait", "database"], check=True)
                subprocess.run(
                    [*selected_command, "exec", "-T", "database", "psql", "--dbname=immich", "--username=postgres", "-c", "CREATE TABLE selected_installation_probe (value integer);"],
                    check=True,
                )
                subprocess.run(
                    [*other_command, "exec", "-T", "database", "psql", "--dbname=immich", "--username=postgres", "-c", "CREATE TABLE other_installation_probe (value integer);"],
                    check=True,
                )

                backup_result = run_cli(selected, "--backup")
                self.assertEqual(0, backup_result.returncode, backup_result.stdout + backup_result.stderr)
                backup = next(selected_backups.glob("*.sql.gz"))
                with gzip.open(backup, "rt", encoding="utf-8") as sql:
                    dump = sql.read()
                self.assertIn("selected_installation_probe", dump)
                self.assertNotIn("other_installation_probe", dump)
                restore_result = run_cli(selected, "--verify-restore", str(backup))
                self.assertEqual(0, restore_result.returncode, restore_result.stdout + restore_result.stderr)
            finally:
                subprocess.run([*selected_command, "down", "-v", "--remove-orphans"], check=False)
                subprocess.run([*other_command, "down", "-v", "--remove-orphans"], check=False)

            self.assert_no_restore_resources()


if __name__ == "__main__":
    unittest.main()
