"""Regression tests for Realm listener detection and UFW ownership.

All OS commands are mocked. Tests never modify systemd or the host firewall.
Run directly: python tests/realm_runtime_regression.py
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("realm_runtime_regression_module", ROOT / "agent" / "realm_plugin.py")
assert spec and spec.loader
realm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(realm)


class ListenerTests(unittest.TestCase):
    def listening(self, port: int, output: str, code: int = 0) -> bool:
        with patch.object(realm, "_run", return_value=(code, output)) as run:
            result = realm._port_listening(port)
        run.assert_called_once_with(["ss", "-H", "-lnt"], timeout=10)
        return result

    def test_ipv4_listener(self):
        self.assertTrue(self.listening(443, "LISTEN 0 4096 0.0.0.0:443 0.0.0.0:*\n"))

    def test_ipv6_listener(self):
        for address in ("[::]:443", "[2001:db8::1]:443", "*:443"):
            with self.subTest(address=address):
                self.assertTrue(self.listening(443, f"LISTEN 0 4096 {address} *:*\n"))

    def test_ipv6_segment_is_not_a_port(self):
        output = "LISTEN 0 4096 [2001:db8:443::1]:8443 [::]:*\n"
        self.assertFalse(self.listening(443, output))
        self.assertTrue(self.listening(8443, output))

    def test_only_local_endpoint_counts(self):
        self.assertFalse(self.listening(443, "LISTEN 0 4096 127.0.0.1:8443 192.0.2.1:443\n"))

    def test_port_prefix_does_not_match(self):
        self.assertFalse(self.listening(443, "LISTEN 0 4096 0.0.0.0:4430 0.0.0.0:*\n"))

    def test_nonlisteners_and_errors_do_not_match(self):
        for code, output in (
            (1, "ss: cannot inspect port :443"),
            (0, ""),
            (0, "unexpected :443"),
            (0, "ESTAB 0 0 127.0.0.1:443 127.0.0.1:1234\n"),
        ):
            with self.subTest(code=code, output=output):
                self.assertFalse(self.listening(443, output, code))


class FirewallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="realm-ufw-test-")
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.ownership = self.directory / "ufw-created.json"
        self.which = patch.object(realm.shutil, "which", return_value="/usr/sbin/ufw")
        self.which.start()
        self.addCleanup(self.which.stop)
        self.commands = []
        self.active = True
        self.allow_results = {}

    def fake_run(self, command, timeout=30):
        self.commands.append(command)
        if command == ["ufw", "status"]:
            return 0, "Status: active" if self.active else "Status: inactive"
        if command[:2] == ["ufw", "allow"]:
            return self.allow_results.get(command[2], (0, "Skipping adding existing rule"))
        if command[:4] == ["ufw", "--force", "delete", "allow"]:
            return 0, "Rule deleted"
        raise AssertionError(f"Unexpected external command: {command}")

    def configure(self, mappings, role="edge"):
        with patch.object(realm, "_run", side_effect=self.fake_run):
            return realm._configure_ufw(self.directory, role, mappings)

    def read_owned(self):
        return json.loads(self.ownership.read_text(encoding="utf-8"))

    def test_redeploy_retains_previous_ownership(self):
        self.ownership.write_text('["443/tcp"]')
        self.assertEqual(self.configure([(443, 8000, 3080)]), [])
        self.assertEqual(self.read_owned(), ["443/tcp"])
        with patch.object(realm, "_run", side_effect=self.fake_run):
            realm._remove_ufw_rules(self.read_owned())
        self.assertIn(["ufw", "--force", "delete", "allow", "443/tcp"], self.commands)

    def test_new_rules_are_merged_but_only_new_rules_returned(self):
        self.ownership.write_text('["443/tcp", "443/tcp"]')
        self.allow_results["8443/tcp"] = (0, "Rule added\nRule added (v6)")
        self.assertEqual(self.configure([(443, 8000, 3080), (8443, 8000, 3081)]), ["8443/tcp"])
        self.assertEqual(self.read_owned(), ["443/tcp", "8443/tcp"])
        self.assertEqual(self.ownership.stat().st_mode & 0o777, 0o600)

    def test_preexisting_unowned_rule_is_not_claimed(self):
        self.assertEqual(self.configure([(443, 8000, 3080)]), [])
        self.assertEqual(self.read_owned(), [])

    def test_gateway_tracks_backbone_instead_of_public_port(self):
        self.allow_results["3080/tcp"] = (0, "Rule added")
        self.assertEqual(self.configure([(443, 8000, 3080)], "gateway"), ["3080/tcp"])
        self.assertEqual(self.read_owned(), ["3080/tcp"])

    def test_failed_allow_rolls_back_only_new_rules(self):
        original = '["443/tcp"]'
        self.ownership.write_text(original)
        self.allow_results["8443/tcp"] = (0, "Rule added")
        self.allow_results["9443/tcp"] = (1, "test failure")
        with self.assertRaises(RuntimeError):
            self.configure([(443, 8000, 3080), (8443, 8000, 3081), (9443, 8000, 3082)])
        self.assertEqual(self.ownership.read_text(), original)
        deleted = [item for item in self.commands if item[:3] == ["ufw", "--force", "delete"]]
        self.assertEqual(deleted, [["ufw", "--force", "delete", "allow", "8443/tcp"]])

    def test_failed_ownership_write_rolls_back_new_rules(self):
        original = '["443/tcp"]'
        self.ownership.write_text(original)
        self.allow_results["8443/tcp"] = (0, "Rule added")
        with patch.object(realm, "_atomic_text", side_effect=OSError("test disk full")):
            with self.assertRaises(OSError):
                self.configure([(443, 8000, 3080), (8443, 8000, 3081)])
        self.assertEqual(self.ownership.read_text(), original)
        self.assertIn(["ufw", "--force", "delete", "allow", "8443/tcp"], self.commands)
        self.assertNotIn(["ufw", "--force", "delete", "allow", "443/tcp"], self.commands)

    def test_corrupt_ownership_does_not_get_silently_erased(self):
        for invalid in ('{', '{}', '"443/tcp"', '[null]', '["0/tcp"]', '["65536/tcp"]', '["443/udp"]'):
            with self.subTest(invalid=invalid):
                self.commands.clear()
                self.ownership.write_text(invalid)
                with self.assertRaises(RuntimeError):
                    self.configure([(443, 8000, 3080)])
                self.assertEqual(self.ownership.read_text(), invalid)
                self.assertEqual(self.commands, [["ufw", "status"]])

    def test_inactive_ufw_preserves_ownership(self):
        self.active = False
        self.ownership.write_text('["443/tcp"]')
        self.assertEqual(self.configure([(443, 8000, 3080)]), [])
        self.assertEqual(self.read_owned(), ["443/tcp"])
        self.assertEqual(self.commands, [["ufw", "status"]])

    def test_missing_ufw_preserves_ownership(self):
        self.ownership.write_text('["443/tcp"]')
        with patch.object(realm.shutil, "which", return_value=None):
            self.assertEqual(self.configure([(443, 8000, 3080)]), [])
        self.assertEqual(self.read_owned(), ["443/tcp"])
        self.assertEqual(self.commands, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)
