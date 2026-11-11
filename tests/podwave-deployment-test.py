#!/usr/bin/env python3
"""Protect PodWave's first-login credential setup."""

from __future__ import annotations

import contextlib
import importlib.util
import io
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "setup_podwave_auth", ROOT / "scripts/setup-podwave-auth.py"
)
assert SPEC and SPEC.loader
AUTH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(AUTH)


class PodWaveAuthSetupTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        AUTH.ENV_FILE = root / "auth.env"
        AUTH.PASSWORD_FILE = root / "password.txt"
        AUTH.CONTROL_PLANE_ENV = root / "control-plane.env"
        AUTH.control_plane_value = lambda _name: "caddy:test@sha256:" + "a" * 64

    def test_password_is_piped_to_pinned_caddy_without_argv_or_error_echo(self) -> None:
        password = "synthetic-password-never-put-in-argv"
        completed = subprocess.CompletedProcess([], 0, "$2a$14$synthetic-hash\n", "")
        output = io.StringIO()
        with (
            patch.object(AUTH.secrets, "token_urlsafe", return_value=password),
            patch.object(AUTH.subprocess, "run", return_value=completed) as run,
            patch.object(sys, "argv", ["setup-podwave-auth.py"]),
            contextlib.redirect_stdout(output),
        ):
            AUTH.main()

        command = run.call_args.args[0]
        self.assertIn("-i", command)
        self.assertEqual(command[-2:], ["caddy", "hash-password"])
        self.assertNotIn(password, command)
        self.assertEqual(run.call_args.kwargs["input"], password + "\n")
        self.assertEqual(AUTH.ENV_FILE.read_text(), "PODWAVE_AUTH_HASH='$2a$14$synthetic-hash'\n")
        self.assertEqual(AUTH.PASSWORD_FILE.read_text(), password + "\n")
        self.assertEqual(AUTH.ENV_FILE.stat().st_mode & 0o777, 0o600)
        self.assertEqual(AUTH.PASSWORD_FILE.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(password, output.getvalue())

    def test_hash_failure_does_not_write_or_echo_credentials(self) -> None:
        password = "synthetic-failure-password"
        failed = subprocess.CompletedProcess([], 127, "", password + " failed")
        output = io.StringIO()
        with (
            patch.object(AUTH.secrets, "token_urlsafe", return_value=password),
            patch.object(AUTH.subprocess, "run", return_value=failed),
            patch.object(sys, "argv", ["setup-podwave-auth.py"]),
            contextlib.redirect_stdout(output),
            self.assertRaisesRegex(SystemExit, "Caddy password hashing failed"),
        ):
            AUTH.main()

        self.assertFalse(AUTH.ENV_FILE.exists())
        self.assertFalse(AUTH.PASSWORD_FILE.exists())
        self.assertNotIn(password, output.getvalue())


if __name__ == "__main__":
    unittest.main()
