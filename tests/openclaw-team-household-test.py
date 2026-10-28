"""Household specialists, shared Family boundaries, and reviewed task/mail grants."""
import copy
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('household_team_fixtures', ROOT / 'tests/openclaw-team-test.py')
fixtures = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixtures)


class HouseholdTeamTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.TeamSetupTest()
        self.fixture.setUp()
        self.setup = fixtures.setup
        self.team = self.fixture.team
        self.installed = fixtures.reviewed_candidate(self.fixture.before, self.team)

    def candidate(self, *args, **kwargs):
        # Task tests retain the independently reviewed optional mail fixture.
        kwargs.setdefault("enable_mail_upgrade", True)
        return self.setup.candidate(*args, **kwargs)

    def tearDown(self):
        self.fixture.tearDown()

    def without_grant(self, names):
        before = copy.deepcopy(self.installed)
        for entry in before['agents']['entries'].values():
            for key in ('allow', 'deny'):
                entry['tools'][key] = sorted(set(entry['tools'][key]) - names)
        for key in ('allow', 'alsoAllow'):
            if key in before['tools']:
                before['tools'][key] = sorted(set(before['tools'][key]) - names)
        before['tools']['subagents']['tools']['allow'] = sorted(
            set(before['tools']['subagents']['tools']['allow']) - names)
        return before

    def test_first_task_and_mail_grants_require_their_own_review_flags(self):
        for names, flag, label in (
                (self.setup.HOUSEHOLD_TASK_TOOLS, 'enable_household_task_upgrade', 'household-task'),
                (self.setup.FAMILY_TASK_TOOLS, 'enable_family_task_upgrade', 'family-task')):
            with self.subTest(label=label):
                before = self.without_grant(names)
                with self.assertRaisesRegex(self.setup.TeamRefused, 'reviewed-' + label + '-upgrade-required'):
                    self.candidate(before, self.team)
                after = self.candidate(before, self.team, **{flag: True})
                self.assertEqual(after, self.installed)
                self.assertEqual(self.candidate(after, self.team), after)
                for section in self.setup.PROTECTED:
                    self.assertEqual(after.get(section), before.get(section))

    def test_partial_or_explicitly_revoked_task_and_mail_grants_are_never_restored(self):
        for names, flag, roles in (
                (self.setup.HOUSEHOLD_TASK_TOOLS, 'enable_household_task_upgrade', ('main', 'personal', 'operator')),
                (self.setup.FAMILY_TASK_TOOLS, 'enable_family_task_upgrade', ('main', 'personal', 'operator', 'family')),
                (self.setup.MAIL_TOOLS, 'enable_mail_upgrade', ('main', 'personal'))):
            name = sorted(names)[0]
            for location in ('global_allow', 'global_deny', 'child_allow', 'child_deny', *roles):
                for enabled in (False, True):
                    with self.subTest(flag=flag, location=location, enabled=enabled):
                        before = copy.deepcopy(self.installed)
                        if location == 'global_allow': before['tools']['alsoAllow'].remove(name)
                        elif location == 'global_deny': before['tools']['deny'].append(name)
                        elif location == 'child_allow': before['tools']['subagents']['tools']['allow'].remove(name)
                        elif location == 'child_deny': before['tools']['subagents']['tools']['deny'].append(name)
                        else: before['agents']['entries'][location]['tools']['allow'].remove(name)
                        if flag == 'enable_mail_upgrade' and not enabled:
                            after = self.candidate(before, self.team, **{flag: enabled})
                            self.assertFalse(set(after['agents']['entries']['personal']['tools']['allow']) & names)
                        else:
                            with self.assertRaises(self.setup.TeamRefused):
                                self.candidate(before, self.team, **{flag: enabled})

    def test_task_and_mail_tools_are_scoped_to_their_assigned_roles(self):
        for role, entry in self.installed['agents']['entries'].items():
            allowed = set(entry['tools']['allow']) - set(entry['tools']['deny'])
            self.assertEqual(allowed & self.setup.HOUSEHOLD_TASK_TOOLS,
                             self.setup.HOUSEHOLD_TASK_TOOLS if role in {'main', 'personal', 'operator'} else set())
            self.assertEqual(allowed & self.setup.FAMILY_TASK_TOOLS,
                             self.setup.FAMILY_TASK_TOOLS if role in {'main', 'personal', 'operator', 'family'} else set())
            self.assertEqual(allowed & self.setup.MAIL_TOOLS,
                             self.setup.MAIL_TOOLS if role in {'main', 'personal'} else set())

    def test_family_review_never_authorizes_a_missing_private_task_or_mail_grant(self):
        for private_names, label in ((self.setup.HOUSEHOLD_TASK_TOOLS, 'household-task'),):
            before = self.without_grant(private_names)
            for entry in before['agents']['entries'].values():
                for key in ('allow', 'deny'):
                    entry['tools'][key] = sorted(set(entry['tools'][key]) - self.setup.FAMILY_TASK_TOOLS)
            for key in ('allow', 'alsoAllow'):
                if key in before['tools']:
                    before['tools'][key] = sorted(set(before['tools'][key]) - self.setup.FAMILY_TASK_TOOLS)
            before['tools']['subagents']['tools']['allow'] = sorted(
                set(before['tools']['subagents']['tools']['allow']) - self.setup.FAMILY_TASK_TOOLS)
            with self.subTest(label=label), self.assertRaisesRegex(
                    self.setup.TeamRefused, 'reviewed-' + label + '-upgrade-required'):
                self.candidate(before, self.team, enable_family_task_upgrade=True)

    def test_family_only_upgrade_preserves_extra_providers_and_all_unrelated_configuration(self):
        before = self.without_grant(self.setup.FAMILY_TASK_TOOLS)
        before['models']['providers']['approved-secondary'] = {'baseUrl': 'https://approved.invalid/v1'}
        before['session'] = {'reset': {'mode': 'none'}, 'threadBindings': {'defaultSpawnContext': 'fork'}}
        before['agents']['defaults']['maxConcurrent'] = 7
        # Family-only review must not require or add private task/mail capabilities.
        for names in (self.setup.HOUSEHOLD_TASK_TOOLS, self.setup.MAIL_TOOLS):
            for entry in before['agents']['entries'].values():
                entry['tools']['allow'] = sorted(set(entry['tools']['allow']) - names)
            before['tools']['alsoAllow'] = sorted(set(before['tools']['alsoAllow']) - names)
            before['tools']['subagents']['tools']['allow'] = sorted(
                set(before['tools']['subagents']['tools']['allow']) - names)
        original = copy.deepcopy(before)
        with self.assertRaisesRegex(self.setup.TeamRefused, 'reviewed-family-task-upgrade-required'):
            self.setup.family_tasks_candidate(before, self.team)
        after = self.setup.family_tasks_candidate(before, self.team, enable_family_task_upgrade=True)
        self.assertEqual(before, original)
        self.assertEqual(self.setup.family_tasks_candidate(after, self.team), after)
        for key in set(before) - {'agents', 'tools'}:
            self.assertEqual(after[key], before[key])
        self.assertEqual(after['agents']['defaults'], before['agents']['defaults'])
        for role, entry in after['agents']['entries'].items():
            expected = role in {'main', 'personal', 'operator', 'family'}
            self.assertEqual(set(entry['tools']['allow']) & self.setup.FAMILY_TASK_TOOLS,
                             self.setup.FAMILY_TASK_TOOLS if expected else set())
            self.assertEqual({key: value for key, value in entry.items() if key != 'tools'},
                             {key: value for key, value in before['agents']['entries'][role].items() if key != 'tools'})
            if not expected:
                self.assertTrue(self.setup.FAMILY_TASK_TOOLS <= set(entry['tools']['deny']))
            self.assertEqual(set(entry['tools']['allow']) - self.setup.FAMILY_TASK_TOOLS,
                             set(before['agents']['entries'][role]['tools']['allow']))

    def test_family_only_upgrade_rejects_partial_grants_and_revocations(self):
        name = sorted(self.setup.FAMILY_TASK_TOOLS)[0]
        for location in ('global_allow', 'global_deny', 'child_allow', 'child_deny',
                         'main', 'personal', 'operator', 'family'):
            for enabled in (False, True):
                with self.subTest(location=location, enabled=enabled):
                    before = copy.deepcopy(self.installed)
                    if location == 'global_allow': before['tools']['alsoAllow'].remove(name)
                    elif location == 'global_deny': before['tools']['deny'].append(name)
                    elif location == 'child_allow': before['tools']['subagents']['tools']['allow'].remove(name)
                    elif location == 'child_deny': before['tools']['subagents']['tools']['deny'].append(name)
                    else: before['agents']['entries'][location]['tools']['allow'].remove(name)
                    with self.assertRaises(self.setup.TeamRefused):
                        self.setup.family_tasks_candidate(before, self.team, enable_family_task_upgrade=enabled)

    def test_family_only_setup_updates_only_four_identity_blocks_and_retains_native_settings(self):
        before = self.without_grant(self.setup.FAMILY_TASK_TOOLS)
        before['models']['providers']['approved-secondary'] = {'baseUrl': 'https://approved.invalid/v1'}
        self.fixture.config.write_text(json.dumps(before))
        unrelated = self.fixture.state / 'workspace-researcher' / 'AGENTS.md'
        unrelated.parent.mkdir(mode=0o700)
        unrelated.write_text('Custom researcher identity must remain byte-for-byte.\n')
        original = unrelated.read_bytes()
        result = self.setup.setup(self.fixture.config, self.team, family_tasks_only=True,
                                  enable_family_task_upgrade=True, apply=True, runner=self.fixture.runner)
        self.assertEqual(result['agents'], ['main', 'personal', 'operator', 'family'])
        self.assertEqual(result['workspace_files'], 4)
        self.assertEqual(unrelated.read_bytes(), original)
        self.assertFalse((self.fixture.state / 'workspace-travel').exists())
        expected = self.setup.family_tasks_candidate(before, self.team, enable_family_task_upgrade=True)
        self.assertEqual(json.loads(self.fixture.config.read_text()), expected)
        again = self.setup.setup(self.fixture.config, self.team, family_tasks_only=True,
                                 apply=True, runner=self.fixture.runner)
        self.assertEqual(again['workspace_files'], 0)
        self.assertFalse(again['config_changed'])
        with self.assertRaisesRegex(self.setup.TeamRefused, 'family-only-unrelated-upgrade-refused'):
            self.setup.setup(self.fixture.config, self.team, family_tasks_only=True, enable_mail_upgrade=True,
                             runner=self.fixture.runner)

    def test_cook_reads_household_evidence_without_write_or_private_source_access(self):
        tools = self.installed['agents']['entries']['cook']['tools']
        allowed = set(tools['allow']) - set(tools['deny'])
        read = {self.setup.MCP_PREFIX + name for name in ('shopping_list', 'discounts_read', 'storage_list')}
        self.assertTrue(read | self.setup.SHOPPING_READ | self.setup.WEB_SEARCH | {'browser'} <= allowed)
        forbidden = self.setup.HOUSEHOLD_TOOLS - read | self.setup.SCHOOL_READ | self.setup.MAIL_TOOLS
        self.assertTrue(forbidden <= set(tools['deny']))
        self.assertFalse(allowed & forbidden)

    def test_gaia_is_separate_from_atlas_delegation_and_private_adult_sources(self):
        entry = self.installed['agents']['entries']['family']
        allowed = set(entry['tools']['allow']) - set(entry['tools']['deny'])
        forbidden = (self.setup.SCHOOL_READ | self.setup.CALENDAR_TOOLS | self.setup.MAIL_TOOLS
                     | self.setup.RETRIEVAL_TOOLS | self.setup.ENGINEERING | self.setup.OPERATIONS
                     | self.setup.HOUSEHOLD_TASK_TOOLS | {'browser'} | self.setup.ORCHESTRATION)
        self.assertTrue(forbidden <= set(entry['tools']['deny']))
        self.assertFalse(allowed & forbidden)
        self.assertTrue(self.setup.HOUSEHOLD_TOOLS | self.setup.FAMILY_TASK_TOOLS <= allowed)
        self.assertEqual(entry['subagents']['allowAgents'], [])
        self.assertNotIn('family', self.installed['agents']['entries']['main']['subagents']['allowAgents'])
        self.assertEqual(entry['memory']['search']['extraPaths'], [])
        self.assertEqual(entry['memory']['search']['sources'], ['memory'])
        self.assertFalse(entry['memory']['search']['experimental']['sessionMemory'])
        identity = self.setup.identity_text('', 'family', self.team)
        self.assertNotIn('Atlas answers', identity)
        self.assertNotIn('sessions_spawn', identity)
        self.assertIn('direct shared Family contact', identity)

    def test_existing_specialist_revocations_cannot_be_erased_on_reapply(self):
        for role, name in (('cook', self.setup.MCP_PREFIX + 'storage_list'),
                           ('administrator', 'browser'), ('finance', 'memory_get')):
            for location in ('allow', 'deny'):
                with self.subTest(role=role, location=location):
                    before = copy.deepcopy(self.installed)
                    if location == 'allow': before['agents']['entries'][role]['tools']['allow'].remove(name)
                    else: before['agents']['entries'][role]['tools']['deny'].append(name)
                    with self.assertRaisesRegex(self.setup.TeamRefused, 'existing-specialist-capability-revoked'):
                        self.candidate(before, self.team)


if __name__ == '__main__':
    unittest.main()
