#!/usr/bin/env python3
"""Contract checks for model-only OpenBot ChatGPT key delivery."""
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
    'provision_openbot_chatgpt_key', ROOT / 'scripts/provision-openbot-chatgpt-key.py'
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ProvisionOpenBotChatGPTKeyTest(unittest.TestCase):
    def test_receipt_requires_exact_models_expiry_and_mcp_deny(self) -> None:
        valid = {
            'key': 'sk-private-test-fixture',
            'models': ['chatgpt-sol-latest', 'chatgpt-terra-latest', 'chatgpt-luna-latest'],
            'user_id': 'openbot-chatgpt-models',
            'expires': None,
            'mcp_servers': ['no-mcp-servers'],
        }
        self.assertTrue(MODULE.valid_receipt(valid))
        for field, value in (
            ('models', ['chatgpt-sol-latest', 'automation-moe']),
            ('expires', '2030-01-01'),
            ('mcp_servers', ['aula-read']),
            ('user_id', 'openbot-agent'),
        ):
            with self.subTest(field=field):
                self.assertFalse(MODULE.valid_receipt({**valid, field: value}))

    def test_key_streams_only_to_private_guest_file(self) -> None:
        secret = 'sk-private-test-fixture'
        revision = 'a' * 40
        calls: list[tuple[list[str], dict[str, object]]] = []

        def fake_run(args, **kwargs):
            calls.append((args, kwargs))
            if args[:4] == ['git', '-C', str(MODULE.ROOT), 'rev-parse']:
                return subprocess.CompletedProcess(args, 0, revision + '\n', '')
            if args[:4] == ['git', '-C', str(MODULE.ROOT), 'status']:
                return subprocess.CompletedProcess(args, 0, '', '')
            if args[0] == 'ssh' and 'systemctl --quiet is-active' in args[-1]:
                return subprocess.CompletedProcess(args, 0, '', '')
            if args[:2] == ['ssh', 'home-core'] and 'docker exec' in args[-1]:
                return subprocess.CompletedProcess(args, 0, json.dumps({
                    'key': secret,
                    'models': MODULE.MODELS,
                    'user_id': MODULE.OWNER,
                    'expires': None,
                    'mcp_servers': ['no-mcp-servers'],
                }), '')
            return subprocess.CompletedProcess(args, 0, 'key-installed\n', '')

        with patch.object(MODULE.GUEST_KEY.__class__, 'is_file', return_value=True), \
             patch.object(MODULE.subprocess, 'run', side_effect=fake_run), \
             patch.object(builtins, 'print') as mocked_print:
            MODULE.run()

        self.assertEqual(len(calls), 6)
        self.assertIn('chatgpt-sol-latest', calls[4][1]['input'])
        self.assertIn("'no-mcp-servers'", calls[4][1]['input'])
        self.assertNotIn(secret, ' '.join(calls[4][0]))
        self.assertEqual(calls[5][1]['input'], secret + '\n')
        self.assertNotIn(secret, str(calls[5][0]))
        receiver = calls[5][0][-1]
        self.assertIn('O_EXCL', receiver)
        self.assertIn('O_NOFOLLOW', receiver)
        self.assertIn('stat.S_IMODE(info.st_mode) == 0o700', receiver)
        self.assertNotIn(secret, receiver)
        self.assertNotIn(secret, mocked_print.call_args.args[0])


if __name__ == '__main__':
    unittest.main()
