"""Native team planning, permissions, identity isolation, and mutation fencing."""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('team_setup', ROOT / 'scripts/setup-openclaw-team.py')
setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup)


def reviewed_candidate(*args, **kwargs):
    return setup.candidate(*args, enable_calendar_upgrade=True, enable_retrieval_upgrade=True, **kwargs)

def reviewed_setup(*args, **kwargs):
    return setup.setup(*args, enable_calendar_upgrade=True, enable_retrieval_upgrade=True, **kwargs)


class TeamSetupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.directory = Path(self.tmp.name).resolve()
        self.state = self.directory / 'state'
        self.state.mkdir(mode=0o700)
        self.root_patch = patch.object(setup, 'STATE_ROOT', self.state)
        self.root_patch.start()
        self.config = self.state / 'openclaw.json'
        self.team = setup.manifest(ROOT / 'config/openclaw-team.json')
        self.before = json.loads((ROOT / 'config/openclaw-nemoclaw.json').read_text())
        self.before['agents']['defaults']['workspace'] = str(self.state / 'workspace')
        self.before['agents']['defaults']['skipBootstrap'] = True
        self.before['agents']['entries']['main']['agentDir'] = str(self.state / 'agents/main/agent')
        self.before['agents']['entries']['main']['tools'] = {
            'allow': sorted((setup.WORKSPACE_TOOLS | setup.ENGINEERING | setup.OPERATIONS | {'browser', 'exec'}) - setup.RETRIEVAL_TOOLS),
            'deny': ['sessions_spawn', 'sessions_send', 'subagents', 'github_publish'],
        }
        self.before['tools'].pop('allow')
        self.before['tools']['profile'] = 'minimal'
        self.before['tools']['alsoAllow'] = sorted(setup.WORKSPACE_TOOLS | {'browser', 'exec', 'bundle-mcp'})
        self.before['tools']['deny'] = ['sessions_spawn', 'sessions_send', 'subagents', 'github_publish']
        self.before['browser'] = {'enabled': True, 'profiles': {'homecompute': {'cdpUrl': 'http://127.0.0.1/?token=synthetic-secret'}}}
        self.before['plugins']['entries']['homecompute-safety'] = {'enabled': True}
        self.before['mcp'] = {'servers': {'homecompute': {'headers': {'Authorization': 'Bearer synthetic-mcp-secret'}}}}
        self.config.write_text(json.dumps(self.before)); self.config.chmod(0o600)
        workspace = self.state / 'workspace'
        workspace.mkdir(mode=0o700)
        (workspace / 'AGENTS.md').write_text('Existing owner boundaries.\n')
        (workspace / 'MEMORY.md').write_text('Owner memory must survive unchanged.\n')
        for name in {name for agent in self.team['agents'].values() for name in agent['skills']}:
            skill = workspace / 'skills' / name
            skill.mkdir(parents=True, mode=0o700)
            (skill / 'SKILL.md').write_text('Trusted existing skill: ' + name)
        self.commands = []

    def tearDown(self):
        self.root_patch.stop()
        self.tmp.cleanup()

    def runner(self, argv, **kwargs):
        self.commands.append((argv, kwargs))
        if '--dry-run' not in argv:
            value = json.loads(self.config.read_text())
            for change in json.loads(argv[argv.index('--batch-json') + 1]):
                value[change['path']] = change['value']
            self.config.write_text(json.dumps(value))
        return subprocess.CompletedProcess(argv, 0, stdout='private native details', stderr='')

    def test_role_capabilities_are_finite_and_keep_separate_workspaces(self):
        before = copy.deepcopy(self.before)
        candidate = reviewed_candidate(before, self.team)
        self.assertEqual(before, self.before)
        self.assertEqual(set(candidate['agents']['entries']), set(setup.ROSTER))
        self.assertEqual(candidate['agents']['entries']['main']['agentDir'], before['agents']['entries']['main']['agentDir'])
        self.assertEqual(candidate['agents']['defaults']['workspace'], before['agents']['defaults']['workspace'])
        self.assertEqual(candidate['agents']['entries']['main']['workspace'], before['agents']['defaults']['workspace'])
        self.assertEqual(candidate['agents']['ownership'], 'explicit')
        self.assertEqual(candidate['agents']['defaults']['systemAgent'], {'agentId': 'main'})
        self.assertTrue(candidate['agents']['defaults']['skipBootstrap'])
        children = candidate['agents']['defaults']['subagents']
        self.assertEqual((children['maxConcurrent'], children['maxSpawnDepth'], children['runTimeoutSeconds']), (1, 1, 300))
        self.assertEqual(candidate['agents']['entries']['main']['subagents']['allowAgents'], list(setup.LEAVES))
        locations = set()
        for role in setup.LEAVES:
            entry = candidate['agents']['entries'][role]
            locations.add(entry['workspace']); locations.add(entry['agentDir'])
            self.assertEqual(entry['subagents']['allowAgents'], [])
            self.assertFalse(set(entry['tools']['allow']) & set(entry['tools']['deny']))
            self.assertTrue(setup.ORCHESTRATION <= set(entry['tools']['deny']))
            self.assertIn('exec', entry['tools']['deny'])
            self.assertEqual(entry['memory']['search']['extraPaths'], [])
        self.assertEqual(len(locations), 2 * len(setup.LEAVES))
        engineer = candidate['agents']['entries']['engineer']['tools']
        self.assertTrue(setup.ENGINEERING <= set(engineer['allow']))
        self.assertTrue(setup.OPERATIONS <= set(engineer['deny']))
        operator = candidate['agents']['entries']['operator']['tools']
        self.assertTrue(setup.OPERATIONS <= set(operator['allow']))
        self.assertTrue(setup.ENGINEERING <= set(operator['deny']))
        self.assertIn('sessions_send', candidate['tools']['deny'])
        self.assertTrue(setup.ORCHESTRATION <= set(candidate['tools']['alsoAllow']))
        for section in setup.PROTECTED:
            self.assertEqual(candidate.get(section), before.get(section))

    def test_quarantine_unqualified_capabilities_and_unknown_roster_are_refused(self):
        variants = []
        value = copy.deepcopy(self.before); value['tools']['deny'].append('*'); variants.append(value)
        value = copy.deepcopy(self.before); value['agents']['entries']['main']['tools']['allow'].remove('browser'); variants.append(value)
        value = copy.deepcopy(self.before); value['agents']['entries']['other'] = {}; variants.append(value)
        value = copy.deepcopy(self.before); value['tools']['fs']['workspaceOnly'] = False; variants.append(value)
        value = copy.deepcopy(self.before); value['plugins']['entries']['homecompute-safety']['enabled'] = False; variants.append(value)
        value = copy.deepcopy(self.before); value['models']['providers']['inference']['apiKey'] = 'real-key'; variants.append(value)
        for value in variants:
            with self.subTest(value=setup.digest(value)), self.assertRaises(setup.TeamRefused):
                reviewed_candidate(value, self.team)

    def test_service_reads_are_role_scoped_without_network_credentials_or_new_actions(self):
        candidate = reviewed_candidate(self.before, self.team)
        for role, expected in setup.ROLE_SERVICE_READ.items():
            tools = candidate['agents']['entries'][role]['tools']
            self.assertEqual(set(tools['allow']) & setup.SERVICE_READ, expected)
            if role != 'main':
                self.assertTrue(setup.SERVICE_READ - expected <= set(tools['deny']))
                self.assertIn('exec', tools['deny'])
        self.assertEqual(setup.ROLE_SERVICE_READ['personal'], setup.SCHOOL_READ | setup.SHOPPING_READ)
        self.assertEqual(setup.ROLE_SERVICE_READ['researcher'], setup.WEB_SEARCH)
        self.assertEqual(setup.ROLE_SERVICE_READ['operator'], set())
        self.assertTrue(setup.SERVICE_READ <= set(candidate['tools']['subagents']['tools']['allow']))
        self.assertEqual(candidate['mcp'], self.before['mcp'])
        self.assertEqual(candidate['plugins'], self.before['plugins'])
        self.assertEqual(candidate.get('proxy'), self.before.get('proxy'))
        self.assertEqual(candidate['tools']['deny'], [name for name in self.before['tools']['deny']
                                                     if name not in setup.ORCHESTRATION])

    def test_finite_global_allow_grants_new_tools_only_to_the_assigned_roles(self):
        before = copy.deepcopy(self.before)
        before['tools']['allow'] = list(before['agents']['entries']['main']['tools']['allow']) + ['bundle-mcp']
        before['tools'].pop('alsoAllow')
        candidate = reviewed_candidate(before, self.team)
        global_allow = set(candidate['tools']['allow'])
        global_deny = set(candidate['tools']['deny'])
        self.assertTrue(setup.SERVICE_READ <= global_allow)
        for role, expected in setup.ROLE_SERVICE_READ.items():
            policy = candidate['agents']['entries'][role]['tools']
            effective = set(policy['allow']) & global_allow - global_deny - set(policy['deny'])
            self.assertEqual(effective & setup.SERVICE_READ, expected)
        self.assertEqual(reviewed_candidate(candidate, self.team), candidate)

    def test_existing_explicit_service_denial_requires_policy_reconciliation(self):
        for location in ('global', 'main'):
            before = copy.deepcopy(self.before)
            policy = before['tools'] if location == 'global' else before['agents']['entries']['main']['tools']
            policy['deny'].append(next(iter(setup.WEB_SEARCH)))
            with self.assertRaisesRegex(setup.TeamRefused, '^(service|retrieval)-tool-policy-denied$'):
                reviewed_candidate(before, self.team)

    def test_channel_routing_map_is_validated_and_only_in_coordinator_identity(self):
        routes = self.team['channel_routes']
        for name, role in {'aula': 'personal', 'shopping': 'shopper', 'storage': 'keeper',
                           'home': 'operator', 'energy': 'operator', 'news': 'researcher',
                           'engineering': 'engineer', 'openclaw': 'operator', 'travel': 'travel'}.items():
            self.assertEqual([row['agent'] for row in routes.values() if row['name'] == name], [role])
        for role in setup.ROSTER:
            identity = setup.identity_text('Existing owner rules.', role, self.team)
            self.assertIn('Existing owner rules.', identity)
            for key in routes:
                self.assertEqual(key in identity, role == 'main')
        for bad in ({}, {'not-an-id': {'name': 'home', 'agent': 'operator'}},
                    {'a' * 26: {'name': 'home', 'agent': 'main'}},
                    {'a' * 26: {'name': 'forged\nheader', 'agent': 'operator'}}):
            value = copy.deepcopy(self.team); value['channel_routes'] = bad
            path = self.directory / 'bad-manifest.json'; path.write_text(json.dumps(value))
            with self.assertRaisesRegex(setup.TeamRefused, 'trusted-channel-routes-required'):
                setup.manifest(path)

    def test_isolation_limits_session_reads_preserving_own_child_delivery_and_unrelated_settings(self):
        before = reviewed_candidate(self.before, self.team)
        before['tools']['sessions'] = {'visibility': 'all'}
        before['session'] = {'threadBindings': {'enabled': False, 'defaultSpawnContext': 'fork'},
                             'reset': {'mode': 'none'}}
        after = reviewed_candidate(before, self.team)
        self.assertEqual(after['tools']['sessions'], {'visibility': 'tree'})
        self.assertEqual(after['session'], {'threadBindings': {'enabled': False, 'defaultSpawnContext': 'isolated'},
                                           'reset': {'mode': 'none'}})
        denied = {'sessions_list', 'sessions_history', 'sessions_search', 'sessions_send'}
        self.assertTrue(denied <= set(after['agents']['entries']['main']['tools']['deny']))
        self.assertTrue(setup.ORCHESTRATION <= set(after['agents']['entries']['main']['tools']['allow']))
        for role in setup.ROSTER:
            self.assertEqual(after['agents']['entries'][role]['tools']['allow'],
                             before['agents']['entries'][role]['tools']['allow'])
        self.assertEqual({row['path'] for row in setup.batch(before, after)}, {'tools', 'session'})
        self.assertEqual(reviewed_candidate(after, self.team), after)
        after['tools']['sessions']['visibility'] = 'self'
        self.assertEqual(reviewed_candidate(after, self.team)['tools']['sessions']['visibility'], 'self')

    def test_coordinator_keeps_inheritable_grants_while_execution_guard_owns_routing(self):
        before = reviewed_candidate(self.before, self.team)
        domain = setup.ENGINEERING | setup.OPERATIONS | setup.SERVICE_TOOLS | {'browser'}
        before['agents']['entries']['main']['tools']['deny'] = sorted(
            set(before['agents']['entries']['main']['tools']['deny']) | domain)
        after = reviewed_candidate(before, self.team)
        main = after['agents']['entries']['main']
        effective = set(main['tools']['allow']) - set(main['tools']['deny']) - set(after['tools']['deny'])
        self.assertTrue(domain <= effective)
        self.assertFalse(domain & set(main['tools']['deny']))
        self.assertTrue(setup.ORCHESTRATION | setup.WORKSPACE_TOOLS <= effective)
        self.assertEqual(main['subagents']['delegationMode'], 'suggest')
        self.assertEqual(after['tools']['deny'], before['tools']['deny'])
        for role in setup.LEAVES:
            self.assertEqual(after['agents']['entries'][role]['tools'], before['agents']['entries'][role]['tools'])
        self.assertEqual(reviewed_candidate(after, self.team), after)
        for denied in ('browser', next(iter(setup.OPERATIONS)), 'memory_get'):
            revoked = copy.deepcopy(after); revoked['tools']['deny'].append(denied)
            with self.assertRaises(setup.TeamRefused):
                reviewed_candidate(revoked, self.team)
        for allowed in (['*'], ['unreviewed_network_tool'], 'browser'):
            invalid = copy.deepcopy(after); invalid['tools']['subagents']['tools']['allow'] = allowed
            with self.assertRaises(setup.TeamRefused):
                reviewed_candidate(invalid, self.team)
        revoked = copy.deepcopy(after)
        revoked['tools']['subagents']['tools']['allow'].remove(next(iter(setup.SHOPPING_READ)))
        with self.assertRaisesRegex(setup.TeamRefused, 'revoked-child-capability-refused'):
            reviewed_candidate(revoked, self.team)
        revoked = copy.deepcopy(after); revoked['tools']['subagents']['tools']['deny'].append('browser')
        with self.assertRaises(setup.TeamRefused):
            reviewed_candidate(revoked, self.team)

    def test_compaction_keeps_context_in_thread_without_automatic_shared_memory_flush(self):
        before = copy.deepcopy(self.before)
        original = {'mode': 'safeguard', 'notifyUser': True, 'recentTurnsPreserve': 1,
                    'memoryFlush': {'enabled': True, 'softThresholdTokens': 4000}}
        before['agents']['defaults']['compaction'] = copy.deepcopy(original)
        after = reviewed_candidate(before, self.team)
        expected = copy.deepcopy(original); expected['memoryFlush']['enabled'] = False
        self.assertEqual(after['agents']['defaults']['compaction'], expected)
        self.assertEqual(before['agents']['defaults']['compaction'], original)
        self.assertTrue(setup.WORKSPACE_TOOLS <= set(after['agents']['entries']['main']['tools']['allow']))
        self.assertEqual(reviewed_candidate(after, self.team), after)

    def test_household_actions_and_skills_are_scoped_to_the_assigned_roles(self):
        before = copy.deepcopy(self.before)
        before['tools']['allow'] = list(before['agents']['entries']['main']['tools']['allow']) + ['bundle-mcp']
        before['tools'].pop('alsoAllow')
        before['agents']['entries']['main']['skills'] = ['owner-custom']
        candidate = reviewed_candidate(before, self.team)
        self.assertEqual(len(setup.HOUSEHOLD_TOOLS), 14)
        new = {'household-shopping', 'discount-shopping', 'household-storage'}
        global_allow = set(candidate['tools']['allow'])
        for role in setup.ROSTER:
            entry = candidate['agents']['entries'][role]
            effective = set(entry['tools']['allow']) & global_allow - set(entry['tools']['deny']) - set(candidate['tools']['deny'])
            expected = setup.ROLE_HOUSEHOLD_TOOLS[role]
            self.assertEqual(effective & setup.HOUSEHOLD_TOOLS, expected)
            expected_skills = new if role in {'main', 'personal'} else {
                'shopper': {'household-shopping', 'discount-shopping'}, 'keeper': {'household-storage'},
            }.get(role, set())
            self.assertEqual(set(entry['skills']) & new, expected_skills)
            if role != 'main':
                self.assertTrue(setup.HOUSEHOLD_TOOLS - expected <= set(entry['tools']['deny']))
        self.assertIn('owner-custom', candidate['agents']['entries']['main']['skills'])
        self.assertEqual(candidate['agents']['defaults']['skills'], before['agents']['defaults']['skills'])
        self.assertEqual(reviewed_candidate(candidate, self.team), candidate)
        for section in setup.PROTECTED:
            self.assertEqual(candidate.get(section), before.get(section))

    def skill_sources(self, names=None):
        source = self.directory / 'reviewed-skills'
        source.mkdir(mode=0o700, exist_ok=True)
        for name in names or ('household-shopping', 'discount-shopping', 'household-storage'):
            directory = source / name; directory.mkdir(mode=0o700)
            (directory / 'SKILL.md').write_bytes((ROOT / 'deploy/openclaw/skills' / name / 'SKILL.md').read_bytes())
        return source

    def test_reviewed_skills_install_in_isolated_workspaces_preserve_memory_and_update_idempotently(self):
        source = self.skill_sources()
        for name in ('household-shopping', 'discount-shopping', 'household-storage'):
            path = self.state / 'workspace/skills' / name
            (path / 'SKILL.md').unlink(); path.rmdir()
        custom = self.state / 'workspace/skills/owner-custom/SKILL.md'
        custom.parent.mkdir(); custom.write_text('Owner-custom instructions remain unchanged.')
        memory = self.state / 'workspace-personal/MEMORY.md'
        memory.parent.mkdir(mode=0o700); memory.write_bytes(b'Private confirmed household preferences.\n')
        retained = self.state / 'agents/personal/agent/openclaw-agent.sqlite'
        retained.parent.mkdir(parents=True, mode=0o700); retained.write_bytes(b'retained-agent-store\x00untouched')
        initial = {str(path): path.read_bytes() for path in self.state.rglob('*') if path.is_file()}
        dry = reviewed_setup(self.config, self.team, skills_source=source, runner=self.runner)
        self.assertEqual(initial, {str(path): path.read_bytes() for path in self.state.rglob('*') if path.is_file()})
        self.assertEqual(len(dry['managed_skill_sha256']), 3)
        first = reviewed_setup(self.config, self.team, skills_source=source, apply=True, runner=self.runner)
        for name in dry['managed_skill_sha256']:
            for workspace in ('workspace', 'workspace-personal'):
                target = self.state / workspace / 'skills' / name / 'SKILL.md'
                self.assertEqual(target.read_bytes(), (source / name / 'SKILL.md').read_bytes())
            self.assertFalse((self.state / 'workspace-operator/skills' / name).exists())
        self.assertEqual(custom.read_text(), 'Owner-custom instructions remain unchanged.')
        self.assertEqual((self.state / 'workspace/MEMORY.md').read_bytes(), initial[str(self.state / 'workspace/MEMORY.md')])
        self.assertEqual(memory.read_bytes(), initial[str(memory)])
        self.assertEqual(retained.read_bytes(), initial[str(retained)])
        ledger = self.state / 'team-skill-ownership.json'
        self.assertEqual(len(json.loads(ledger.read_text())), 9)
        self.assertEqual(ledger.stat().st_mode & 0o777, 0o600)
        again = reviewed_setup(self.config, self.team, skills_source=source, apply=True, runner=self.runner)
        self.assertFalse(again['config_changed']); self.assertEqual(again['workspace_files'], 0)
        updated = source / 'household-shopping/SKILL.md'
        updated.write_bytes(updated.read_bytes() + b'\nReviewed revision.\n')
        refresh = reviewed_setup(self.config, self.team, skills_source=source, apply=True, runner=self.runner)
        self.assertFalse(refresh['config_changed']); self.assertTrue(refresh['restart_required'])
        self.assertNotEqual(refresh['backup_directory'], first['backup_directory'])
        self.assertEqual((self.state / 'workspace-personal/skills/household-shopping/SKILL.md').read_bytes(), updated.read_bytes())
        again = reviewed_setup(self.config, self.team, skills_source=source, runner=self.runner)
        self.assertEqual(again['workspace_files'], 0)

    def test_seven_agent_migration_preserves_five_existing_toolsets_and_state(self):
        old = reviewed_candidate(self.before, self.team)
        existing = setup.ROSTER[:5]
        for role in ('shopper', 'keeper', 'travel'):
            old['agents']['entries'].pop(role)
        old['agents']['entries']['main']['subagents']['allowAgents'] = list(existing[1:])
        old['agents']['defaults']['subagents']['maxChildrenPerAgent'] = 4
        retained = {}
        for role in existing:
            entry = old['agents']['entries'][role]; entry['name'] = 'Previous ' + role
            for path, content in ((Path(entry['workspace']) / 'MEMORY.md', ('memory-' + role).encode()),
                                  (Path(entry['agentDir']) / 'openclaw-agent.sqlite', ('state-' + role).encode())):
                path.parent.mkdir(parents=True, mode=0o700, exist_ok=True); path.write_bytes(content)
                retained[path] = content
        self.config.write_text(json.dumps(old))
        reviewed_setup(self.config, self.team, apply=True, runner=self.runner)
        after = json.loads(self.config.read_text())
        for role in existing:
            for field in ('workspace', 'agentDir', 'tools'):
                self.assertEqual(after['agents']['entries'][role][field], old['agents']['entries'][role][field])
        self.assertTrue(all(path.read_bytes() == content for path, content in retained.items()))
        self.assertEqual([after['agents']['entries'][role]['name'] for role in setup.ROSTER],
                         ['Atlas', 'Luna', 'Nova', 'Apollo', 'Orion', 'Vega', 'Ceres', 'Lyra'])
        self.assertEqual(after['agents']['defaults']['maxConcurrent'], 1)
        self.assertEqual(after['agents']['defaults']['subagents']['maxChildrenPerAgent'], 7)
        for role, count in (('shopper', 12), ('keeper', 6)):
            entry = after['agents']['entries'][role]
            self.assertEqual(len(set(entry['tools']['allow']) & setup.SERVICE_TOOLS), count)
            self.assertFalse(set(entry['tools']['allow']) & (setup.SCHOOL_READ | setup.WEB_SEARCH | setup.ENGINEERING | setup.OPERATIONS | {'browser'}))
            self.assertEqual(entry['heartbeat']['every'], '0m')
        for section in setup.PROTECTED:
            self.assertEqual(after.get(section), old.get(section))

    def test_reviewed_source_hash_fences_each_stage(self):
        original = self.config.read_bytes()
        with self.assertRaisesRegex(setup.TeamRefused, '^reviewed-config-digest-mismatch$'):
            reviewed_setup(self.config, self.team, apply=True, expected_config_sha256='0' * 64, runner=self.runner)
        self.assertEqual(self.commands, []); self.assertEqual(self.config.read_bytes(), original)
        dry = reviewed_setup(self.config, self.team, runner=self.runner)
        self.assertEqual(dry['source_config_sha256'], hashlib.sha256(original).hexdigest())
        result = reviewed_setup(self.config, self.team, apply=True,
                             expected_config_sha256=dry['source_config_sha256'], runner=self.runner)
        self.assertTrue(result['applied'])

    def test_first_skill_refresh_requires_reviewed_previous_hash_and_backs_up_old_bytes(self):
        source = self.skill_sources(('homecompute-operations',))
        path = self.state / 'workspace/skills/homecompute-operations/SKILL.md'; old = path.read_bytes()
        with self.assertRaisesRegex(setup.TeamRefused, '^unreviewed-skill-overwrite-refused$'):
            reviewed_setup(self.config, self.team, skills_source=source, apply=True, runner=self.runner)
        self.assertEqual(path.read_bytes(), old)
        self.assertFalse((self.state / 'team-setup-backups').exists())
        result = reviewed_setup(self.config, self.team, skills_source=source,
                             expected_skills={'homecompute-operations': hashlib.sha256(old).hexdigest()},
                             apply=True, runner=self.runner)
        backup = Path(result['backup_directory']); paths = json.loads((backup / 'paths.json').read_text())
        self.assertEqual((backup / ('workspace-' + str(paths.index(str(path))))).read_bytes(), old)
        for role in ('workspace', 'workspace-engineer', 'workspace-operator'):
            self.assertEqual((self.state / role / 'skills/homecompute-operations/SKILL.md').read_bytes(),
                             (source / 'homecompute-operations/SKILL.md').read_bytes())

    def test_modified_managed_skill_or_untrusted_source_is_refused(self):
        source = self.skill_sources()
        for name in ('household-shopping', 'discount-shopping', 'household-storage'):
            path = self.state / 'workspace/skills' / name / 'SKILL.md'
            path.write_bytes((source / name / 'SKILL.md').read_bytes())
        reviewed_setup(self.config, self.team, skills_source=source, apply=True, runner=self.runner)
        target = self.state / 'workspace-personal/skills/household-shopping/SKILL.md'
        target.write_text('Owner-modified managed skill.')
        with self.assertRaisesRegex(setup.TeamRefused, '^unreviewed-skill-overwrite-refused$'):
            reviewed_setup(self.config, self.team, skills_source=source, apply=True, runner=self.runner)
        self.assertEqual(target.read_text(), 'Owner-modified managed skill.')
        for name in ('helper.py', 'arbitrary.json'):
            extra = source / 'discount-shopping' / name; extra.write_text('unexpected')
            with self.assertRaisesRegex(setup.TeamRefused, '^skill-source-refused$'):
                setup.reviewed_skill_sources(source)
            extra.unlink()
        path = source / 'discount-shopping/SKILL.md'; path.unlink(); path.symlink_to(self.config)
        with self.assertRaisesRegex(setup.TeamRefused, '^skill-source-refused$'):
            setup.reviewed_skill_sources(source)

    def test_dry_run_preserves_config_files_and_never_discloses_secrets(self):
        initial = {str(path): path.read_bytes() for path in self.state.rglob('*') if path.is_file()}
        result = reviewed_setup(self.config, self.team, runner=self.runner)
        after = {str(path): path.read_bytes() for path in self.state.rglob('*') if path.is_file()}
        self.assertEqual(initial, after)
        self.assertFalse(result['applied']); self.assertTrue(result['schema_validated'])
        self.assertEqual(len(self.commands), 1); self.assertIn('--dry-run', self.commands[0][0])
        self.assertNotIn('synthetic-secret', json.dumps(result))
        self.assertNotIn('synthetic-mcp-secret', json.dumps(result))
        self.assertNotIn('synthetic-secret', json.dumps(self.commands[0][0]))

    def test_apply_keeps_memory_backs_up_and_is_idempotent(self):
        raw = self.config.read_bytes()
        first = reviewed_setup(self.config, self.team, apply=True, runner=self.runner)
        candidate = json.loads(self.config.read_text())
        self.assertTrue(first['applied'])
        backup = Path(first['backup_directory'])
        self.assertEqual((backup / 'openclaw-before.json').read_bytes(), raw)
        self.assertEqual((backup / 'openclaw-before.json').stat().st_mode & 0o777, 0o600)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o700)
        self.assertEqual((self.state / 'workspace/MEMORY.md').read_text(), 'Owner memory must survive unchanged.\n')
        main_identity = (self.state / 'workspace/AGENTS.md').read_text()
        self.assertTrue(main_identity.startswith('Existing owner boundaries.\n'))
        for role in setup.ROSTER:
            workspace = self.state / ('workspace' if role == 'main' else 'workspace-' + role)
            identity = (workspace / 'AGENTS.md').read_text()
            self.assertEqual(identity.count(setup.START), 1)
            self.assertIn(self.team['agents'][role]['instructions'], identity)
        second = reviewed_setup(self.config, self.team, apply=True, runner=self.runner)
        self.assertFalse(second['config_changed']); self.assertEqual(second['workspace_files'], 0)
        self.assertEqual(candidate, json.loads(self.config.read_text()))
        self.assertEqual(main_identity, (self.state / 'workspace/AGENTS.md').read_text())
        self.assertEqual(len(self.commands), 2)

    def test_native_validation_failure_never_changes_workspace_or_config(self):
        original = self.config.read_bytes()
        def failed(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 1, '', 'contains a secret native diagnostic')
        with self.assertRaisesRegex(setup.TeamRefused, '^native-config-validation-refused$'):
            reviewed_setup(self.config, self.team, apply=True, runner=failed)
        self.assertEqual(original, self.config.read_bytes())
        self.assertFalse((self.state / 'workspace-personal').exists())
        self.assertFalse((self.state / 'team-setup-backups').exists())

    def test_changed_native_config_after_validation_is_fenced(self):
        def drift(argv, **kwargs):
            value = json.loads(self.config.read_text()); value['logging']['level'] = 'error'
            self.config.write_text(json.dumps(value))
            return subprocess.CompletedProcess(argv, 0, '', '')
        with self.assertRaisesRegex(setup.TeamRefused, '^native-config-changed-since-validation$'):
            reviewed_setup(self.config, self.team, apply=True, runner=drift)
        self.assertFalse((self.state / 'workspace-personal').exists())
        self.assertFalse((self.state / 'team-setup-backups').exists())

    def test_symlink_identity_or_skill_source_is_refused(self):
        identity = self.state / 'workspace/AGENTS.md'
        identity.unlink(); identity.symlink_to(self.state / 'workspace/MEMORY.md')
        with self.assertRaises(setup.TeamRefused):
            reviewed_setup(self.config, self.team, runner=self.runner)
        identity.unlink(); identity.write_text('Owner boundaries')
        source = self.state / 'workspace/skills/household-summary/unsafe'
        source.symlink_to(self.config)
        with self.assertRaises(setup.TeamRefused):
            reviewed_setup(self.config, self.team, runner=self.runner)

    def test_existing_specialist_workspace_is_never_repointed(self):
        before = reviewed_candidate(self.before, self.team)
        before['agents']['entries']['personal'].update(workspace='/private/other', agentDir='/private/other')
        with self.assertRaisesRegex(setup.TeamRefused, '^existing-specialist-location-refused$'):
            reviewed_candidate(before, self.team)

    def test_managed_group_writable_workspace_is_retained_but_backups_remain_private(self):
        workspace = self.state / 'workspace'
        workspace.chmod(0o770)
        result = reviewed_setup(self.config, self.team, apply=True, runner=self.runner)
        self.assertTrue(result['applied'])
        self.assertEqual(workspace.stat().st_mode & 0o777, 0o770)
        self.assertEqual(Path(result['backup_directory']).stat().st_mode & 0o777, 0o700)
        private = self.state / 'team-setup-backups/not-private'
        private.mkdir(); private.chmod(0o770)
        with self.assertRaisesRegex(setup.TeamRefused, '^private-directory-required$'):
            setup.private_directory(private)
        private.chmod(0o755)
        with self.assertRaisesRegex(setup.TeamRefused, '^private-directory-required$'):
            setup.private_directory(private)
        with patch.object(setup.os, 'getegid', return_value=workspace.stat().st_gid + 1):
            with self.assertRaisesRegex(setup.TeamRefused, '^private-directory-required$'):
                setup.private_directory(workspace)
        workspace.chmod(0o777)
        with self.assertRaisesRegex(setup.TeamRefused, '^private-directory-required$'):
            setup.private_directory(workspace)


if __name__ == '__main__':
    unittest.main()
