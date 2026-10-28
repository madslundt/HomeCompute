"""Pinned overlay refusal, idempotence, and nonsecret source behavior."""
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('yield_ack_setup', ROOT / 'scripts/setup-openclaw-yield-acknowledgment.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class YieldAcknowledgmentOverlayTests(unittest.TestCase):
    def test_exact_source_is_patched_once_and_reapplication_is_idempotent(self):
        source = ('function nativeTerminal() {\n' + MOD.BEFORE + '}\n').encode()
        with patch.object(MOD, 'BASE_SHA256', hashlib.sha256(source).hexdigest()):
            after = MOD.overlay(source)
            self.assertEqual(after.count(MOD.AFTER.encode()), 1)
            self.assertEqual(after.replace(MOD.AFTER.encode(), MOD.BEFORE.encode()), source)
            self.assertEqual(MOD.overlay(after), after)

    def test_unknown_upstream_or_modified_overlay_is_refused(self):
        source = ('function nativeTerminal() {\n' + MOD.BEFORE + '}\n').encode()
        with patch.object(MOD, 'BASE_SHA256', hashlib.sha256(source).hexdigest()):
            for unknown in (source + b'changed', source.replace(b'suppressToolErrorWarnings', b'unknown'),
                            MOD.overlay(source) + b'changed', source + MOD.BEFORE.encode()):
                with self.subTest(unknown=unknown), self.assertRaises(ValueError):
                    MOD.overlay(unknown)


if __name__ == '__main__':
    unittest.main()
