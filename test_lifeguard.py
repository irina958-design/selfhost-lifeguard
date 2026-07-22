import tempfile
import unittest
from pathlib import Path

from lifeguard import inspect, read_env


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

    def test_env_parser_keeps_equals_in_value(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text("export TOKEN='a=b'\n", encoding="utf-8")
            self.assertEqual({"TOKEN": "a=b"}, read_env(path))


if __name__ == "__main__":
    unittest.main()
