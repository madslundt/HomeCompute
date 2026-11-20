#!/usr/bin/env python3
"""Verify that private profile import stays in the partner Hermes home."""

import importlib.util
import io
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("configure_openbot_hermes", ROOT / "scripts/configure-openbot-hermes.py")
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class PartnerHermesTest(unittest.TestCase):
    def setUp(self):
        self.scratch = tempfile.TemporaryDirectory()
        self.addCleanup(self.scratch.cleanup)
        self.home = Path(self.scratch.name) / "data"
        self.home.mkdir()
        self.profile = self.home / ".hermes/memories/USER.md"

    def run_configure(self, source, volume=None):
        settings = {setup.COMPRESSION_KEY: str(setup.COMPRESSION_THRESHOLD_TOKENS)}
        commands = []

        def docker(*args):
            commands.append(args)
            if args[1] == "inspect":
                return "true" if args[3] == "{{.State.Running}}" else str(volume or self.home)
            if args[4:7] == ("config", "get", "custom_providers"):
                return "[]"
            if args[4:6] == ("config", "set"):
                settings[args[6]] = args[7]
            if args[4:6] == ("config", "get"):
                return settings[args[6]]
            return ""

        args = ["configure-openbot-hermes.py", "--container", setup.PARTNER_CONTAINER, "--install-user-profile"]
        with (patch.object(setup, "PARTNER_DATA", self.home), patch.object(setup, "run", side_effect=docker),
              patch("sys.argv", args), patch("sys.stdin", io.StringIO(source)),
              patch("sys.stdout", new_callable=io.StringIO) as stdout,
              patch("sys.stderr", new_callable=io.StringIO) as stderr):
            result = setup.main()
        return result, settings, commands, stdout.getvalue() + stderr.getvalue()

    def test_import_verifies_local_model_and_writes_only_private_memory(self):
        value = "Jeg hedder Testperson. Foretrækker dansk.\n"
        result, settings, commands, output = self.run_configure(value)
        self.assertEqual(result, 0, output)
        self.assertEqual(settings["model.base_url"], "http://ai.home.arpa:18080/v1")
        self.assertEqual(settings["model.default"], "automation-moe")
        self.assertIn("updates:\n  check: false\n", (self.home / ".hermes/config.yaml").read_text())
        self.assertTrue(all(setup.PARTNER_CONTAINER in command for command in commands))
        self.assertEqual(self.profile.read_text(), value)
        self.assertEqual(stat.S_IMODE(self.profile.stat().st_mode), 0o600)
        self.assertNotIn("Testperson", output)
        self.assertEqual(self.run_configure(value)[0], 0)
        self.assertEqual(self.run_configure("A different profile")[0], 1)
        self.assertEqual(self.profile.read_text(), value)

    def test_rejects_incorrect_mount_and_oversized_profile(self):
        result, _, _, output = self.run_configure("Synthetic profile", volume=self.home.parent)
        self.assertEqual(result, 1)
        self.assertIn("does not match the managed data directory", output)
        result, _, commands, _ = self.run_configure("a" * (setup.PARTNER_PROFILE_MAX_CHARS + 2))
        self.assertEqual(result, 1)
        self.assertFalse(commands)
        self.assertFalse(self.profile.exists())


if __name__ == "__main__":
    unittest.main()
