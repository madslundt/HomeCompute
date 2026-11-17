import importlib.util
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('dashboard_host', ROOT / 'scripts/setup-hermes-dashboard-host.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class DashboardHostTests(unittest.TestCase):
    def test_adds_tailnet_hostname_without_removing_existing_settings(self):
        before = b'KEEP=one\n'
        after = module.updated_env(before)
        self.assertIn(b'KEEP=one\n', after)
        self.assertIn(b'_NEMOCLAW_HERMES_DASHBOARD_EXTERNAL_HOST=home-core.tail479ad.ts.net\n', after)

    def test_replaces_only_a_reviewed_previous_value(self):
        before = b'KEEP=one\n_NEMOCLAW_HERMES_DASHBOARD_EXTERNAL_HOST=agents.home.arpa\nTAIL=two\n'
        after = module.updated_env(before)
        self.assertEqual(after.count(b'_NEMOCLAW_HERMES_DASHBOARD_EXTERNAL_HOST='), 1)
        self.assertTrue(after.startswith(b'KEEP=one\n'))
        self.assertTrue(after.endswith(b'TAIL=two\n'))

    def test_is_idempotent(self):
        once = module.updated_env(b'KEEP=one\n')
        self.assertEqual(module.updated_env(once), once)

    def test_rejects_duplicate_or_unreviewed_host_values(self):
        for raw in (b'_NEMOCLAW_HERMES_DASHBOARD_EXTERNAL_HOST=a.home\n',
                    b'_NEMOCLAW_HERMES_DASHBOARD_EXTERNAL_HOST=\n'
                    b'_NEMOCLAW_HERMES_DASHBOARD_EXTERNAL_HOST=\n'):
            with self.assertRaises(ValueError):
                module.updated_env(raw)

    def test_recreated_sandbox_requires_and_preserves_prior_rollback_evidence(self):
        with tempfile.TemporaryDirectory() as example:
            state = pathlib.Path(example)
            before_env = b'KEEP=one\n'
            before_sha = module.digest(before_env)
            before = state / f'before-{before_sha}.env'
            before.write_bytes(before_env)
            receipt_path = state / 'receipt.json'
            receipt = {
                'state': 'applied', 'sandbox': module.PIN['previous_owner_sandbox'],
                'external_host': module.TARGET, 'local_hostname': module.PIN['local_hostname'],
                'applied_at': 1767312000, 'before_sha256': before_sha, 'backup': str(before),
            }
            receipt_raw = json.dumps(receipt).encode()
            receipt_path.write_bytes(receipt_raw)
            with patch.object(module, 'STATE', state), patch.object(module, 'RECEIPT', receipt_path):
                with self.assertRaisesRegex(ValueError, 'newer owner sandbox'):
                    module.archive_recreated_receipt('2026-01-01T00:00:00Z')
                recreated = '2026-01-03T00:00:00.123456789Z'
                receipt_path.write_text(json.dumps({**receipt, 'sandbox': 'unrelated'}))
                with self.assertRaisesRegex(ValueError, 'prior applied hostname receipt'):
                    module.archive_recreated_receipt(recreated)
                receipt_path.write_bytes(receipt_raw)
                archived = module.archive_recreated_receipt(recreated)
                self.assertEqual(archived.read_bytes(), receipt_raw)
                self.assertEqual(receipt_path.read_bytes(), receipt_raw)
                self.assertEqual(module.archive_recreated_receipt(recreated), archived)
                before.write_bytes(b'changed\n')
                with self.assertRaisesRegex(ValueError, 'preimage must verify'):
                    module.archive_recreated_receipt(recreated)


if __name__ == '__main__':
    unittest.main()
