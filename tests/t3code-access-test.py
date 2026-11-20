#!/usr/bin/env python3
"""Verify the T3 Code route guard protects existing Serve and Funnel routes."""

import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("t3code_access", ROOT / "scripts/setup-t3code-access.py")
access = importlib.util.module_from_spec(spec)
spec.loader.exec_module(access)


class AccessTests(unittest.TestCase):
    def test_empty_port_and_owned_route(self):
        self.assertEqual(access.route_state({}, access.URL, access.BACKEND), "absent")
        config = {
            "TCP": {str(access.URL.port): {"HTTPS": True}},
            "Web": {access.URL.netloc: {"Handlers": {"/": {"Proxy": access.BACKEND}}}},
            "AllowFunnel": {f"{access.URL.hostname}:443": True},
        }
        self.assertEqual(access.route_state(config, access.URL, access.BACKEND), "active")

    def test_refuses_another_service_or_funnel_on_same_port(self):
        for config in (
            {"TCP": {str(access.URL.port): {"HTTPS": True}}},
            {"AllowFunnel": {access.URL.netloc: True}},
            {"Foreground": {"session": {"TCP": {str(access.URL.port): {"HTTPS": True}}}}},
        ):
            with self.subTest(config=config), self.assertRaises(ValueError):
                access.route_state(config, access.URL, access.BACKEND)


if __name__ == "__main__":
    unittest.main()
