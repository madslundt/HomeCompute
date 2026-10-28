#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import native_llm_activity as activity
spec = importlib.util.spec_from_file_location('native_queue_setup', ROOT / 'scripts/setup-openclaw-llm-queue.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class NativeActivityTest(unittest.TestCase):
    def test_fixed_metadata_command_and_authenticated_identity(self):
        runner = Mock(return_value=SimpleNamespace(stdout=json.dumps({'active_turns': 2, 'observed_at': 100}).encode()))
        self.assertEqual(activity.collect(runner, lambda: 103)['active_turns'], 2)
        args, kwargs = runner.call_args
        self.assertIn('homecompute-safety.llm.activity --json --timeout 5000', args[0][-1])
        self.assertIn('/var/lib/homecompute-openclaw/.ssh/id_ed25519', args[0])
        self.assertEqual(kwargs['timeout'], 8)
        self.assertTrue(kwargs['check'])
        self.assertNotIn('shell', kwargs)

    def test_stale_missing_negative_boolean_or_foreign_fields_fail_closed(self):
        for value in ({'active_turns': 0, 'observed_at': 70}, {'active_turns': True, 'observed_at': 100},
                      {'active_turns': -1, 'observed_at': 100}, {'active_turns': 0},
                      {'active_turns': 0, 'observed_at': 100, 'payload': 'private'}):
            runner = Mock(return_value=SimpleNamespace(stdout=json.dumps(value).encode()))
            with self.assertRaises(ValueError):
                activity.collect(runner, lambda: 103)

    def test_safety_overlay_preserves_existing_policy_and_is_idempotent(self):
        source = b'import existing from "policy";\n' + setup.SAFETY_ANCHOR.encode()
        patched = setup.safety_overlay(source)
        self.assertIn(b'import existing from "policy";', patched)
        self.assertEqual(setup.safety_overlay(patched), patched)
        with self.assertRaises(ValueError):
            setup.safety_overlay(b'unknown shape')

    def test_unqualified_official_runtime_cannot_be_patched(self):
        with self.assertRaises(ValueError):
            setup.overlay(setup.ANCHOR.encode())


if __name__ == '__main__':
    unittest.main()
