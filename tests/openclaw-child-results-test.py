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
    def test_candidate_profile_is_explicit_and_unknown_versions_refuse(self):
        runtime,before,after=MOD.runtime_profile('2026.9.2')
        self.assertEqual(runtime.name,'subagent-announce-output-Cy2Rx3_K.js')
        self.assertEqual(before,'71523669a7f466e8c3a240f7d0a8485a8fa8882f5920add16176685cb5d34dc1')
        self.assertEqual(after,'ed09c0b16e452ec159085ef35b3475cd20c15b6ffbf8620c4b24517588c86bad')
        with self.assertRaises(ValueError):MOD.runtime_profile('latest')
        with self.assertRaises(ValueError):MOD.overlay(b'unreviewed','2026.9.2')

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
