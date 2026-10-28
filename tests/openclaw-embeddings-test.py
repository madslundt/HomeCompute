"""Memory provider migration keeps private corpora and rebuilds incompatible indexes."""
import copy
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('memory_setup', ROOT / 'scripts/setup-openclaw-embeddings.py')
memory = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(memory)


class MemoryMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.root.chmod(0o700)
        self.config = self.root / 'openclaw.json'
        self.before = json.loads((ROOT / 'config/openclaw-nemoclaw.json').read_text())
        self.before['mcp']['servers']['homecompute'] = {
            'transport': 'streamable-http', 'url': memory.ORIGIN + '/mcp',
            'headers': {'Authorization': 'Bearer synthetic-local-memory-secret'}}
        self.before['agents']['defaults']['workspace'] = str(self.root / 'workspace')
        self.before['agents']['entries']['personal'] = {
            'workspace': str(self.root / 'workspace-personal'),
            'memory': {'search': {'provider': 'none', 'store': {'vector': {'enabled': False}}}}}
        self.config.write_text(json.dumps(self.before))
        self.config.chmod(0o600)
        self.commands = []

    def tearDown(self):
        self.temp.cleanup()

    def runner(self, arguments, **kwargs):
        self.commands.append((arguments, kwargs))
        return subprocess.CompletedProcess(arguments, 0, stdout='private diagnostic', stderr='')

    def test_local_provider_and_agent_isolation_without_other_config_changes(self):
        after, agents = memory.candidate(self.before, self.root)
        self.assertEqual(agents, ['main', 'personal'])
        self.assertEqual(after['mcp'], self.before['mcp'])
        self.assertEqual(after['models'], self.before['models'])
        search = after['memory']['search']
        self.assertEqual(search['remote']['baseUrl'], memory.ORIGIN + '/v1')
        self.assertEqual(search['remote']['apiKey'], 'synthetic-local-memory-secret')
        self.assertEqual(search['queryInputType'], 'query')
        self.assertEqual(search['documentInputType'], 'document')
        self.assertEqual(search['fallback'], 'none')
        self.assertEqual(search['outputDimensionality'], 1024)
        for entry in after['agents']['entries'].values():
            agent_search = entry['memory']['search']
            self.assertEqual(agent_search['sources'], ['memory'])
            self.assertEqual(agent_search['extraPaths'], [])
            self.assertFalse(agent_search['experimental']['sessionMemory'])
            self.assertTrue(agent_search['store']['vector']['enabled'])
        self.assertEqual(self.before['agents']['entries']['personal']['memory']['search']['provider'], 'none')

    def test_untrusted_endpoint_or_cross_workspace_is_refused(self):
        for mutate in (
            lambda value: value['mcp']['servers']['homecompute'].update(url='https://example.com/mcp'),
            lambda value: value['mcp']['servers']['homecompute'].update(url=memory.ORIGIN + '/mcp?x=y'),
            lambda value: value['mcp']['servers']['homecompute']['headers'].update(Authorization='Bearer bad\nkey'),
            lambda value: value['agents']['entries']['personal'].update(workspace=str(self.root / 'workspace')),
        ):
            before = copy.deepcopy(self.before)
            mutate(before)
            with self.assertRaises(memory.Refused):
                memory.candidate(before, self.root)

    def test_managed_embedding_credentials_follow_the_current_openshell_environment(self):
        expected = {'source': 'env', 'provider': 'default',
                    'id': 'HOMECOMPUTE_OPENCLAW_MCP_TOKEN'}
        for revision in ('v123_', 'v456_', 's' + 'a' * 64 + '_', ''):
            before = copy.deepcopy(self.before)
            before['mcp']['servers']['homecompute']['headers']['Authorization'] = (
                'Bearer openshell:resolve:env:' + revision + 'HOMECOMPUTE_OPENCLAW_MCP_TOKEN')
            after, _ = memory.candidate(before, self.root)
            self.assertEqual(after['memory']['search']['remote']['apiKey'], expected)
            for entry in after['agents']['entries'].values():
                self.assertEqual(entry['memory']['search']['remote']['apiKey'], expected)
            self.assertEqual(after['mcp'], before['mcp'])

    def test_managed_embedding_credentials_reject_an_unrelated_resolver_key(self):
        self.before['mcp']['servers']['homecompute']['headers']['Authorization'] = (
            'Bearer openshell:resolve:env:v123_OTHER_TOKEN')
        with self.assertRaises(memory.Refused):
            memory.candidate(self.before, self.root)

    def test_preview_only_validates_private_candidate_and_does_not_mutate(self):
        original = self.config.read_bytes()
        report = memory.setup(self.config, runner=self.runner)
        self.assertFalse(report['applied'])
        self.assertTrue(report['reindex_required'])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(len(list(self.root.iterdir())), 1)
        self.assertEqual(self.commands[0][0], ['openclaw', 'config', 'validate', '--json'])
        self.assertNotIn('synthetic-local-memory-secret', json.dumps(self.commands))

    def test_apply_backs_up_config_and_sqlite_and_reindexes_each_agent_once(self):
        database_root = self.root / 'memory'
        database_root.mkdir(mode=0o700)
        database_root.chmod(0o2770)
        database = database_root / 'main.sqlite'
        connection = sqlite3.connect(database)
        connection.execute('CREATE TABLE notes(text)')
        connection.execute('INSERT INTO notes VALUES(?)', ('confirmed preference',))
        connection.commit(); connection.close(); database.chmod(0o600)
        agent_root = self.root / 'agents/main/agent'
        agent_root.mkdir(mode=0o700, parents=True)
        agent_database = agent_root / 'openclaw-agent.sqlite'
        connection = sqlite3.connect(agent_database)
        connection.execute('CREATE TABLE chunks(text)')
        connection.execute('INSERT INTO chunks VALUES(?)', ('original memory index',))
        connection.commit(); connection.close(); agent_database.chmod(0o600)
        canonical_root = self.root / 'state'
        canonical_root.mkdir(mode=0o700)
        canonical = canonical_root / 'openclaw.sqlite'
        connection = sqlite3.connect(canonical)
        connection.execute('CREATE TABLE session(id)')
        connection.execute('INSERT INTO session VALUES(?)', ('preserve-session',))
        connection.commit(); connection.close(); canonical.chmod(0o600)
        original = self.config.read_bytes()
        first = memory.setup(self.config, apply=True, runner=self.runner)
        backup = Path(first['backup_directory'])
        self.assertEqual((backup / 'openclaw-before.json').read_bytes(), original)
        saved = sqlite3.connect(backup / 'main.sqlite')
        try:
            self.assertEqual(saved.execute('SELECT text FROM notes').fetchone()[0], 'confirmed preference')
        finally:
            saved.close()
        canonical_saved = sqlite3.connect(backup / 'openclaw-state-before.sqlite')
        try:
            self.assertEqual(canonical_saved.execute('SELECT id FROM session').fetchone()[0], 'preserve-session')
        finally:
            canonical_saved.close()
        agent_saved = sqlite3.connect(backup / 'agent-main-before.sqlite')
        try:
            self.assertEqual(agent_saved.execute('SELECT text FROM chunks').fetchone()[0], 'original memory index')
        finally:
            agent_saved.close()
        indexes = [args for args, _ in self.commands if 'index' in args]
        self.assertEqual(indexes, [['openclaw', 'memory', 'index', '--force', '--agent', agent]
                                   for agent in ['main', 'personal']])
        self.commands.clear()
        second = memory.setup(self.config, apply=True, runner=self.runner)
        self.assertFalse(second['reindexed'])
        self.assertFalse(second['config_changed'])
        self.assertEqual(len(self.commands), 1)

    def test_native_validation_preserves_managed_node_preloads(self):
        from unittest.mock import patch
        with patch.dict(os.environ, {'NODE_OPTIONS': '--require /tmp/native-preload.js'}):
            memory.setup(self.config, runner=self.runner)
        options = self.commands[0][1]['env']['NODE_OPTIONS']
        self.assertIn('--require /tmp/native-preload.js', options)
        self.assertIn('--max-old-space-size=384', options)

    def test_network_upgrade_preserves_every_other_endpoint_and_scopes_post_to_node(self):
        before = {'version': 1, 'filesystem_policy': {'read_only': ['/usr']},
                  'network_policies': {'existing': {'retain': True}, 'mcp_bridge_homecompute': {
                      'endpoints': [{'host': 'homecompute-capabilities.home.arpa', 'port': 19445,
                                     'path': '/mcp', 'allowed_ips': ['10.77.20.1'],
                                     'protocol': 'mcp', 'enforcement': 'enforce',
                                     'credential_binding': {'provider': 'agent-openclaw-mcp-homecompute'}}]}}}
        after = memory.network_candidate(before)
        self.assertEqual(after['filesystem_policy'], before['filesystem_policy'])
        for name, entry in before['network_policies'].items():
            self.assertEqual(after['network_policies'][name], entry)
        route = after['network_policies']['homecompute_embeddings']
        self.assertEqual(route['binaries'], [{'path': '/usr/local/bin/node'}])
        self.assertEqual(route['endpoints'][0]['path'], '/v1/embeddings')
        self.assertEqual(route['endpoints'][0]['rules'], [{'allow': {'method': 'POST', 'path': '/v1/embeddings'}}])
        self.assertEqual(memory.network_candidate(after), after)
        drift = copy.deepcopy(after); drift['network_policies']['homecompute_embeddings']['endpoints'][0]['path'] = '/**'
        with self.assertRaises(memory.Refused):
            memory.network_candidate(drift)
        drift = copy.deepcopy(before); drift['network_policies']['mcp_bridge_homecompute']['endpoints'][0]['allowed_ips'] = ['0.0.0.0/0']
        with self.assertRaises(memory.Refused):
            memory.network_candidate(drift)

    def test_failed_reindex_leaves_pending_marker_and_retry_rebuilds(self):
        def failure(arguments, **kwargs):
            self.commands.append((arguments, kwargs))
            return subprocess.CompletedProcess(arguments, 1 if 'index' in arguments else 0)
        with self.assertRaises(memory.Refused):
            memory.setup(self.config, apply=True, runner=failure)
        self.assertFalse((self.root / '.homecompute-memory-embedding-identity').exists())
        report = memory.setup(self.config, apply=True, runner=self.runner)
        self.assertTrue(report['reindexed'])


if __name__ == '__main__':
    unittest.main()
