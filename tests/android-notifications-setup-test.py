"""Source and credential-boundary checks for the Android n8n installer."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('android_notifications_setup', ROOT / 'scripts/setup-android-notifications.py')
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)


class WorkflowTest(unittest.TestCase):
    def test_exact_source_workflow_is_inert_filtered_and_has_no_delivery_node(self):
        flow = setup.load_template()
        self.assertFalse(flow['active'])
        self.assertEqual({node['type'] for node in flow['nodes']},
                         {'n8n-nodes-base.webhook', 'n8n-nodes-base.code'})
        webhook = next(node for node in flow['nodes'] if node['type'] == 'n8n-nodes-base.webhook')
        self.assertEqual(webhook['parameters']['authentication'], 'headerAuth')
        self.assertEqual(webhook['parameters']['responseMode'], 'onReceived')

    def test_template_modification_is_refused(self):
        flow = json.loads(setup.TEMPLATE.read_text())
        flow['active'] = True
        with tempfile.TemporaryDirectory() as temp:
            candidate = Path(temp) / 'workflow.json'
            candidate.write_text(json.dumps(flow))
            with patch.object(setup, 'TEMPLATE', candidate):
                with self.assertRaisesRegex(ValueError, 'exact reviewed'):
                    setup.load_template()

    def test_secret_must_be_root_owned_private_and_well_formed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'secret'
            path.write_text('a' * 48 + '\n')
            path.chmod(0o600)
            with patch.object(setup, 'SECRET', path):
                self.assertEqual(setup.read_secret(), 'a' * 48)
                path.chmod(0o644)
                with self.assertRaisesRegex(ValueError, 'private'):
                    setup.read_secret()


if __name__ == '__main__':
    unittest.main()
