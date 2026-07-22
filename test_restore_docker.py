import gzip
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from lifeguard import RestoreError, create_database_backup, verify_database_restore


@unittest.skipUnless(os.environ.get("LIFEGUARD_DOCKER_TEST") == "1", "set LIFEGUARD_DOCKER_TEST=1 to run")
class DockerRestoreTest(unittest.TestCase):
    def test_restore_is_isolated_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            compose = root / "docker-compose.yml"
            compose.write_text(
                """services:
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
volumes:
  source-data:
""",
                encoding="utf-8",
            )
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=source-data\nDB_USERNAME=postgres\nDB_DATABASE_NAME=immich\nIMMICH_VERSION=v3.0.0\nDB_PASSWORD=test-only\n",
                encoding="utf-8",
            )
            (root / "library" / "backups").mkdir(parents=True)
            compose_command = ["docker", "compose", "--project-directory", str(root), "-f", str(compose)]
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
                project, image = verify_database_restore(root, backup)
            finally:
                subprocess.run([*compose_command, "down", "-v", "--remove-orphans"], check=False)

            self.assertEqual("postgres:14-alpine", image)
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


if __name__ == "__main__":
    unittest.main()
