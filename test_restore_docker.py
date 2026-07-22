import gzip
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from lifeguard import verify_database_restore


@unittest.skipUnless(os.environ.get("LIFEGUARD_DOCKER_TEST") == "1", "set LIFEGUARD_DOCKER_TEST=1 to run")
class DockerRestoreTest(unittest.TestCase):
    def test_restore_is_isolated_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "docker-compose.yml").write_text("services:\n  database:\n    image: postgres:14-alpine\n", encoding="utf-8")
            (root / ".env").write_text(
                "UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=./postgres\nDB_USERNAME=postgres\nDB_DATABASE_NAME=immich\nIMMICH_VERSION=v3.0.0\nDB_PASSWORD=test-only\n",
                encoding="utf-8",
            )
            backup = root / "probe.sql.gz"
            with gzip.open(backup, "wt", encoding="utf-8") as sql:
                sql.write("CREATE TABLE restore_probe (value integer);\nINSERT INTO restore_probe VALUES (1);\n")

            project, image = verify_database_restore(root, backup)

            self.assertEqual("postgres:14-alpine", image)
            containers = subprocess.run(
                ["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.ID}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            volumes = subprocess.run(
                ["docker", "volume", "ls", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.Name}}"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual("", containers.stdout.strip())
            self.assertEqual("", volumes.stdout.strip())


if __name__ == "__main__":
    unittest.main()
