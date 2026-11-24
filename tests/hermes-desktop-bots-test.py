#!/usr/bin/env python3
"""Check isolation and the import lifecycle with synthetic credentials and profiles."""

import json
from pathlib import Path
import runpy
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = runpy.run_path(str(ROOT / 'deploy/hermes/bots/install.py'))
ROSTER = runpy.run_path(str(ROOT / 'scripts/setup-hermes-desktop-bots.py'))['desired']


class DesktopBotTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profiles = Path(temporary.name) / 'profiles'
        self.owner = self.profiles.parent / 'SOUL.md'
        self.owner.write_text('synthetic owner instructions')
        self.keys = {'home-planner': 'sk-local-synthetic', 'researcher': 'sk-sol-synthetic'}
        self.imports = []
        self.deleted = set()
        self.fail_on = None

    def native_import(self, archive, *, name):
        if name == self.fail_on:
            raise ValueError('interrupted before import')
        with tarfile.open(archive) as package:
            self.assertEqual(set(package.getnames()), {f'{name}/{item}' for item in (
                'config.yaml', 'profile.yaml', '.env', 'SOUL.md', '.no-bundled-skills')})
            self.profiles.mkdir(exist_ok=True)
            package.extractall(self.profiles, filter='data')
        self.imports.append(name)

    def read(self, expected=None):
        payload = ROSTER()
        if expected is not None:
            payload.update(keys=self.keys, expected=expected)
        return RUNTIME['reconcile'](self.profiles, payload, ['cli', 'api_server'], json.loads,
                                    self.native_import, lambda path: path.name in self.deleted)

    def test_plan_then_apply_starts_empty_profiles_with_distinct_models(self):
        before = self.read()
        self.assertEqual(before, dict.fromkeys(self.keys, 'absent'))
        self.assertFalse(self.profiles.exists())
        after = self.read(before)
        self.assertEqual(after, dict.fromkeys(self.keys, 'managed'))
        self.assertEqual(self.read(after), after)
        self.assertEqual(self.imports, list(self.keys))
        for name, model in {'home-planner': 'automation-moe', 'researcher': 'chatgpt-sol-latest'}.items():
            config = json.loads((self.profiles / name / 'config.yaml').read_bytes())
            self.assertEqual(config['model']['default'], model)
            self.assertEqual(config['model']['key_env'], RUNTIME['KEY'])
            self.assertEqual(config['platform_toolsets'], {'cli': ['memory'], 'api_server': ['memory']})
            self.assertFalse(config['mcp_servers'])
            self.assertFalse(config['fallback_providers'])
            self.assertEqual(config['agent']['coding_context'], 'off')
            self.assertIn('bighelp_workflows', config['agent']['disabled_toolsets'])
            self.assertFalse(config['plugins']['enabled'])
        self.assertEqual(self.owner.read_text(), 'synthetic owner instructions')

    def test_interrupted_apply_can_resume_with_fresh_plan(self):
        before = self.read()
        self.fail_on = 'researcher'
        with self.assertRaisesRegex(ValueError, 'interrupted'):
            self.read(before)
        self.fail_on = None
        with self.assertRaisesRegex(ValueError, 'state changed'):
            self.read(before)
        self.assertEqual(self.read(self.read()), dict.fromkeys(self.keys, 'managed'))
        self.assertEqual(self.imports, list(self.keys))

    def test_existing_or_retired_profile_is_preserved(self):
        self.deleted.add('researcher')
        with self.assertRaisesRegex(ValueError, 'existing researcher requires review'):
            self.read()
        self.deleted.clear()
        (self.profiles / 'researcher').mkdir(parents=True)
        with self.assertRaisesRegex(ValueError, 'existing researcher requires review'):
            self.read()
        self.assertEqual(self.imports, [])

    def test_changed_profile_credential_stops_apply(self):
        applied = self.read(self.read())
        self.keys['researcher'] = 'sk-different-synthetic'
        with self.assertRaisesRegex(ValueError, 'existing researcher requires review'):
            self.read(applied)
        self.assertEqual(self.imports, list(self.keys))


if __name__ == '__main__':
    unittest.main()
