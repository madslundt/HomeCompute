"""Reviewed calendar upgrade, role authority, skill installation and bundle boundaries."""
import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('calendar_team_fixtures', ROOT / 'tests/openclaw-team-test.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class CalendarTeamTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TeamSetupTest()
        self.fixture.setUp()
        self.setup = fixtures.setup
        self.team = self.fixture.team
        self.installed = fixtures.reviewed_candidate(self.fixture.before, self.team)
        self.legacy = copy.deepcopy(self.installed)
        calendar = self.setup.CALENDAR_TOOLS
        for entry in self.legacy['agents']['entries'].values():
            for key in ('allow', 'deny'):
                entry['tools'][key] = sorted(set(entry['tools'][key]) - calendar)
            entry['skills'] = [name for name in entry['skills'] if name != 'google-calendar']
        for key in ('allow', 'alsoAllow'):
            if key in self.legacy['tools']:
                self.legacy['tools'][key] = sorted(set(self.legacy['tools'][key]) - calendar)
        self.legacy['tools']['subagents']['tools']['allow'] = sorted(
            set(self.legacy['tools']['subagents']['tools']['allow']) - calendar)

    def candidate(self, *args, **kwargs):
        # Calendar tests retain the separately reviewed optional mail fixture.
        return self.setup.candidate(*args, enable_mail_upgrade=True, **kwargs)

    def tearDown(self):
        self.fixture.tearDown()

    def test_upgrade_requires_review_and_preserves_other_roles_and_protected_config(self):
        with self.assertRaisesRegex(self.setup.TeamRefused, 'reviewed-calendar-upgrade-required'):
            self.candidate(self.legacy, self.team)
        after = self.candidate(self.legacy, self.team, enable_calendar_upgrade=True)
        self.assertEqual(len(self.setup.CALENDAR_TOOLS), 6)
        for role, entry in after['agents']['entries'].items():
            permitted = set(entry['tools']['allow']) - set(entry['tools']['deny'])
            expected = self.setup.CALENDAR_TOOLS if role in {'main', 'personal'} else self.setup.CALENDAR_READ if role == 'travel' else set()
            self.assertEqual(permitted & self.setup.CALENDAR_TOOLS, expected)
            if role not in {'main', 'personal', 'travel'}:
                self.assertEqual(entry['tools']['allow'], self.legacy['agents']['entries'][role]['tools']['allow'])
            self.assertEqual('google-calendar' in entry['skills'], role == 'personal')
        for section in self.setup.PROTECTED:
            self.assertEqual(after.get(section), self.legacy.get(section))
        self.assertEqual(self.candidate(after, self.team), after)

    def test_missing_existing_roster_ceiling_cannot_be_recreated_by_upgrade_flag(self):
        for enabled in (False, True):
            before = copy.deepcopy(self.legacy); before['tools'].pop('subagents')
            with self.assertRaisesRegex(self.setup.TeamRefused, 'finite-child-tool-policy-required'):
                self.candidate(before, self.team, enable_calendar_upgrade=enabled)
        with self.assertRaisesRegex(self.setup.TeamRefused, 'reviewed-calendar-upgrade-required'):
            self.candidate(self.fixture.before, self.team)

    def test_upgrade_cannot_revive_legacy_global_or_child_revocations(self):
        name = next(iter(self.setup.SHOPPING_READ))
        for location in ('child_allow', 'child_deny', 'global_allow', 'global_deny'):
            before = copy.deepcopy(self.legacy)
            if location == 'child_allow': before['tools']['subagents']['tools']['allow'].remove(name)
            if location == 'child_deny': before['tools']['subagents']['tools']['deny'].append(name)
            if location == 'global_allow': before['tools']['alsoAllow'].remove(name)
            if location == 'global_deny': before['tools']['deny'].append(name)
            with self.assertRaises(self.setup.TeamRefused):
                self.candidate(before, self.team, enable_calendar_upgrade=True)

    def test_partial_calendar_or_later_revocations_are_never_an_upgrade(self):
        name = next(iter(self.setup.CALENDAR_TOOLS))
        for enabled in (False, True):
            for location in ('child_allow', 'child_deny', 'global_allow', 'global_deny', 'personal', 'main'):
                before = copy.deepcopy(self.installed)
                if location == 'child_allow': before['tools']['subagents']['tools']['allow'].remove(name)
                if location == 'child_deny': before['tools']['subagents']['tools']['deny'].append(name)
                if location == 'global_allow': before['tools']['alsoAllow'].remove(name)
                if location == 'global_deny': before['tools']['deny'].append(name)
                if location in {'personal', 'main'}: before['agents']['entries'][location]['tools']['allow'].remove(name)
                with self.assertRaises(self.setup.TeamRefused):
                    self.candidate(before, self.team, enable_calendar_upgrade=enabled)
        revoked = copy.deepcopy(self.installed)
        revoked['tools']['subagents']['tools']['allow'] = sorted(
            set(revoked['tools']['subagents']['tools']['allow']) - self.setup.CALENDAR_TOOLS)
        with self.assertRaises(self.setup.TeamRefused):
            self.candidate(revoked, self.team, enable_calendar_upgrade=True)

    def test_unknown_child_grant_and_calendar_denial_remain_fail_closed(self):
        for location in ('unknown', 'child', 'global', 'personal'):
            before = copy.deepcopy(self.legacy)
            if location == 'unknown': before['tools']['subagents']['tools']['allow'].append('arbitrary_calendar_admin')
            if location == 'child': before['tools']['subagents']['tools']['deny'] += list(self.setup.CALENDAR_TOOLS)
            if location == 'global': before['tools']['deny'] += list(self.setup.CALENDAR_TOOLS)
            if location == 'personal': before['agents']['entries']['personal']['tools']['deny'] += list(self.setup.CALENDAR_TOOLS)
            with self.assertRaises(self.setup.TeamRefused):
                self.candidate(before, self.team, enable_calendar_upgrade=True)

    def test_managed_calendar_skill_installs_only_for_luna_and_preserves_memory(self):
        self.fixture.config.write_text(json.dumps(self.legacy))
        (self.fixture.state / 'workspace/skills/google-calendar/SKILL.md').unlink()
        (self.fixture.state / 'workspace/skills/google-calendar').rmdir()
        source = self.fixture.skill_sources(('google-calendar',))
        memory = self.fixture.state / 'workspace/MEMORY.md'; original = memory.read_bytes()
        result = self.setup.setup(self.fixture.config, self.team, skills_source=source,
                                  enable_calendar_upgrade=True, apply=True, runner=self.fixture.runner)
        self.assertTrue(result['applied'])
        ledger = json.loads((self.fixture.state / 'team-skill-ownership.json').read_text())
        self.assertEqual(set(ledger), {'workspace-personal/skills/google-calendar/SKILL.md'})
        for role in self.setup.ROSTER:
            path = Path(json.loads(self.fixture.config.read_text())['agents']['entries'][role]['workspace'])
            target = path / 'skills/google-calendar/SKILL.md'
            self.assertEqual(target.exists(), role == 'personal')
            if target.exists(): self.assertEqual(target.read_bytes(), (source / 'google-calendar/SKILL.md').read_bytes())
        self.assertEqual(memory.read_bytes(), original)
        again = self.setup.setup(self.fixture.config, self.team, skills_source=source,
                                 apply=True, runner=self.fixture.runner)
        self.assertFalse(again['config_changed']); self.assertEqual(again['workspace_files'], 0)

    def test_isolated_native_helper_bundle_requires_reviewed_sibling_policy_module(self):
        with tempfile.TemporaryDirectory() as directory:
            bundle = Path(directory)
            for name in ('setup-openclaw-team.py', 'openclaw_team_policy.py', 'openclaw_integration_policy.py',
                         'openclaw_access_policy.py', 'openclaw_workflow_skills.py'):
                shutil.copyfile(ROOT / 'scripts' / name, bundle / name)
            command = [sys.executable, '-I', str(bundle / 'setup-openclaw-team.py'), '--help']
            ready = subprocess.run(command, capture_output=True, text=True, timeout=10)
            self.assertEqual(ready.returncode, 0, ready.stderr)
            self.assertIn('--enable-calendar-upgrade', ready.stdout)
            for name in ('openclaw_team_policy.py', 'openclaw_integration_policy.py',
                         'openclaw_access_policy.py', 'openclaw_workflow_skills.py'):
                (bundle / name).unlink()
                refused = subprocess.run(command, capture_output=True, text=True, timeout=10)
                self.assertNotEqual(refused.returncode, 0)
                shutil.copyfile(ROOT / 'scripts' / name, bundle / name)

    def test_travel_can_research_and_read_calendar_but_cannot_write_or_operate_home(self):
        policy = self.installed['agents']['entries']['travel']['tools']
        permitted = set(policy['allow']) - set(policy['deny'])
        self.assertIn('browser', permitted)
        self.assertIn('browser-automation', self.installed['agents']['entries']['travel']['skills'])
        self.assertEqual(permitted & self.setup.SERVICE_TOOLS,
                         self.setup.WEB_SEARCH | self.setup.CALENDAR_READ)
        forbidden = (self.setup.CALENDAR_TOOLS - self.setup.CALENDAR_READ
                     | self.setup.HOUSEHOLD_TOOLS | self.setup.SCHOOL_READ
                     | self.setup.ENGINEERING | self.setup.OPERATIONS | {'exec'})
        self.assertTrue(forbidden <= set(policy['deny']))
        self.assertFalse(permitted & forbidden)
        self.assertEqual(self.installed['agents']['entries']['travel']['subagents']['allowAgents'], [])

    def test_retrieval_upgrade_is_explicit_role_scoped_and_preserves_local_memory(self):
        legacy = copy.deepcopy(self.installed)
        retrieval = self.setup.RETRIEVAL_TOOLS
        for entry in legacy['agents']['entries'].values():
            for key in ('allow', 'deny'):
                entry['tools'][key] = sorted(set(entry['tools'][key]) - retrieval)
        legacy['tools']['alsoAllow'] = sorted(set(legacy['tools']['alsoAllow']) - retrieval)
        legacy['tools']['subagents']['tools']['allow'] = sorted(
            set(legacy['tools']['subagents']['tools']['allow']) - retrieval)
        with self.assertRaisesRegex(self.setup.TeamRefused, 'reviewed-retrieval-upgrade-required'):
            self.candidate(legacy, self.team)
        after = self.candidate(legacy, self.team, enable_retrieval_upgrade=True)
        self.assertEqual(after, self.installed)
        expected = {'main': retrieval, 'personal': {self.setup.MCP_PREFIX + 'school_search'},
                    'researcher': {self.setup.MCP_PREFIX + 'research_search'},
                    'operator': {self.setup.MCP_PREFIX + 'operations_search'},
                    'travel': {self.setup.MCP_PREFIX + 'research_search'},
                    'cook': {self.setup.MCP_PREFIX + 'research_search'},
                    'administrator': {self.setup.MCP_PREFIX + 'research_search'},
                    'finance': {self.setup.MCP_PREFIX + 'research_search'}}
        for role, entry in after['agents']['entries'].items():
            allowed = set(entry['tools']['allow']) - set(entry['tools']['deny'])
            self.assertEqual(allowed & retrieval, expected.get(role, set()))
            search = entry.get('memory', after['memory'])['search']
            self.assertEqual(search['provider'], 'openai-compatible')
            self.assertEqual(search['sources'], ['memory'])
            self.assertFalse(search['experimental']['sessionMemory'])
        for location in ('global', 'child', 'personal', 'denied'):
            revoked = copy.deepcopy(after)
            name = self.setup.MCP_PREFIX + 'school_search'
            if location == 'global': revoked['tools']['alsoAllow'].remove(name)
            elif location == 'child': revoked['tools']['subagents']['tools']['allow'].remove(name)
            elif location == 'personal': revoked['agents']['entries']['personal']['tools']['allow'].remove(name)
            else: revoked['tools']['deny'].append(name)
            with self.subTest(location=location), self.assertRaises(self.setup.TeamRefused):
                self.candidate(revoked, self.team, enable_retrieval_upgrade=True)

    def test_additive_travel_role_keeps_existing_specialist_permissions_and_native_state(self):
        before = copy.deepcopy(self.installed)
        before['agents']['entries'].pop('travel')
        before['agents']['entries']['main']['subagents']['allowAgents'].remove('travel')
        before['agents']['defaults']['subagents']['maxChildrenPerAgent'] = 6
        after = self.candidate(before, self.team)
        for role in set(self.setup.LEAVES) - {'travel'}:
            self.assertEqual(after['agents']['entries'][role], before['agents']['entries'][role])
        for section in self.setup.PROTECTED:
            self.assertEqual(after.get(section), before.get(section))
        self.assertEqual(self.candidate(after, self.team), after)

    def test_managed_travel_skill_installs_only_for_travel_without_touching_memory(self):
        self.fixture.config.write_text(json.dumps(self.installed))
        source = self.fixture.skill_sources(('travel-adventures',))
        memory = self.fixture.state / 'workspace/MEMORY.md'; original = memory.read_bytes()
        result = self.setup.setup(self.fixture.config, self.team, skills_source=source,
                                  apply=True, runner=self.fixture.runner)
        self.assertTrue(result['applied'])
        ledger = json.loads((self.fixture.state / 'team-skill-ownership.json').read_text())
        self.assertEqual(set(ledger), {'workspace-travel/skills/travel-adventures/SKILL.md'})
        for role, entry in self.installed['agents']['entries'].items():
            if role == 'main': continue  # Fixture contains a source copy, outside its skill allowlist.
            target = Path(entry['workspace']) / 'skills/travel-adventures/SKILL.md'
            self.assertEqual(target.exists(), role == 'travel')
        self.assertEqual(memory.read_bytes(), original)
        again = self.setup.setup(self.fixture.config, self.team, skills_source=source,
                                 apply=True, runner=self.fixture.runner)
        self.assertFalse(again['config_changed']); self.assertEqual(again['workspace_files'], 0)


if __name__ == '__main__':
    unittest.main()
