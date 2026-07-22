import gzip
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from lifeguard import BackupError, build_backup_command, create_database_backup, custom_backup_is_mounted, inspect, main, plan_upgrade, read_env, rehearse_upgrade, verify_database_restore


class LifeguardTest(unittest.TestCase):
    def test_ready_installation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=./postgres\nIMMICH_VERSION=v3.0.0\nDB_PASSWORD=changed123\n",
                encoding="utf-8",
            )
            (root / "library" / "backups").mkdir(parents=True)
            (root / "library" / "backups" / "dump.sql.gz").touch()
            (root / "postgres").mkdir()

            findings = inspect(root)
            self.assertFalse([item for item in findings if item.level in {"WARN", "FAIL"}])

    def test_missing_env_is_blocking(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").touch()
            self.assertIn("env.missing", {item.code for item in inspect(root) if item.level == "FAIL"})

    def test_official_defaults_are_reported_not_blocked(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services:\n  immich-server: {}\n  database: {}\n", encoding="utf-8")
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=./postgres\nIMMICH_VERSION=v3\nDB_PASSWORD=postgres\n",
                encoding="utf-8",
            )
            (root / "library").mkdir()
            (root / "postgres").mkdir()

            findings = inspect(root)
            codes = {item.code for item in findings}
            self.assertFalse([item for item in findings if item.level == "FAIL"])
            self.assertTrue({"version.unpinned", "db.default-password", "backup.missing"} <= codes)

    def test_custom_backup_location(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text(
                "services:\n  immich-server:\n    volumes:\n      - ${BACKUP_LOCATION}:/data/backups\n",
                encoding="utf-8",
            )
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=./postgres\nBACKUP_LOCATION=./database-backups\nIMMICH_VERSION=v3.0.0\nDB_PASSWORD=changed123\n",
                encoding="utf-8",
            )
            (root / "library").mkdir()
            (root / "postgres").mkdir()
            (root / "database-backups").mkdir()
            (root / "database-backups" / "dump.sql.gz").touch()

            findings = inspect(root)
            self.assertIn("backup.found", {item.code for item in findings})
            self.assertNotIn("backup.missing", {item.code for item in findings})

    def test_unmounted_custom_backup_is_unverified(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=./postgres\nBACKUP_LOCATION=./database-backups\nIMMICH_VERSION=v3.0.0\nDB_PASSWORD=changed123\n",
                encoding="utf-8",
            )
            (root / "library").mkdir()
            (root / "postgres").mkdir()

            codes = {item.code for item in inspect(root)}
            self.assertIn("backup.unverified", codes)
            self.assertNotIn("backup.missing", codes)

    def test_custom_backup_mount_detection_is_line_scoped(self):
        self.assertTrue(custom_backup_is_mounted("- ${BACKUP_LOCATION}:/data/backups\n"))
        self.assertFalse(custom_backup_is_mounted("# ${BACKUP_LOCATION}:/data/backups\n"))

    def test_backup_command_and_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=./postgres\nDB_USERNAME=lifeguard_user\nDB_DATABASE_NAME=lifeguard_db\nIMMICH_VERSION=v3.0.0\nDB_PASSWORD=not-on-command-line\n",
                encoding="utf-8",
            )
            (root / "library" / "backups").mkdir(parents=True)

            def fake_popen(command, stdout, stderr):
                self.assertEqual(build_backup_command(root, root / "docker-compose.yml", read_env(root / ".env")), command)
                self.assertNotIn("not-on-command-line", " ".join(command))
                return SimpleNamespace(stdout=io.BytesIO(b"CREATE TABLE example ();\n"), wait=lambda: 0)

            real_gzip_open = gzip.open

            def private_gzip_open(path, mode):
                self.assertEqual(root / "library" / "backups", Path(path).parent.parent)
                return real_gzip_open(path, mode)

            with patch("lifeguard.subprocess.Popen", side_effect=fake_popen), patch(
                "lifeguard.gzip.open", side_effect=private_gzip_open
            ):
                backup = create_database_backup(root)

            self.assertTrue(backup.exists())
            with gzip.open(backup, "rb") as saved:
                self.assertEqual(b"CREATE TABLE example ();\n", saved.read())

    def test_failed_backup_leaves_no_output(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=./postgres\nIMMICH_VERSION=v3.0.0\nDB_PASSWORD=changed123\n",
                encoding="utf-8",
            )
            backup_directory = root / "library" / "backups"
            backup_directory.mkdir(parents=True)

            def failed_popen(command, stdout, stderr):
                stderr.write(b"database connection failed")
                return SimpleNamespace(stdout=io.BytesIO(), wait=lambda: 1)

            with patch("lifeguard.subprocess.Popen", side_effect=failed_popen):
                with self.assertRaises(BackupError):
                    create_database_backup(root)

            self.assertFalse(list(backup_directory.iterdir()))

    def test_successful_first_backup_clears_missing_backup_exit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=./postgres\nIMMICH_VERSION=v3.0.0\nDB_PASSWORD=changed123\n",
                encoding="utf-8",
            )
            backup_directory = root / "library" / "backups"
            backup_directory.mkdir(parents=True)
            (root / "postgres").mkdir()

            def create_backup(_root):
                backup = backup_directory / "first.sql.gz"
                backup.touch()
                return backup

            output = io.StringIO()
            with patch.object(sys, "argv", ["lifeguard.py", str(root), "--backup"]), patch(
                "lifeguard.create_database_backup", side_effect=create_backup
            ), patch("sys.stdout", new=output):
                self.assertEqual(0, main())
            self.assertNotIn("No database backup found", output.getvalue())

    def test_upgrade_plan_accepts_only_newer_same_major_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / ".env").write_text("UPLOAD_LOCATION=./library\nIMMICH_VERSION=v3.0.3\n", encoding="utf-8")
            (root / "library" / "backups").mkdir(parents=True)
            (root / "library" / "backups" / "dump.sql.gz").touch()

            self.assertFalse([item for item in plan_upgrade(root, "v3.0.4") if item.level == "FAIL"])
            for target in ("v3.0.3", "v3.0.2", "v4.0.0", "v3"):
                with self.subTest(target=target):
                    self.assertTrue([item for item in plan_upgrade(root, target) if item.level == "FAIL"])

    def test_upgrade_plan_requires_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=./postgres\nIMMICH_VERSION=v3.0.3\nDB_PASSWORD=changed123\n",
                encoding="utf-8",
            )
            (root / "library" / "backups").mkdir(parents=True)
            (root / "postgres").mkdir()

            output = io.StringIO()
            with patch.object(sys, "argv", ["lifeguard.py", str(root), "--plan-upgrade", "v3.0.4"]), patch("sys.stdout", new=output):
                self.assertEqual(2, main())
            self.assertIn("FAIL  A database backup in the verified backup directory is required", output.getvalue())

    def test_upgrade_rehearsal_uses_only_disposable_storage_and_password(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./private-library\nDB_DATA_LOCATION=./private-database\nIMMICH_VERSION=v3.0.2\nDB_PASSWORD=production-secret\n",
                encoding="utf-8",
            )
            (root / "private-library" / "backups").mkdir(parents=True)
            (root / "private-library" / "backups" / "dump.sql.gz").touch()
            captured = {}

            def fake_run_checked(command, cwd, timeout):
                compose_path = Path(command[command.index("-f") + 1]) if "-f" in command else None
                if compose_path and compose_path.name == "compose.json":
                    captured.update(json.loads(compose_path.read_text(encoding="utf-8")))
                if command[-2:] == ["immich-admin", "version"]:
                    return "v3.0.3\n"
                if command[-2:] == ["immich-admin", "schema-check"]:
                    return "Migrations are up to date\n\nNo schema drift detected\n"
                if command[-1].startswith("SELECT COUNT"):
                    return "42\n"
                return ""

            cleanup = SimpleNamespace(returncode=0, stderr="")
            with patch("lifeguard.service_image", side_effect=["official-postgres", "official-valkey"]), patch(
                "lifeguard.run_checked", side_effect=fake_run_checked
            ), patch("lifeguard.restore_backup"), patch("lifeguard.subprocess.run", return_value=cleanup):
                _, image, backup_name = rehearse_upgrade(root, "v3.0.3")

            self.assertEqual("ghcr.io/immich-app/immich-server:v3.0.3", image)
            self.assertEqual("dump.sql.gz", backup_name)
            serialized = json.dumps(captured)
            self.assertNotIn("production-secret", serialized)
            self.assertNotIn("private-library", serialized)
            self.assertNotIn("private-database", serialized)
            self.assertNotIn("ports", captured["services"]["immich-server"])
            self.assertEqual(["rehearsal-upload:/data"], captured["services"]["immich-server"]["volumes"])
            self.assertTrue(captured["networks"]["default"]["internal"])

    def test_restore_verification_uses_internal_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
            (root / ".env").write_text("DB_USERNAME=postgres\nDB_DATABASE_NAME=immich\n", encoding="utf-8")
            backup = root / "dump.sql"
            backup.write_text("CREATE TABLE example ();\n", encoding="utf-8")
            captured = {}

            def fake_run_checked(command, cwd, timeout):
                compose_path = Path(command[command.index("-f") + 1])
                captured.update(json.loads(compose_path.read_text(encoding="utf-8")))
                return "1\n" if command[-1].startswith("SELECT COUNT") else ""

            cleanup = SimpleNamespace(returncode=0, stderr="")
            with patch("lifeguard.database_image", return_value="official-postgres"), patch(
                "lifeguard.run_checked", side_effect=fake_run_checked
            ), patch("lifeguard.restore_backup"), patch("lifeguard.subprocess.run", return_value=cleanup):
                verify_database_restore(root, backup)

            self.assertTrue(captured["networks"]["default"]["internal"])

    def test_env_parser_keeps_equals_in_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("export TOKEN='a=b'\n", encoding="utf-8")
            self.assertEqual({"TOKEN": "a=b"}, read_env(path))


if __name__ == "__main__":
    unittest.main()
