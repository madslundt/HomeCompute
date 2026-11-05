#!/usr/bin/env python3
"""Contract checks for secret-safe Hermes LiteLLM key delivery."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch
import builtins

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "provision_hermes_litellm_key", ROOT / "scripts/provision-hermes-litellm-key.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProvisionHermesLiteLLMKeyTest(unittest.TestCase):
    def test_scoped_key_streams_only_to_private_guest_file(self) -> None:
        secret = "sk-private-test-fixture"
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if len(calls) == 1:
                return subprocess.CompletedProcess(
                    args, 0,
                    json.dumps({"key": secret, "models": ["automation-moe"],
                                "user_id": "hermes-agent", "expires": None}), ""
                )
            return subprocess.CompletedProcess(args, 0, "key-installed\n", "")

        with patch.object(MODULE.subprocess, "run", side_effect=fake_run), \
             patch.object(builtins, "print") as mocked_print:
            MODULE.run()

        self.assertEqual(len(calls), 2)
        self.assertIn("key/generate", calls[0][1]["input"])
        self.assertIn("automation-moe", calls[0][1]["input"])
        self.assertNotIn(secret, " ".join(calls[0][0]))
        self.assertEqual(calls[1][1]["input"], secret + "\n")
        self.assertNotIn(secret, str(calls[1][0]))
        self.assertNotIn(secret, mocked_print.call_args.args[0])

    def test_rejects_broader_model_scope_before_guest_delivery(self) -> None:
        calls: list[list[str]] = []

        def fake_run(args, **kwargs):
            calls.append(args)
            return subprocess.CompletedProcess(
                args, 0,
                json.dumps({"key": "sk-private-test-fixture", "models": ["automation-moe", "home"],
                            "user_id": "hermes-agent", "expires": None}), ""
            )

        with patch.object(MODULE.subprocess, "run", side_effect=fake_run):
            with self.assertRaisesRegex(RuntimeError, "unexpected scope"):
                MODULE.run()
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
