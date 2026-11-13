#!/usr/bin/env python3
"""Tests PodWave's private gateway CA setup."""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ENTRYPOINT = ROOT / "deploy/podwave/podwave-entrypoint.sh"


class PodWaveNetworkTests(unittest.TestCase):
    def test_entrypoint_combines_system_and_homecompute_ca_bundles(self) -> None:
        with tempfile.TemporaryDirectory(prefix="podwave-ca-test-") as temp:
            directory = Path(temp)
            system_bundle = directory / "system-ca.pem"
            homecompute_ca = directory / "homecompute-root.crt"
            combined_bundle = directory / "combined.pem"
            system_bundle.write_text("system roots\n")
            homecompute_ca.write_text("HomeCompute root\n")

            result = subprocess.run(
                ["/bin/sh", str(ENTRYPOINT), "sh", "-c", 'printf "%s" "$SSL_CERT_FILE"'],
                check=True,
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "SSL_CERT_FILE": str(system_bundle),
                    "HOMECOMPUTE_ROOT_CA": str(homecompute_ca),
                    "PODWAVE_CA_BUNDLE": str(combined_bundle),
                },
            )

            self.assertEqual(result.stdout, str(combined_bundle))
            self.assertEqual(combined_bundle.read_text(), "system roots\nHomeCompute root\n")
            self.assertEqual(combined_bundle.stat().st_mode & 0o777, 0o600)

    def test_entrypoint_fails_closed_when_homecompute_ca_is_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="podwave-ca-missing-") as temp:
            directory = Path(temp)
            system_bundle = directory / "system-ca.pem"
            system_bundle.write_text("system roots\n")
            result = subprocess.run(
                ["/bin/sh", str(ENTRYPOINT), "sh", "-c", "echo should-not-run"],
                capture_output=True,
                text=True,
                env={
                    **os.environ,
                    "SSL_CERT_FILE": str(system_bundle),
                    "HOMECOMPUTE_ROOT_CA": str(directory / "missing.crt"),
                    "PODWAVE_CA_BUNDLE": str(directory / "combined.pem"),
                },
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn("should-not-run", result.stdout)
            self.assertIn("HomeCompute root CA is unreadable", result.stderr)


if __name__ == "__main__":
    unittest.main()
