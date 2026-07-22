import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from lifeguard import create_database_backup, verify_database_restore
from test_restore_docker import SCRIPT, create_install, run_cli


PAYLOAD_ROWS = 65_536
MIB = 1024 * 1024


@unittest.skipUnless(os.environ.get("LIFEGUARD_ENGINEERING_TEST") == "1", "set LIFEGUARD_ENGINEERING_TEST=1 to run")
class EngineeringAcceptanceTest(unittest.TestCase):
    def assert_no_restore_resources(self):
        for command in (
            ["docker", "ps", "-a", "--filter", "name=lifeguard-restore-", "--format", "{{.ID}}"],
            ["docker", "volume", "ls", "--filter", "name=lifeguard-restore-", "--format", "{{.Name}}"],
            ["docker", "network", "ls", "--filter", "name=lifeguard-restore-", "--format", "{{.Name}}"],
        ):
            result = subprocess.run(command, capture_output=True, text=True, check=True)
            self.assertEqual("", result.stdout.strip())

    def test_large_stream_restore_and_interrupted_backup_cleanup(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compose_command, backup_directory = create_install(root)
            backup_process = None
            try:
                subprocess.run([*compose_command, "up", "-d", "--wait", "database"], check=True, timeout=300)
                subprocess.run(
                    [
                        *compose_command,
                        "exec",
                        "-T",
                        "database",
                        "psql",
                        "--dbname=immich",
                        "--username=postgres",
                        "-v",
                        "ON_ERROR_STOP=1",
                        "-c",
                        f"""CREATE TABLE engineering_payload AS
SELECT rows.id,
       (SELECT string_agg(md5(rows.id::text || ':' || parts.part::text), '' ORDER BY parts.part)
          FROM generate_series(1, 32) AS parts(part)) AS payload
  FROM generate_series(1, {PAYLOAD_ROWS}) AS rows(id);""",
                    ],
                    check=True,
                    timeout=300,
                )
                size = subprocess.run(
                    [
                        *compose_command,
                        "exec",
                        "-T",
                        "database",
                        "psql",
                        "--dbname=immich",
                        "--username=postgres",
                        "--tuples-only",
                        "--no-align",
                        "-c",
                        "SELECT pg_total_relation_size('engineering_payload');",
                    ],
                    capture_output=True,
                    text=True,
                    check=True,
                    timeout=60,
                )
                self.assertGreater(int(size.stdout.strip()), 48 * MIB)

                backup = create_database_backup(root)
                self.assertGreater(backup.stat().st_size, 16 * MIB)
                verify_database_restore(root, backup)
                expected_backups = set(backup_directory.glob("immich-db-*.sql.gz"))

                backup_process = subprocess.Popen(
                    [sys.executable, str(SCRIPT), str(root), "--backup"],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline:
                    progress = list(backup_directory.glob(".lifeguard-*/backup.tmp"))
                    if progress and progress[0].stat().st_size > MIB:
                        break
                    if backup_process.poll() is not None:
                        output = backup_process.communicate()[0]
                        self.fail(f"Backup finished before interruption could be injected:\n{output}")
                    time.sleep(0.05)
                else:
                    self.fail("Backup did not produce a staged stream within 60 seconds")

                subprocess.run([*compose_command, "kill", "database"], check=True, timeout=60)
                output = backup_process.communicate(timeout=180)[0]
                self.assertEqual(2, backup_process.returncode, output)
                self.assertIn("pg_dump failed", output)
                self.assertEqual(expected_backups, set(backup_directory.glob("immich-db-*.sql.gz")))
                self.assertFalse(list(backup_directory.glob(".lifeguard-*")))
            finally:
                if backup_process and backup_process.poll() is None:
                    backup_process.kill()
                    backup_process.communicate()
                subprocess.run([*compose_command, "down", "-v", "--remove-orphans"], check=False, timeout=180)

            self.assert_no_restore_resources()

    @unittest.skipUnless(os.name == "posix", "POSIX permission semantics are required")
    def test_non_writable_backup_directory_fails_safely(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, backup_directory = create_install(root)
            backup_directory.chmod(0o500)
            try:
                if os.access(backup_directory, os.W_OK):
                    self.skipTest("current user can still write to a mode 0500 directory")
                result = run_cli(root, "--backup")
            finally:
                backup_directory.chmod(0o700)

            self.assertEqual(2, result.returncode, result.stdout + result.stderr)
            self.assertIn("could not be staged safely", result.stdout)
            self.assertNotIn("Traceback", result.stdout + result.stderr)
            self.assertFalse(list(backup_directory.iterdir()))


if __name__ == "__main__":
    unittest.main()
