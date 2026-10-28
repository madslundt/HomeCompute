"""Finite native roles, revocation preservation and guarded installer behavior."""
from __future__ import annotations
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import openclaw_integration_policy as policy
from openclaw_team_policy import finite, ROSTER, TeamRefused
from openclaw_integrations import TOOLS as DEFINITIONS
SPEC = importlib.util.spec_from_file_location('integration_setup', ROOT / 'scripts/setup-openclaw-integration-tools.py')
installer = importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(installer)


def fixture():
    return {'tools': {'allow': ['read'], 'deny': [], 'fs': {'workspaceOnly': True}, 'elevated': {'enabled': False},
                      'subagents': {'tools': {'allow': ['read'], 'deny': []}}},
            'agents': {'entries': {role: {'workspace': '/private/' + role, 'skills': [],
                        'tools': {'allow': ['read'], 'deny': ['exec']}} for role in ROSTER}},
            'models': {'private': 'unchanged'}, 'mcp': {'private': 'unchanged'}, 'browser': {'enabled': True}}


def candidate(before, enable=False):
    return policy.candidate(before, finite=finite, refused=TeamRefused, enable=enable)


class IntegrationPolicyTest(unittest.TestCase):
    def test_roles_match_tools_and_exclude_coordinator_and_family(self):
        before = fixture(); after = candidate(before, True)
        self.assertEqual(before, fixture())
        self.assertEqual(policy.TOOLS, {'homecompute__' + t['name'] for t in DEFINITIONS})
        for role, entry in after['agents']['entries'].items():
            selected = policy.ROLE_TOOLS.get(role, set())
            self.assertEqual(set(entry['tools']['allow']) & policy.TOOLS, selected)
            self.assertEqual(set(entry['tools']['deny']) & policy.TOOLS, policy.TOOLS - selected)
            self.assertIn('exec', entry['tools']['deny'])
            self.assertEqual(entry['workspace'], before['agents']['entries'][role]['workspace'])
        self.assertEqual(after, candidate(after))
        for key in ('models', 'mcp', 'browser'): self.assertEqual(after[key], before[key])

    def test_scoped_upgrade_preserves_other_existing_roles_and_denies_new_tools(self):
        before = fixture()
        before['agents']['entries']['other-specialist'] = {'workspace': '/private/other',
            'tools': {'allow': ['read'], 'deny': ['exec']}}
        after = candidate(before, True)
        other = after['agents']['entries']['other-specialist']
        self.assertEqual(other['workspace'], '/private/other')
        self.assertEqual(other['tools']['allow'], ['read'])
        self.assertTrue(policy.TOOLS <= set(other['tools']['deny']))
        self.assertEqual(after, candidate(after))

    def test_initial_grant_requires_review_and_partial_revocations_stay_denied(self):
        with self.assertRaises(TeamRefused): candidate(fixture())
        full = candidate(fixture(), True)
        for location in ('role', 'global', 'child'):
            changed = copy.deepcopy(full)
            ceiling = (changed['agents']['entries']['engineer']['tools'] if location == 'role' else
                       changed['tools'] if location == 'global' else changed['tools']['subagents']['tools'])
            ceiling['allow'].remove(policy.PREFIX + 'docs_query')
            with self.subTest(location=location), self.assertRaises(TeamRefused): candidate(changed, True)
        denied = fixture(); denied['agents']['entries']['personal']['tools']['deny'].append(policy.PREFIX + 'photo_search')
        with self.assertRaises(TeamRefused): candidate(denied, True)
        parent = fixture(); parent['tools']['subagents']['tools']['deny'].append(policy.PREFIX + 'docs_query')
        with self.assertRaises(TeamRefused): candidate(parent, True)

    def test_installer_validates_fences_backs_up_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory).resolve() / 'openclaw.json'
            config.write_text(json.dumps(fixture())); config.chmod(0o600)
            raw = config.read_bytes(); calls = []
            def runner(argv, **kwargs):
                calls.append(argv)
                if '--dry-run' not in argv:
                    value = json.loads(config.read_bytes())
                    for change in json.loads(argv[argv.index('--batch-json') + 1]): value[change['path']] = change['value']
                    config.write_text(json.dumps(value))
                return subprocess.CompletedProcess(argv, 0)
            dry = installer.setup(config, runner=runner)
            self.assertEqual(config.read_bytes(), raw)
            with self.assertRaises(ValueError): installer.setup(config, apply=True, expected_config_sha256='stale', runner=runner)
            result = installer.setup(config, apply=True, expected_config_sha256=dry['source_config_sha256'], runner=runner)
            self.assertTrue(result['applied'])
            backups = list(config.parent.glob('integration-setup-backups/*/before.json'))
            self.assertEqual(len(backups), 1); self.assertEqual(backups[0].read_bytes(), raw)
            self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
            again = installer.setup(config, runner=runner)
            self.assertFalse(again['changed'])
            self.assertEqual(hashlib.sha256(raw).hexdigest(), dry['source_config_sha256'])


if __name__ == '__main__': unittest.main()
