#!/usr/bin/env python3
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('prepare_nemo', ROOT / 'scripts/prepare-openclaw-nemoclaw.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def managed():
    return {'models': {'providers': {'inference': {'baseUrl': 'https://inference.local/v1', 'apiKey': 'unused'}}},
            'gateway': {'mode': 'local', 'port': 18789, 'auth': {'token': 'synthetic-owner-token'}},
            'plugins': {'entries': {'nemoclaw': {'enabled': True}}, 'installs': {'nemoclaw': {'source': 'path'}}},
            'tools': {'alsoAllow': ['bundle-mcp']},
            'proxy': {'enabled': True, 'proxyUrl': 'http://10.200.0.1:3128'}}


class NemoPreparationTests(unittest.TestCase):
    def setUp(self):
        self.overlay = json.loads((ROOT / 'config/openclaw-nemoclaw.json').read_text())

    def test_preserves_native_ownership_and_removes_mcp_grant(self):
        base = managed()
        result = module.prepare(base, self.overlay)
        self.assertEqual(result['gateway']['auth'], base['gateway']['auth'])
        self.assertEqual(result['proxy'], base['proxy'])
        self.assertEqual(result['plugins']['installs'], base['plugins']['installs'])
        self.assertNotIn('alsoAllow', result['tools'])
        self.assertEqual(result['models']['providers']['inference']['apiKey'], 'unused')
        self.assertFalse(result['plugins']['entries']['homecompute-broker']['enabled'])
        self.assertIn('group:runtime', result['tools']['deny'])

    def test_rejects_raw_model_credentials_or_direct_upstream(self):
        for mutation in [{'apiKey': 'synthetic-real-secret'}, {'baseUrl': 'https://ai.home.arpa/v1'}]:
            base = managed()
            base['models']['providers']['inference'].update(mutation)
            with self.assertRaises(ValueError):
                module.prepare(base, self.overlay)

    def test_explicit_managed_port_is_preserved_and_mismatches_refused(self):
        base = managed()
        base['gateway']['port'] = 18791
        with self.assertRaises(ValueError):
            module.prepare(base, self.overlay)
        self.assertEqual(module.prepare(base, self.overlay, gateway_port=18791)['gateway']['port'], 18791)
        with self.assertRaises(ValueError):
            module.prepare(base, self.overlay, gateway_port=443)

    def test_rejects_other_providers_and_missing_managed_plugin(self):
        base = managed()
        base['models']['providers']['openai'] = {'apiKey': 'synthetic'}
        with self.assertRaises(ValueError):
            module.prepare(base, self.overlay)
        base = managed()
        base['plugins']['entries'].clear()
        with self.assertRaises(ValueError):
            module.prepare(base, self.overlay)

    def test_overlay_cannot_replace_gateway_auth_or_proxy(self):
        for extra in [{'gateway': {'auth': {'token': 'replacement'}}}, {'proxy': {'enabled': False}}]:
            overlay = module.merge(self.overlay, extra)
            with self.assertRaises(ValueError):
                module.prepare(managed(), overlay)


if __name__ == '__main__':
    unittest.main()
