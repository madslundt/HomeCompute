#!/usr/bin/env python3
"""Exercise guarded Hermes ChatGPT profile reconciliation."""
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SETUP = runpy.run_path(str(ROOT / 'scripts/setup-hermes-chatgpt-profiles.py'))
DESIRED = SETUP['roster']()
CLI = ['nemohermes', 'hermes-personal-v2']


class FakeHermes:
    def __init__(self):
        self.states = {item['name']: 'absent' for item in DESIRED['profiles']}
        self.operations = []

    def __call__(self, argv, data=None):
        self.operations.append((argv, data))
        if argv[2:4] == ['exec', '--stdin'] and argv[-2:] == ['-c', SETUP['INSPECT']]:
            return json.dumps({name: {'state': state} for name, state in self.states.items()})
        if argv[2:4] == ['exec', '--'] and argv[4] == '/opt/hermes/.venv/bin/hermes':
            self.states[argv[7]] = 'created'
            return b''
        if argv[2:4] == ['exec', '--stdin'] and argv[-2:] == ['-c', SETUP['PATCH']]:
            payload = json.loads(data)
            for item in payload['profiles']:
                self.states[item['name']] = 'managed'
            return b''
        raise AssertionError(f'unexpected native command: {argv}')


class HermesChatGPTProfileTests(unittest.TestCase):
    def test_browser_reconnect_updates_existing_owner_profiles_and_keeps_their_settings(self):
        with patch.dict(sys.modules, {'yaml': type('Yaml', (), {
            'safe_load': staticmethod(json.loads),
            'safe_dump': staticmethod(json.dumps),
        })}):
            browser = runpy.run_path(str(ROOT / 'deploy/hermes/configure-browser.py'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / '.hermes'
            profiles = [item['name'] for item in DESIRED['profiles']]
            named = [root / 'profiles' / name for name in profiles[:2]]
            for home in [root, *named]:
                home.mkdir(parents=True, exist_ok=True)
                (home / 'config.yaml').write_text(json.dumps({
                    'model': home.name, 'browser': {'backend': 'off', 'timeout': 20},
                }))
                (home / '.env').write_text('MODEL_KEY=synthetic\n')
                (home / '.env').chmod(0o600)

            conflict = named[-1] / '.env'
            conflict.write_text('BROWSER_CDP_URL=ws://other.example/\n')
            with self.assertRaisesRegex(ValueError, 'endpoint differs'):
                browser['configure'](root, profiles)
            self.assertEqual((root / '.env').read_text(), 'MODEL_KEY=synthetic\n')
            conflict.write_text('MODEL_KEY=synthetic\n')

            endpoint = browser['ENDPOINT']
            self.assertEqual(browser['configure'](root, profiles), ['default', *profiles[:2]])
            for home in [root, *named]:
                self.assertEqual(json.loads((home / 'config.yaml').read_text()), {
                    'model': home.name, 'browser': {'timeout': 20, 'cdp_url': endpoint, 'allow_private_urls': False},
                })
                self.assertEqual((home / '.env').read_text(), f'MODEL_KEY=synthetic\nBROWSER_CDP_URL={endpoint}\n')
                self.assertEqual((home / '.env').stat().st_mode & 0o777, 0o600)
            with patch('os.replace', side_effect=AssertionError('already correct')):
                browser['configure'](root, profiles)
            self.assertFalse((root / 'profiles' / profiles[2]).exists())

    def test_reviewed_plan_required_and_profile_plan_is_bound_to_state(self):
        fake = FakeHermes()
        before = SETUP['reconcile'](fake, CLI, DESIRED, {}, 'plan')
        digest = SETUP['plan_digest']('a' * 40, DESIRED, before)
        self.assertEqual(set(x['state'] for x in before.values()), {'absent'})
        with self.assertRaisesRegex(ValueError, 'reviewed --expected-plan-sha256 required'):
            SETUP['reconcile'](fake, CLI, DESIRED, {}, 'apply', revision='a' * 40)
        with self.assertRaisesRegex(ValueError, 'state changed'):
            SETUP['reconcile'](fake, CLI, DESIRED, {}, 'apply', '0' * 64, 'a' * 40)
        self.assertEqual(fake.operations[-1][0][2:4], ['exec', '--stdin'])
        self.assertEqual(digest, SETUP['plan_digest']('a' * 40, DESIRED, before))
        self.assertEqual(fake.states, {item['name']: 'absent' for item in DESIRED['profiles']})

    def test_apply_and_resume_are_idempotent(self):
        fake = FakeHermes()
        before = SETUP['reconcile'](fake, CLI, DESIRED, {}, 'plan')
        digest = SETUP['plan_digest']('b' * 40, DESIRED, before)
        result = SETUP['reconcile'](fake, CLI, DESIRED,
                                   {item['name']: 'sk-test' for item in DESIRED['profiles']},
                                   'apply', digest, 'b' * 40)
        self.assertEqual({name: row['state'] for name, row in result.items()},
                         dict.fromkeys(fake.states, 'managed'))
        creates = [row for row in fake.operations if row[0][2:4] == ['exec', '--']]
        self.assertEqual(len(creates), 3)
        after = SETUP['reconcile'](fake, CLI, DESIRED, {}, 'plan')
        result = SETUP['reconcile'](fake, CLI, DESIRED, {}, 'apply',
                                   SETUP['plan_digest']('b' * 40, DESIRED, after), 'b' * 40)
        self.assertEqual({name: row['state'] for name, row in result.items()},
                         dict.fromkeys(fake.states, 'managed'))
        creates_after = [row for row in fake.operations if row[0][2:4] == ['exec', '--']]
        self.assertEqual(len(creates_after), 3)

    def test_existing_profile_conflict_blocks_every_change(self):
        fake = FakeHermes()
        fake.states['chatgpt-terra'] = 'conflict'
        with self.assertRaisesRegex(ValueError, 'differs'):
            SETUP['reconcile'](fake, CLI, DESIRED, {}, 'apply', 'a' * 64, 'c' * 40)
        self.assertFalse(any(row[0][2:4] == ['exec', '--'] for row in fake.operations))

    def test_patch_writes_profile_name_and_readback_verifies_clone(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / '.hermes'
            profile = root / 'profiles' / 'chatgpt-sol'
            profile.mkdir(parents=True)
            owner = {'mcp_servers': ['homecompute'], 'memory': {'enabled': True},
                     'toolsets': ['default'], 'platform_toolsets': ['homeassistant']}
            (root / 'config.yaml').write_text(json.dumps(owner))
            (profile / 'config.yaml').write_text(json.dumps({
                **owner, 'model': {'default': 'automation-moe', 'provider': 'custom'}}))
            (profile / '.env').write_text('OWNER_CONTEXT=retained\n')
            stub = Path(directory) / 'yaml.py'
            stub.write_text('import json\ndef safe_load(value): return json.loads(value)\ndef safe_dump(value, sort_keys=False): return json.dumps(value)\n')
            environment = {**os.environ, 'PYTHONPATH': str(directory)}
            patch = SETUP['PATCH'].replace("pathlib.Path('/sandbox/.hermes')", f'pathlib.Path({str(root)!r})')
            payload = {'endpoint': DESIRED['endpoint'], 'profiles': [DESIRED['profiles'][0]],
                       'keys': {'chatgpt-sol': 'sk-synthetic-sol'}}
            subprocess.run([sys.executable, '-c', patch], input=json.dumps(payload), text=True,
                           capture_output=True, check=True, env=environment)
            marker = json.loads((profile / '.homecompute-chatgpt-profile.json').read_text())
            self.assertEqual(marker['name'], 'chatgpt-sol')
            self.assertIn('OWNER_CONTEXT=retained', (profile / '.env').read_text())
            inspect = SETUP['INSPECT'].replace("pathlib.Path('/sandbox/.hermes')", f'pathlib.Path({str(root)!r})')
            result = subprocess.run([sys.executable, '-c', inspect], input=json.dumps(DESIRED), text=True,
                                    capture_output=True, check=True, env=environment)
            state = json.loads(result.stdout)['chatgpt-sol']
            self.assertEqual(state['state'], 'managed')
            self.assertTrue(all(state['checks'].values()))

    def test_repairable_partial_profile_is_updated_without_recreating_it(self):
        fake = FakeHermes()
        fake.states['chatgpt-sol'] = 'repairable'
        before = SETUP['reconcile'](fake, CLI, DESIRED, {}, 'plan')
        digest = SETUP['plan_digest']('d' * 40, DESIRED, before)
        result = SETUP['reconcile'](fake, CLI, DESIRED,
                                   {item['name']: 'sk-test' for item in DESIRED['profiles']},
                                   'apply', digest, 'd' * 40)
        creates = [row for row in fake.operations if row[0][2:4] == ['exec', '--']]
        self.assertEqual([row[0][7] for row in creates], ['chatgpt-terra', 'chatgpt-luna'])
        self.assertEqual(result['chatgpt-sol']['state'], 'managed')


if __name__ == '__main__':
    unittest.main()
