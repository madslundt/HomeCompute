"""Guard the source overlay's exact-input and idempotence contracts."""
import hashlib
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('child_result_setup', ROOT / 'scripts/setup-openclaw-child-results.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class ChildResultOverlayTests(unittest.TestCase):
    def test_exact_source_only_and_reapplication_is_idempotent(self):
        source = ('\n'.join(before for before, _ in MOD.CHANGES) + '\n').encode()
        with patch.object(MOD, 'BASE_SHA256', hashlib.sha256(source).hexdigest()):
            after = MOD.overlay(source)
            self.assertEqual(MOD.overlay(after), after)
            for _, replacement in MOD.CHANGES:
                self.assertEqual(after.count(replacement.encode()), 1)
            for unknown in (source + b'changed', after + b'changed', source.replace(b'resultText', b'other')):
                with self.subTest(unknown=unknown), self.assertRaises(ValueError):
                    MOD.overlay(unknown)


if __name__ == '__main__':
    unittest.main()
