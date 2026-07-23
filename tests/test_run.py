import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import run


class RunTest(unittest.TestCase):
    def test_backup_runs(self):
        with mock.patch.object(run.subprocess, "call", return_value=0) as call, \
             mock.patch.object(sys, "argv", ["run.py", "--directory", "/app", "--command", "backup"]):
            self.assertEqual(run.main(), 0)
            self.assertIn("--backup", call.call_args.args[0])

    def test_rehearse_upgrade_runs(self):
        with mock.patch.object(run.subprocess, "call", return_value=0) as call, \
             mock.patch.object(sys, "argv", ["run.py", "--directory", "/app", "--command", "rehearse-upgrade", "--version", "3.0.3"]):
            self.assertEqual(run.main(), 0)
            self.assertIn("--rehearse-upgrade", call.call_args.args[0])

    def test_verify_restore_without_backup_path(self):
        with mock.patch.object(run.subprocess, "call") as call, \
             mock.patch.object(sys, "argv", ["run.py", "--directory", "/app", "--command", "verify-restore"]):
            self.assertEqual(run.main(), 2)
            call.assert_not_called()

    def test_unknown_command(self):
        with mock.patch.object(run.subprocess, "call") as call, \
             mock.patch.object(sys, "argv", ["run.py", "--directory", "/app", "--command", "nonsense"]):
            self.assertEqual(run.main(), 2)
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
