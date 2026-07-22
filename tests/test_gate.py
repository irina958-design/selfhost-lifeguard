import sys
import tempfile
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


class LicenseInstanceTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        patch = mock.patch.object(gate, "STATE_FILE", Path(self.temp.name) / "instances.json")
        patch.start()
        self.addCleanup(patch.stop)

    def test_first_run_activates_and_later_runs_validate(self):
        calls = []

        def fake_post(endpoint, fields):
            calls.append((endpoint, fields))
            if endpoint == "activate":
                return {"activated": True, "instance": {"id": "inst-1"}}
            return {"valid": True}

        with mock.patch.object(gate, "_post", side_effect=fake_post):
            self.assertTrue(gate.license_valid("KEY"))
            self.assertTrue(gate.license_valid("KEY"))

        self.assertEqual([endpoint for endpoint, _ in calls], ["activate", "validate"])
        self.assertEqual(calls[1][1]["instance_id"], "inst-1")

    def test_activation_limit_reached_fails_closed(self):
        with mock.patch.object(gate, "_post", return_value={"activated": False, "error": "limit reached"}):
            self.assertFalse(gate.license_valid("KEY"))

    def test_network_failure_fails_closed(self):
        with mock.patch.object(gate, "_post", return_value={}):
            self.assertFalse(gate.license_valid("KEY"))

    def test_install_id_is_opaque_and_stable(self):
        with mock.patch.dict(gate.os.environ, {"GITHUB_REPOSITORY": "owner/repo"}):
            first = gate.install_id()
            self.assertEqual(first, gate.install_id())
        self.assertRegex(first, r"^lifeguard-[0-9a-f]{12}$")


if __name__ == "__main__":
    unittest.main()
