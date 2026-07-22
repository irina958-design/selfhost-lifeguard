import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import gate


class GateTest(unittest.TestCase):
    def test_pro_command_blocked_without_license(self):
        with mock.patch.object(gate, "license_valid", return_value=False), \
             mock.patch.object(gate.subprocess, "call") as call, \
             mock.patch.object(sys, "argv", ["gate.py", "--directory", "/app", "--command", "verify-restore", "--backup", "b.sql.gz"]):
            self.assertEqual(gate.main(), 3)
            call.assert_not_called()

    def test_pro_command_runs_with_valid_license(self):
        with mock.patch.object(gate, "license_valid", return_value=True), \
             mock.patch.object(gate.subprocess, "call", return_value=0) as call, \
             mock.patch.object(sys, "argv", ["gate.py", "--directory", "/app", "--command", "rehearse-upgrade", "--version", "3.0.3"]):
            self.assertEqual(gate.main(), 0)
            self.assertIn("--rehearse-upgrade", call.call_args.args[0])

    def test_free_command_runs_without_license(self):
        with mock.patch.object(gate.subprocess, "call", return_value=0) as call, \
             mock.patch.object(sys, "argv", ["gate.py", "--directory", "/app", "--command", "backup"]):
            self.assertEqual(gate.main(), 0)
            self.assertIn("--backup", call.call_args.args[0])

    def test_pro_command_with_license_but_missing_arg(self):
        with mock.patch.object(gate, "license_valid", return_value=True), \
             mock.patch.object(gate.subprocess, "call") as call, \
             mock.patch.object(sys, "argv", ["gate.py", "--directory", "/app", "--command", "verify-restore"]):
            self.assertEqual(gate.main(), 2)
            call.assert_not_called()


if __name__ == "__main__":
    unittest.main()
