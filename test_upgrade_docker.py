import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from lifeguard import create_database_backup


SCRIPT = Path(__file__).with_name("lifeguard.py").resolve()
DATABASE_IMAGE = "ghcr.io/immich-app/postgres:14-vectorchord0.4.3-pgvectors0.2.0@sha256:bcf63357191b76a916ae5eb93464d65c07511da41e3bf7a8416db519b40b1c23"
V2_REDIS_IMAGE = "docker.io/valkey/valkey:9@sha256:3b55fbaa0cd93cf0d9d961f405e4dfcc70efe325e2d84da207a0a8e6d8fde4f9"
V3_REDIS_IMAGE = "docker.io/valkey/valkey:9@sha256:4963247afc4cd33c7d3b2d2816b9f7f8eeebab148d29056c2ca4d7cbc966f2d9"


def create_source_install(root: Path, source_version: str, redis_image: str) -> list[str]:
    root.mkdir(parents=True)
    (root / "library" / "backups").mkdir(parents=True)
    (root / "docker-compose.yml").write_text(
        f"""services:
  database:
    image: {DATABASE_IMAGE}
    environment:
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: test-only
      POSTGRES_DB: immich
      POSTGRES_INITDB_ARGS: --data-checksums
    healthcheck:
      test: [CMD, psql, --username, postgres, --dbname, immich, -c, SELECT 1]
      interval: 1s
      timeout: 5s
      retries: 120
    volumes:
      - source-db:/var/lib/postgresql/data
  redis:
    image: {redis_image}
    healthcheck:
      test: [CMD, redis-cli, ping]
      interval: 1s
      timeout: 5s
      retries: 60
  immich-server:
    image: ghcr.io/immich-app/immich-server:{source_version}
    environment:
      DB_HOSTNAME: database
      DB_USERNAME: postgres
      DB_PASSWORD: test-only
      DB_DATABASE_NAME: immich
      REDIS_HOSTNAME: redis
      IMMICH_WORKERS_INCLUDE: api
    depends_on:
      database:
        condition: service_healthy
      redis:
        condition: service_healthy
    healthcheck:
      test: [CMD, immich-healthcheck]
      interval: 2s
      timeout: 5s
      retries: 150
      start_period: 10s
    volumes:
      - source-upload:/data
volumes:
  source-db:
  source-upload:
""",
        encoding="utf-8",
    )
    (root / ".env").write_text(
        f"UPLOAD_LOCATION=./library\nDB_DATA_LOCATION=source-db\nDB_USERNAME=postgres\nDB_DATABASE_NAME=immich\nIMMICH_VERSION={source_version}\nDB_PASSWORD=test-only\n",
        encoding="utf-8",
    )
    return ["docker", "compose", "--project-directory", str(root), "-f", str(root / "docker-compose.yml")]


@unittest.skipUnless(os.environ.get("LIFEGUARD_UPGRADE_TEST") == "1", "set LIFEGUARD_UPGRADE_TEST=1 to run")
class DockerUpgradeTest(unittest.TestCase):
    def test_real_immich_patch_upgrade_rehearsals(self):
        for source, target, redis_image in (
            ("v2.7.4", "v2.7.5", V2_REDIS_IMAGE),
            ("v3.0.2", "v3.0.3", V3_REDIS_IMAGE),
        ):
            with self.subTest(source=source, target=target), tempfile.TemporaryDirectory(prefix="lifeguard real upgrade ") as temporary:
                root = Path(temporary) / "source installation"
                compose_command = create_source_install(root, source, redis_image)
                try:
                    subprocess.run([*compose_command, "up", "-d", "--wait"], check=True, timeout=900)
                    backup = create_database_backup(root)
                    result = subprocess.run(
                        [sys.executable, str(SCRIPT), str(root), "--rehearse-upgrade", target],
                        capture_output=True,
                        text=True,
                        timeout=1200,
                    )
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    self.assertIn("Upgrade rehearsal passed", result.stdout)
                    self.assertIn(backup.name, result.stdout)
                finally:
                    subprocess.run([*compose_command, "down", "-v", "--remove-orphans"], check=False, timeout=180)

        containers = subprocess.run(
            ["docker", "ps", "-a", "--filter", "name=lifeguard-upgrade-", "--format", "{{.ID}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        volumes = subprocess.run(
            ["docker", "volume", "ls", "--filter", "name=lifeguard-upgrade-", "--format", "{{.Name}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual("", containers.stdout.strip())
        self.assertEqual("", volumes.stdout.strip())


if __name__ == "__main__":
    unittest.main()
