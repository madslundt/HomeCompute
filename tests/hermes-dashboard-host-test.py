import importlib.util
import pathlib
import unittest

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


if __name__ == '__main__':
    unittest.main()
